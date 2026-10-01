"""Локальная история Telegram. Пользователи, тарифы и платежи остаются в Shluz."""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


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
            if version > 1:
                raise RuntimeError("База Telegram создана более новой версией")
            db.execute("PRAGMA journal_mode=WAL")
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
                    "INSERT INTO dialogs VALUES(?,?,?,?,?)",
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
                "INSERT INTO dialogs VALUES(?,?,?,?,?)",
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
            db.execute("UPDATE dialogs SET title=? WHERE id=?", ("Новый диалог", dialog_id))
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
                        "INSERT INTO dialogs VALUES(?,?,?,?,?)",
                        (new_id, user_id, "Новый диалог", now, now),
                    )
                db.execute("UPDATE profiles SET active_dialog=? WHERE user_id=?", (new_id, user_id))
            return True

    def begin_turn(self, user_id: int, update_id: int, text: str, model: str) -> str | None:
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
            db.execute(
                "INSERT INTO turns VALUES(?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            db.execute(
                "UPDATE dialogs SET updated_at=?, title=CASE WHEN title=? THEN ? "
                "ELSE title END WHERE id=?",
                (now, "Новый диалог", " ".join(text.split())[:60], profile[0]),
            )
        return turn_id

    def complete_turn(self, turn_id: str, answer: str) -> bool:
        with self.connection() as db:
            return bool(
                db.execute(
                    "UPDATE turns SET assistant_content=?,status='complete',updated_at=? "
                    "WHERE id=? AND status='processing'",
                    (answer, time.time(), turn_id),
                ).rowcount
            )

    def fail_turn(self, turn_id: str, status: str) -> None:
        if status not in {"failed", "unknown"}:
            raise ValueError("Invalid failure status")
        with self.connection() as db:
            db.execute(
                "UPDATE turns SET status=?,updated_at=? WHERE id=? AND status='processing'",
                (status, time.time(), turn_id),
            )

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
                    "ORDER BY t.created_at,t.id",
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
        return "\n".join(parts)

    def recover_pending(self) -> int:
        with self.connection() as db:
            return db.execute(
                "UPDATE turns SET status='unknown' WHERE status='processing'"
            ).rowcount

    def cleanup(self, *, retention_days: int = 30) -> None:
        with self.connection() as db:
            db.execute(
                "DELETE FROM turns WHERE status!='processing' AND created_at<?",
                (time.time() - retention_days * 86400,),
            )
            db.execute(
                "DELETE FROM processed_updates WHERE created_at<?", (time.time() - 7 * 86400,)
            )
            for dialog in db.execute("SELECT id FROM dialogs").fetchall():
                db.execute(
                    "DELETE FROM turns WHERE dialog_id=? AND status!='processing' AND id "
                    "NOT IN (SELECT id FROM turns WHERE dialog_id=? ORDER BY created_at DESC "
                    "LIMIT ?)",
                    (dialog[0], dialog[0], self.max_saved_turns),
                )
