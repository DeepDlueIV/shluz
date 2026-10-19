"""Миграция проверяется на старой схеме, а не metadata.create_all."""

from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import inspect, select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, DemoGrant, ProductPolicy


def test_migration_preserves_old_demo_then_issues_new_default(tmp_path, monkeypatch):
    url = "sqlite+pysqlite:///" + str(tmp_path / "migration.db")
    monkeypatch.setenv("SHLUZ_DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "20261019_08")
    db = Database(Settings(database_url=SecretStr(url), _env_file=None))
    with db.session() as s:
        s.add(ProductPolicy(id="default", mode="demo", demo_requests=3, demo_max_input_chars=24000))
        a = Account(display_name="Старый аккаунт")
        s.add(a)
        s.flush()
        account_id = a.id
        s.add(DemoGrant(account_id=a.id, requests_used=2))
    command.upgrade(cfg, "head")
    assert "demo_limits" in inspect(db.engine).get_table_names()
    from app.product.service import demo_allowance, grant_demo
    from app.webaccess.models import DemoControl, DemoLimit

    with db.session() as s:
        assert demo_allowance(s, account_id) == {
            "limit": 3,
            "used": 2,
            "pending": 0,
            "remaining": 1,
        }
        assert s.get(ProductPolicy, "default").demo_requests == 1
        assert s.get(ProductPolicy, "default").demo_max_input_chars == 3000
        assert not s.scalar(select(DemoControl))  # создаётся лениво с закрытым paid budget
        a = Account(display_name="Новый аккаунт")
        s.add(a)
        s.flush()
        grant_demo(s, a.id)
        assert s.get(DemoLimit, a.id).requests_limit == 1
    command.upgrade(cfg, "head")
    with db.session() as s:
        assert s.get(DemoGrant, account_id).requests_used == 2
    db.dispose()
