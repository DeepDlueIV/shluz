"""Локальная история Telegram. Пользователи, тарифы и платежи остаются в Shluz."""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

METRICS_SQL = (
    "SELECT model,status,COUNT(*) AS requests,"
    "SUM(CASE WHEN feedback=1 THEN 1 ELSE 0 END) AS positive,"
    "SUM(CASE WHEN feedback=-1 THEN 1 ELSE 0 END) AS negative "
    "FROM turns GROUP BY model,status ORDER BY model,status"
)


class DialogLimit(ValueError):
    pass


class Store:
    def __init__(self, path: Path, *, max_dialogs: int = 20, max_saved_turns: int = 200) -> None:
        self.path = Path(path)
        self.max_dialogs = max_dialogs
        self.max_saved_turns = max_saved_turns
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("База Telegram создана более новой версией")
            db.execute("PRAGMA journal_mode=WAL")
            if version == 0:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS profiles (
                        user_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                        model TEXT, style TEXT NOT NULL DEFAULT 'balanced', active_dialog TEXT,
                        account_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS dialogs (
                        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES profiles(user_id),
                        title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_dialogs_user ON dialogs(user_id, updated_at);
                    CREATE TABLE IF NOT EXISTS turns (
                        id TEXT PRIMARY KEY, update_id INTEGER NOT NULL UNIQUE,
                        dialog_id TEXT NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
                        model TEXT NOT NULL, user_content TEXT NOT NULL, assistant_content TEXT,
                        status TEXT NOT NULL
                            CHECK(status IN ('processing','complete','failed','unknown')),
                        created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS ix_turns_dialog ON turns(dialog_id, created_at);
                    CREATE TABLE IF NOT EXISTS processed_updates (
                        update_id INTEGER PRIMARY KEY, created_at REAL NOT NULL
                    );
                    PRAGMA user_version=1;
                """)
                version = 1
            if version == 1:
                db.execute("BEGIN IMMEDIATE")
                db.execute("ALTER TABLE dialogs ADD COLUMN title_custom INTEGER NOT NULL DEFAULT 0")
                db.execute(
                    "ALTER TABLE turns ADD COLUMN replaces_turn_id TEXT "
                    "REFERENCES turns(id) ON DELETE SET NULL"
                )
                db.execute("ALTER TABLE turns ADD COLUMN feedback INTEGER CHECK(feedback IN(-1,1))")
                db.execute("CREATE INDEX ix_turns_replaces ON turns(replaces_turn_id)")
                db.execute("PRAGMA user_version=2")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def ensure_user(self, user_id: int, name: str) -> dict:
        now = time.time()
        with self.connection() as db:
            existing = db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
            if existing is None:
                dialog_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO profiles(user_id,name,active_dialog,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (user_id, name[:200], dialog_id, now, now),
                )
                db.execute(
                    "INSERT INTO dialogs(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (dialog_id, user_id, "Новый диалог", now, now),
                )
            else:
                db.execute(
                    "UPDATE profiles SET name=?,updated_at=? WHERE user_id=?",
                    (name[:200], now, user_id),
                )
        return self.profile(user_id)

    def profile(self, user_id: int) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise LookupError("Unknown Telegram profile")
            return dict(row)

    def bind_account(self, user_id: int, account_id: str) -> None:
        with self.connection() as db:
            row = db.execute(
                "SELECT account_id FROM profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            if row is None or row[0] not in {None, account_id}:
                raise ValueError("Account binding mismatch")
            db.execute("UPDATE profiles SET account_id=? WHERE user_id=?", (account_id, user_id))

    def set_model(self, user_id: int, model: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE profiles SET model=? WHERE user_id=?", (model, user_id))

    def set_style(self, user_id: int, style: str) -> None:
        if style not in {"balanced", "short", "detailed"}:
            raise ValueError("Unknown response style")
        with self.connection() as db:
            db.execute("UPDATE profiles SET style=? WHERE user_id=?", (style, user_id))

    def claim_update(self, update_id: int) -> bool:
        with self.connection() as db:
            return bool(
                db.execute(
                    "INSERT OR IGNORE INTO processed_updates VALUES(?,?)",
                    (update_id, time.time()),
                ).rowcount
            )

    def new_dialog(self, user_id: int) -> str:
        now = time.time()
        dialog_id = uuid.uuid4().hex
        with self.connection() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM dialogs WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            if count >= self.max_dialogs:
                raise DialogLimit("Удалите ненужный диалог перед созданием нового")
            db.execute(
                "INSERT INTO dialogs(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (dialog_id, user_id, "Новый диалог", now, now),
            )
            db.execute("UPDATE profiles SET active_dialog=? WHERE user_id=?", (dialog_id, user_id))
        return dialog_id

    def dialogs(self, user_id: int, *, offset: int = 0, limit: int = 8) -> list[dict]:
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM dialogs WHERE user_id=? "
                    "ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                    (user_id, limit, offset),
                )
            ]

    def activate_dialog(self, user_id: int, dialog_id: str) -> bool:
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM dialogs WHERE id=? AND user_id=?", (dialog_id, user_id)
            ).fetchone():
                return False
            db.execute("UPDATE profiles SET active_dialog=? WHERE user_id=?", (dialog_id, user_id))
            return True

    def clear_dialog(self, user_id: int, dialog_id: str) -> bool:
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM dialogs WHERE id=? AND user_id=?", (dialog_id, user_id)
            ).fetchone():
                return False
            db.execute("DELETE FROM turns WHERE dialog_id=?", (dialog_id,))
            db.execute(
                "UPDATE dialogs SET title=?,title_custom=0 WHERE id=?", ("Новый диалог", dialog_id)
            )
            return True

    def delete_dialog(self, user_id: int, dialog_id: str) -> bool:
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM dialogs WHERE id=? AND user_id=?", (dialog_id, user_id)
            ).fetchone():
                return False
            db.execute("DELETE FROM dialogs WHERE id=?", (dialog_id,))
            profile = db.execute(
                "SELECT active_dialog FROM profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            if profile[0] == dialog_id:
                replacement = db.execute(
                    "SELECT id FROM dialogs WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
                new_id = replacement[0] if replacement else uuid.uuid4().hex
                if not replacement:
                    now = time.time()
                    db.execute(
                        "INSERT INTO dialogs(id,user_id,title,created_at,updated_at) "
                        "VALUES(?,?,?,?,?)",
                        (new_id, user_id, "Новый диалог", now, now),
                    )
                db.execute("UPDATE profiles SET active_dialog=? WHERE user_id=?", (new_id, user_id))
            return True

    def begin_turn(
        self,
        user_id: int,
        update_id: int,
        text: str,
        model: str,
        *,
        replaces_turn_id: str | None = None,
    ) -> str | None:
        now = time.time()
        turn_id = uuid.uuid4().hex
        with self.connection() as db:
            if db.execute("SELECT 1 FROM turns WHERE update_id=?", (update_id,)).fetchone():
                return None
            profile = db.execute(
                "SELECT active_dialog FROM profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            if profile is None:
                raise LookupError("Unknown profile")
            if replaces_turn_id:
                latest = self._latest_turn(db, user_id, skip_failed=True)
                if (
                    latest is None
                    or latest["id"] != replaces_turn_id
                    or latest["status"] != "complete"
                    or latest["user_content"] != text
                ):
                    raise ValueError("Retry source is no longer the last completed turn")
            db.execute(
                "INSERT INTO turns(id,update_id,dialog_id,model,user_content,assistant_content,"
                "status,created_at,updated_at,replaces_turn_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id,
                    update_id,
                    profile[0],
                    model,
                    text,
                    None,
                    "processing",
                    now,
                    now,
                    replaces_turn_id,
                ),
            )
            db.execute(
                "UPDATE dialogs SET updated_at=?, title=CASE "
                "WHEN title=? AND title_custom=0 THEN ? "
                "ELSE title END WHERE id=?",
                (now, "Новый диалог", " ".join(text.split())[:60], profile[0]),
            )
        return turn_id

    def _prune_dialog(self, db, dialog_id: str, *, expire_empty: bool = False) -> None:
        db.execute(
            "DELETE FROM turns WHERE dialog_id=? AND status!='processing' AND id "
            "NOT IN (SELECT id FROM turns WHERE dialog_id=? ORDER BY created_at DESC,id DESC "
            "LIMIT ?)",
            (dialog_id, dialog_id, self.max_saved_turns),
        )
        # Заголовок тоже содержит текст: нельзя оставлять фрагмент удалённого запроса.
        first = db.execute(
            "SELECT user_content FROM turns WHERE dialog_id=? ORDER BY created_at,id LIMIT 1",
            (dialog_id,),
        ).fetchone()
        title = " ".join(first[0].split())[:60] if first else "Новый диалог"
        db.execute(
            "UPDATE dialogs SET title=?,title_custom=CASE WHEN ? THEN 0 ELSE title_custom END "
            "WHERE id=? AND (title_custom=0 OR ?)",
            (title, expire_empty and first is None, dialog_id, expire_empty and first is None),
        )

    def complete_turn(self, turn_id: str, answer: str) -> bool:
        with self.connection() as db:
            changed = db.execute(
                "UPDATE turns SET assistant_content=?,status='complete',updated_at=? "
                "WHERE id=? AND status='processing'",
                (answer, time.time(), turn_id),
            ).rowcount
            row = db.execute("SELECT dialog_id FROM turns WHERE id=?", (turn_id,)).fetchone()
            if row:
                self._prune_dialog(db, row[0])
            return bool(changed)

    def fail_turn(self, turn_id: str, status: str) -> None:
        if status not in {"failed", "unknown"}:
            raise ValueError("Invalid failure status")
        with self.connection() as db:
            db.execute(
                "UPDATE turns SET status=?,updated_at=? WHERE id=? AND status='processing'",
                (status, time.time(), turn_id),
            )
            row = db.execute("SELECT dialog_id FROM turns WHERE id=?", (turn_id,)).fetchone()
            if row:
                self._prune_dialog(db, row[0])

    def turn_status(self, turn_id: str) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT status FROM turns WHERE id=?", (turn_id,)).fetchone()
            return row[0] if row else None

    def history(self, user_id: int, dialog_id: str | None = None) -> list[dict[str, str]]:
        selected = dialog_id or self.profile(user_id)["active_dialog"]
        with self.connection() as db:
            rows = list(
                db.execute(
                    "SELECT t.* FROM turns t JOIN dialogs d ON d.id=t.dialog_id "
                    "WHERE d.user_id=? AND d.id=? AND t.status='complete' "
                    "AND NOT EXISTS (SELECT 1 FROM turns r WHERE r.replaces_turn_id=t.id "
                    "AND r.status='complete') ORDER BY t.created_at,t.id",
                    (user_id, selected),
                )
            )
        result = []
        for row in rows:
            result.extend(
                [
                    {"role": "user", "content": row["user_content"]},
                    {"role": "assistant", "content": row["assistant_content"]},
                ]
            )
        return result

    def export_dialog(self, user_id: int, dialog_id: str | None = None) -> str:
        parts = ["Shluz · история диалога", datetime.now(UTC).isoformat(), ""]
        for message in self.history(user_id, dialog_id):
            parts.extend(["Вы:" if message["role"] == "user" else "Shluz:", message["content"], ""])
        selected = dialog_id or self.profile(user_id)["active_dialog"]
        with self.connection() as db:
            revisions = db.execute(
                "SELECT t.* FROM turns t JOIN dialogs d ON d.id=t.dialog_id "
                "WHERE d.user_id=? AND d.id=? AND t.status='complete' "
                "AND EXISTS (SELECT 1 FROM turns r WHERE r.replaces_turn_id=t.id "
                "AND r.status='complete') ORDER BY t.created_at,t.id",
                (user_id, selected),
            ).fetchall()
        if revisions:
            parts.append("Предыдущие варианты ответов (не входят в контекст модели):")
            for row in revisions:
                parts.extend(["Вы:", row["user_content"], "Shluz:", row["assistant_content"], ""])
        return "\n".join(parts)

    def recover_pending(self) -> int:
        with self.connection() as db:
            return db.execute(
                "UPDATE turns SET status='unknown' WHERE status='processing'"
            ).rowcount

    def cleanup(self, *, retention_days: int = 30) -> None:
        with self.connection() as db:
            cutoff = time.time() - retention_days * 86400
            expired = {
                row[0]
                for row in db.execute(
                    "SELECT DISTINCT dialog_id FROM turns WHERE status!='processing' "
                    "AND created_at<?",
                    (cutoff,),
                )
            }
            expired.update(
                row[0]
                for row in db.execute(
                    "SELECT id FROM dialogs WHERE updated_at<?",
                    (cutoff,),
                )
            )
            db.execute(
                "DELETE FROM turns WHERE status!='processing' AND created_at<?",
                (cutoff,),
            )
            db.execute(
                "DELETE FROM processed_updates WHERE created_at<?", (time.time() - 7 * 86400,)
            )
            for dialog in db.execute("SELECT id FROM dialogs").fetchall():
                self._prune_dialog(db, dialog[0], expire_empty=dialog[0] in expired)

    def _latest_turn(self, db, user_id: int, *, skip_failed: bool = False):
        condition = " AND t.status!='failed'" if skip_failed else ""
        return db.execute(
            "SELECT t.* FROM turns t JOIN profiles p ON p.active_dialog=t.dialog_id "
            "WHERE p.user_id=?" + condition + " ORDER BY t.created_at DESC,t.id DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    def latest_turn(self, user_id: int) -> dict | None:
        with self.connection() as db:
            row = self._latest_turn(db, user_id)
            return dict(row) if row else None

    def turn_for(self, user_id: int, turn_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT t.* FROM turns t JOIN dialogs d ON d.id=t.dialog_id "
                "WHERE d.user_id=? AND t.id=?",
                (user_id, turn_id),
            ).fetchone()
            return dict(row) if row else None

    def retry_source(self, user_id: int, turn_id: str = "") -> dict | None:
        with self.connection() as db:
            row = self._latest_turn(db, user_id, skip_failed=True)
            if row and row["status"] == "complete" and (not turn_id or row["id"] == turn_id):
                return dict(row)
            return None

    def rate_answer(self, user_id: int, turn_id: str, rating: int) -> bool:
        if rating not in {-1, 1}:
            raise ValueError("Invalid feedback rating")
        with self.connection() as db:
            return bool(
                db.execute(
                    "UPDATE turns SET feedback=? WHERE id=? AND status='complete' AND "
                    "dialog_id IN (SELECT id FROM dialogs WHERE user_id=?)",
                    (rating, turn_id, user_id),
                ).rowcount
            )

    def rename_dialog(self, user_id: int, dialog_id: str, title: str) -> bool:
        title = " ".join(title.split())
        if not title or len(title) > 60:
            raise ValueError("Title must contain 1 to 60 characters")
        with self.connection() as db:
            return bool(
                db.execute(
                    "UPDATE dialogs SET title=?,title_custom=1,updated_at=? "
                    "WHERE id=? AND user_id=?",
                    (title, time.time(), dialog_id, user_id),
                ).rowcount
            )

    def forget_dialogs(self, user_id: int) -> None:
        # Сохраняем привязку аккаунта и tombstones updates: удаление не разрешает повтор расхода.
        now, dialog_id = time.time(), uuid.uuid4().hex
        with self.connection() as db:
            db.execute("DELETE FROM dialogs WHERE user_id=?", (user_id,))
            db.execute(
                "INSERT INTO dialogs(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (dialog_id, user_id, "Новый диалог", now, now),
            )
            db.execute(
                "UPDATE profiles SET active_dialog=? WHERE user_id=?",
                (dialog_id, user_id),
            )

    def metrics(self) -> list[dict]:
        # Только агрегаты по сохранённой истории, не финансовый отчёт и не тексты пользователей.
        with self.connection() as db:
            return [dict(row) for row in db.execute(METRICS_SQL)]
