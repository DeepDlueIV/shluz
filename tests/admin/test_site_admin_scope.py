"""Гостевые настройки сайта доступны из админки, но не смешиваются с owner DB."""

from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.admin.routes import _admin_csrf_token
from app.config import Settings
from app.db.database import Database
from app.db.models import ModelPolicy, ProductPolicy
from app.factory import create_app
from app.webaccess.models import DemoControl, DemoModelBound
from app.webaccess.settings import WebSettings


def test_site_scope_edits_only_guest_database(tmp_path):
    settings = Settings(
        environment="test",
        admin_username="owner",
        admin_password=SecretStr("test-admin-password"),
        database_url=SecretStr("sqlite+pysqlite:///" + str(tmp_path / "owner.sqlite")),
        site_demo_database_url=SecretStr("sqlite+pysqlite:///" + str(tmp_path / "site.sqlite")),
        _env_file=None,
    )
    site_settings = settings.model_copy(
        update={"database_url": SecretStr("sqlite+pysqlite:///" + str(tmp_path / "site.sqlite"))}
    )
    site_database = Database(site_settings)
    site_database.create_schema()
    with site_database.session() as session:
        session.add(
            ProductPolicy(
                id="default",
                mode="demo",
                demo_requests=0,
                demo_models_json='["mock-chat"]',
            )
        )

    app = create_app(settings=settings, web_settings=WebSettings(enabled=False, _env_file=None))
    auth = ("owner", "test-admin-password")
    try:
        with TestClient(app) as client:
            page = client.get("/admin/access?scope=site", auth=auth)
            assert page.status_code == 200
            assert "гостевой сайт / website guests" in page.text

            response = client.post(
                "/admin/access/demo",
                auth=auth,
                follow_redirects=False,
                data={
                    "csrf_token": _admin_csrf_token(settings),
                    "scope": "site",
                    "mode": "demo",
                    "demo_requests": "1",
                    "demo_models": "mistral-small-3-2-24b-instruct",
                    "demo_max_input_chars": "1000",
                    "demo_max_output_tokens": "512",
                    "demo_daily_requests": "10",
                    "requests_per_minute": "2",
                },
            )
            assert response.status_code == 303
            assert response.headers["location"] == "/admin/access?scope=site"

            with site_database.session() as session:
                site_policy = session.get(ProductPolicy, "default")
                assert site_policy.demo_requests == 1
                assert site_policy.demo_models_json == '["mistral-small-3-2-24b-instruct"]'
                assert session.get(ModelPolicy, "channel:web") is None
            with app.state.database.session() as session:
                assert session.get(ProductPolicy, "default") is None

            channel_response = client.post(
                "/admin/access/models",
                auth=auth,
                follow_redirects=False,
                data={
                    "csrf_token": _admin_csrf_token(settings),
                    "scope": "site",
                    "subject": "channel:web",
                    "models": "mistral-small-3-2-24b-instruct",
                },
            )
            assert channel_response.status_code == 303
            with site_database.session() as session:
                assert session.get(ModelPolicy, "channel:web").models_json == (
                    '["mistral-small-3-2-24b-instruct"]'
                )
            with app.state.database.session() as session:
                assert session.get(ModelPolicy, "channel:web") is None

            forbidden_model = client.post(
                "/admin/access/models",
                auth=auth,
                data={
                    "csrf_token": _admin_csrf_token(settings),
                    "scope": "site",
                    "subject": "channel:web",
                    "models": "unapproved-model",
                },
            )
            assert forbidden_model.status_code == 422

            forbidden_plan = client.post(
                "/admin/access/models",
                auth=auth,
                data={
                    "csrf_token": _admin_csrf_token(settings),
                    "scope": "site",
                    "subject": "plan:missing",
                    "models": "mistral-small-3-2-24b-instruct",
                },
            )
            assert forbidden_plan.status_code == 422

            unsafe = client.post(
                "/admin/access/demo",
                auth=auth,
                data={
                    "csrf_token": _admin_csrf_token(settings),
                    "scope": "site",
                    "mode": "demo",
                    "demo_requests": "1",
                    "demo_models": "mistral-small-3-2-24b-instruct",
                    "demo_max_input_chars": "1000",
                    "demo_max_output_tokens": "512",
                    "demo_daily_requests": "11",
                    "requests_per_minute": "2",
                },
            )
            assert unsafe.status_code == 422
    finally:
        app.state.database.dispose()
        site_database.dispose()


def test_site_web_protection_and_cost_bound_are_scoped_and_capped(tmp_path):
    settings = Settings(
        environment="test",
        admin_username="owner",
        admin_password=SecretStr("test-admin-password"),
        database_url=SecretStr("sqlite+pysqlite:///" + str(tmp_path / "owner.sqlite")),
        _env_file=None,
    )
    site_database = Database(
        settings.model_copy(
            update={
                "database_url": SecretStr("sqlite+pysqlite:///" + str(tmp_path / "site.sqlite"))
            }
        )
    )
    site_database.create_schema()
    app = create_app(
        settings=settings,
        site_demo_database=site_database,
        web_settings=WebSettings(enabled=False, _env_file=None),
    )
    auth = ("owner", "test-admin-password")
    control_data = {
        "csrf_token": _admin_csrf_token(settings),
        "scope": "site",
        "enabled": "on",
        "daily_usd": "0.02",
        "http_network_minute": "120",
        "http_global_minute": "2000",
        "captcha_global_minute": "300",
        "otp_verify_global_minute": "500",
        "concurrent": "1",
        "timeout_seconds": "45",
        "idle_seconds": "15",
        "network_daily": "10",
        "sessions_hourly": "20",
        "sessions_global_daily": "10",
        "captcha_minute": "10",
        "otp_email_daily": "5",
        "otp_network_daily": "20",
        "otp_global_daily": "100",
        "otp_verify_minute": "20",
    }
    try:
        with TestClient(app) as client:
            page = client.get("/admin/web-access?scope=site", auth=auth)
            assert page.status_code == 200
            assert "Venice API key" in page.text
            response = client.post(
                "/admin/web-access/control",
                auth=auth,
                data=control_data,
                follow_redirects=False,
            )
            assert response.status_code == 303
            with site_database.session() as session:
                site_control = session.get(DemoControl, "default")
                assert site_control.daily_usd == Decimal("0.02")
                assert site_control.concurrent == 1
            with app.state.database.session() as session:
                assert session.get(DemoControl, "default") is None

            unsafe = dict(control_data, daily_usd="0.03")
            assert (
                client.post("/admin/web-access/control", auth=auth, data=unsafe).status_code == 422
            )

            bound = {
                "csrf_token": _admin_csrf_token(settings),
                "scope": "site",
                "provider": "venice",
                "model": "mistral-small-3-2-24b-instruct",
                "max_cost_usd": "0.02",
                "max_input_chars": "1000",
                "max_output_tokens": "512",
                "reviewed": "on",
            }
            assert (
                client.post(
                    "/admin/web-access/model",
                    auth=auth,
                    data=bound,
                    follow_redirects=False,
                ).status_code
                == 303
            )
            with site_database.session() as session:
                assert (
                    session.get(DemoModelBound, "venice:mistral-small-3-2-24b-instruct") is not None
                )
            with app.state.database.session() as session:
                assert session.get(DemoModelBound, "venice:mistral-small-3-2-24b-instruct") is None
            unsafe_bound = dict(bound, model="different-paid-model")
            assert (
                client.post("/admin/web-access/model", auth=auth, data=unsafe_bound).status_code
                == 422
            )
    finally:
        app.state.database.dispose()
        site_database.dispose()


def test_site_scope_fails_closed_when_guest_database_is_not_configured(tmp_path):
    missing_site_file = tmp_path / "not-created.sqlite"
    settings = Settings(
        environment="test",
        admin_username="owner",
        admin_password=SecretStr("test-admin-password"),
        database_url=SecretStr("sqlite+pysqlite:///" + str(tmp_path / "owner.sqlite")),
        site_demo_database_url=SecretStr("sqlite+pysqlite:///" + str(missing_site_file)),
        _env_file=None,
    )
    app = create_app(settings=settings, web_settings=WebSettings(enabled=False, _env_file=None))
    try:
        with TestClient(app) as client:
            response = client.get("/admin/access?scope=site", auth=("owner", "test-admin-password"))
            assert response.status_code == 503
            assert not missing_site_file.exists()
    finally:
        app.state.database.dispose()
