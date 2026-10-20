from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from app.db.models import Account, Plan, Subscription
from app.product import service as product
from app.providers.base import ChatResult
from tests.api.test_telegram_onboarding import auto_client as fixture_client


@pytest.fixture
def wc(tmp_path):
    yield from fixture_client.__wrapped__(tmp_path)


def setup(c, amount="10"):
    from app.billing.service import credit

    with c.app.state.database.session() as s:
        account = Account(display_name="Wallet test")
        plan = Plan(
            code="payg",
            name="По использованию",
            billing_mode="payg",
            markup_percent=Decimal("20"),
            monthly_request_limit=1,
        )
        s.add_all([account, plan])
        s.flush()
        s.add(Subscription(account_id=account.id, plan_id=plan.id, source="harness"))
    with product.locked_session(c.app.state.database) as s:
        credit(s, account.id, Decimal(amount), "topup-1", "owner", "Тестовое начисление")
    return account.id, plan.id


def authorize(c, aid, **kw):
    return c.app.state.usage_service.authorize_request(
        account_id=aid,
        source="harness",
        provider=kw.pop("provider", "mock"),
        model=kw.pop("model", "mock-chat"),
        **kw,
    )


def bound(c, cost="2"):
    from app.billing.models import WalletModelBound

    with c.app.state.database.session() as s:
        s.add(
            WalletModelBound(
                id="venice:sample",
                max_cost_usd=Decimal(cost),
                max_input_chars=3000,
                max_output_tokens=512,
                reviewed=True,
            )
        )


def reserve_paid(c, aid):
    return authorize(
        c, aid, provider="venice", model="sample", input_chars=10, requested_output_tokens=100
    )


def allow_paid(c, pid):
    from app.db.models import ModelPolicy

    with c.app.state.database.session() as s:
        s.add(ModelPolicy(id="channel:harness", models_json='["sample","mock-chat"]'))


def test_credit_exactly_once_and_units_do_not_change_money(wc):
    from app.billing.service import credit, snapshot, wallet

    aid, pid = setup(wc)
    with product.locked_session(wc.app.state.database) as s:
        credit(s, aid, Decimal("10"), "topup-1", "owner", "Тестовое начисление")
        wallet(s, aid).display_unit = "credits"
        data = snapshot(s, aid)
        assert Decimal(data["balance_usd"]) == 10
        assert Decimal(data["balance_credits"]) == 1000
    with product.locked_session(wc.app.state.database) as s, pytest.raises(ValueError):
        credit(s, aid, Decimal("12"), "topup-1", "owner", "changed")


def test_markup_debit_once_and_hold_remainder_released(wc):
    from app.billing.service import snapshot

    aid, pid = setup(wc)
    bound(wc)
    allow_paid(wc, pid)
    r = reserve_paid(wc, aid)
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["reserved_usd"]) == Decimal("2.4")
    result = ChatResult(content="ok", cost_usd=Decimal("1"), prompt_tokens=3, completion_tokens=4)
    wc.app.state.usage_service.record_success(result=result, reservation=r)
    wc.app.state.usage_service.record_success(result=result, reservation=r)
    with wc.app.state.database.session() as s:
        data = snapshot(s, aid)
        assert Decimal(data["balance_usd"]) == Decimal("8.8")
        assert Decimal(data["reserved_usd"]) == 0
        assert data["unit_rate"] == 100


def test_unknown_and_expired_hold_never_becomes_free(wc):
    from app.billing.service import snapshot

    aid, pid = setup(wc)
    bound(wc)
    allow_paid(wc, pid)
    r = reserve_paid(wc, aid)
    wc.app.state.usage_service.record_failure(r, error_code="provider_error")
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["reserved_usd"]) == Decimal("2.4")
        assert Decimal(snapshot(s, aid)["available_usd"]) == Decimal("7.6")


def test_definite_refusal_releases_reserve(wc):
    from app.billing.service import snapshot

    aid, pid = setup(wc)
    bound(wc)
    allow_paid(wc, pid)
    r = reserve_paid(wc, aid)
    wc.app.state.usage_service.record_failure(r, error_code="provider_authentication_error")
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["available_usd"]) == 10


def test_parallel_reservation_cannot_overspend(wc):
    from app.product.service import PolicyError

    aid, pid = setup(wc, "3")
    bound(wc)
    allow_paid(wc, pid)

    def attempt(_):
        try:
            reserve_paid(wc, aid)
            return True
        except PolicyError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1


def test_channel_specific_plan_leaves_telegram_subscription(wc):
    aid, pid = setup(wc)
    with wc.app.state.database.session() as s:
        p = Plan(code="tg", name="Telegram subscription", monthly_request_limit=10)
        s.add(p)
        s.flush()
        s.add(Subscription(account_id=aid, plan_id=p.id, source="telegram"))
    with wc.app.state.database.session() as s:
        assert product.active_subscription(s, aid, source="harness")[1].billing_mode == "payg"
        assert product.active_subscription(s, aid, source="telegram")[1].id == p.id
    # Две генерации payg не ограничиваются сохранённым monthly_request_limit=1.
    for _ in range(2):
        r = authorize(wc, aid)
        wc.app.state.usage_service.record_success(reservation=r, result=ChatResult(content="ok"))


def test_unreviewed_price_and_oversized_input_rejected(wc):
    from app.product.service import PolicyError

    aid, pid = setup(wc)
    allow_paid(wc, pid)
    with pytest.raises(PolicyError):
        reserve_paid(wc, aid)
    bound(wc)
    with pytest.raises(PolicyError):
        authorize(
            wc,
            aid,
            provider="venice",
            model="sample",
            input_chars=3001,
            requested_output_tokens=100,
        )


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "0", "0.000000001"])
def test_invalid_credit_rejected(wc, amount):
    from app.billing.service import credit

    aid, pid = setup(wc)
    with product.locked_session(wc.app.state.database) as s, pytest.raises(ValueError):
        credit(s, aid, Decimal(amount), "bad", "owner", "x")


def test_payg_profile_and_wallet_api_do_not_show_demo(wc):
    from app.db.repositories import create_api_token
    from telegram_bot.ui import profile_text

    aid, _ = setup(wc)
    with wc.app.state.database.session() as s:
        create_api_token(
            s, account_id=aid, name="desktop", source="harness", raw_token="shluz_payg_test"
        )
    headers = {"Authorization": "Bearer shluz_payg_test"}
    me = wc.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["allowance"]["scope"] == "harness"
    text = profile_text(me.json(), {"user_id": 123, "model": "mock-chat"})
    assert "10" in text and "USD" in text and "Демо выдаётся" not in text
    result = wc.get("/v1/me/wallet", headers=headers)
    assert result.status_code == 200
    assert Decimal(result.json()["wallet"]["balance_usd"]) == 10
    assert len(result.json()["entries"]) == 1
    assert "actor" not in str(result.json())
    assert wc.get("/v1/me/wallet").status_code == 401


def test_invite_assignment_does_not_use_desktop_plan(wc):
    from app.db.models import Identity, TelegramAdmission
    from app.telegram.service import approve

    aid, payg = setup(wc)
    with wc.app.state.database.session() as s:
        tgplan = Plan(code="telegram", name="Telegram", monthly_request_limit=10)
        s.add(tgplan)
        s.add(Identity(account_id=aid, provider="telegram", subject="55555"))
        admission = TelegramAdmission(telegram_id="55555", display_name="Test", account_id=aid)
        s.add(admission)
        s.flush()
    approve(wc.app.state.database, admission.id, tgplan.id, actor="owner")
    with wc.app.state.database.session() as s:
        assert product.active_subscription(s, aid, source="telegram")[1].id == tgplan.id
        assert product.active_subscription(s, aid, source="harness")[1].id == payg


def test_wallet_admin_requires_auth_csrf_and_idempotency(wc):
    from app.billing.service import snapshot
    from tests.api.test_telegram_onboarding import admin_post

    aid, _ = setup(wc)
    assert wc.get("/admin/wallets").status_code == 401
    assert (
        wc.post(
            f"/admin/wallets/{aid}/credit", auth=("owner", "test-password"), data={"amount": "10"}
        ).status_code
        == 403
    )
    for _ in range(2):
        r = admin_post(
            wc,
            f"/admin/wallets/{aid}/credit",
            amount="2.5",
            note="owner credit",
            operation_id="idem-second",
        )
        assert r.status_code == 303
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["balance_usd"]) == Decimal("12.5")
    page = wc.get("/admin/wallets", auth=("owner", "test-password"))
    assert page.status_code == 200
    assert "не подключён" in page.text


def test_overrun_caps_customer_charge_and_disables_rule(wc):
    from app.billing.models import WalletHold, WalletModelBound
    from app.billing.service import snapshot

    aid, pid = setup(wc)
    bound(wc)
    allow_paid(wc, pid)
    r = reserve_paid(wc, aid)
    wc.app.state.usage_service.record_success(
        result=ChatResult(content="ok", cost_usd=Decimal("3")), reservation=r
    )
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["balance_usd"]) == Decimal("7.6")
        assert not s.get(WalletModelBound, "venice:sample").reviewed
        assert s.get(WalletHold, r.event_id).state == "review"


def test_uncertain_reserve_can_be_reconciled_once_by_admin(wc):
    from app.billing.service import snapshot
    from tests.api.test_telegram_onboarding import admin_post

    aid, pid = setup(wc)
    bound(wc)
    allow_paid(wc, pid)
    r = reserve_paid(wc, aid)
    wc.app.state.usage_service.record_failure(r, error_code="provider_error")
    for _ in range(2):
        res = admin_post(
            wc,
            f"/admin/wallets/holds/{r.event_id}/resolve",
            cost_usd="1",
            note="Проверен счёт поставщика",
            confirm="yes",
        )
        assert res.status_code == 303
    with wc.app.state.database.session() as s:
        funds = snapshot(s, aid)
        assert Decimal(funds["balance_usd"]) == Decimal("8.8")
        assert Decimal(funds["reserved_usd"]) == 0


def test_non_usd_provider_units_are_not_treated_as_free(wc):
    from app.billing.service import snapshot

    aid, pid = setup(wc)
    bound(wc)
    allow_paid(wc, pid)
    r = reserve_paid(wc, aid)
    event = wc.app.state.usage_service.record_success(
        result=ChatResult(content="ok", cost_usd=Decimal("0"), cost_diem=Decimal("1")),
        reservation=r,
    )
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["reserved_usd"]) == Decimal("2.4")
    assert not event.cost_known


def test_wallet_storage_preserves_eight_decimals_even_large_balance(wc):
    from app.billing.service import snapshot

    aid, _ = setup(wc, "1000000000.12345678")
    with wc.app.state.database.session() as s:
        assert Decimal(snapshot(s, aid)["balance_usd"]) == Decimal("1000000000.12345678")


def test_migration_preserves_old_account_and_subscription_defaults(tmp_path, monkeypatch):
    import sqlite3

    from alembic import command
    from alembic.config import Config

    db = tmp_path / "wallet-upgrade.db"
    monkeypatch.setenv("SHLUZ_DATABASE_URL", "sqlite+pysqlite:///" + str(db))
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "20261020_10")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO accounts(id,display_name,role,status,created_at,updated_at) "
            "VALUES('old','Saved','user','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT display_name FROM accounts WHERE id='old'").fetchone() == (
            "Saved",
        )
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20261020_11",
        )
        assert conn.execute("SELECT COUNT(*) FROM wallet_entries").fetchone() == (0,)
    command.check(cfg)
