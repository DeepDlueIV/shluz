"""Контракт нового доступа: написан до реализации, проверяется в облаке."""
from fastapi.testclient import TestClient

from app.factory import create_app
from app.db.models import Account, ProductPolicy
from app.product.service import demo_allowance, grant_demo, policy


def test_web_access_routes_exist_but_fail_closed_without_configuration(test_settings):
    app = create_app(test_settings)
    paths = app.openapi()['paths']
    for path in ('/v1/web/session', '/v1/web/chat/completions',
                 '/v1/web/auth/otp/request', '/v1/web/auth/otp/verify',
                 '/v1/web/logout', '/v1/web/me'):
        assert path in paths, f'Missing web contract: {path}'
    with TestClient(app) as client:
        response = client.post('/v1/web/session', headers={'Origin': 'https://example.test'})
        assert response.status_code == 503
        assert not response.cookies


def test_new_demo_defaults_are_one_small_request(test_settings):
    app = create_app(test_settings)
    with app.state.database.session() as session:
        config = policy(session)
        assert config.demo_requests == 1
        assert config.demo_max_input_chars == 3000
        assert config.demo_max_output_tokens == 512


def test_grant_keeps_issued_limit_when_future_default_changes(test_settings):
    app = create_app(test_settings)
    db = app.state.database
    with db.session() as session:
        config = ProductPolicy(id='default', demo_requests=1)
        session.add(config)
        account = Account(display_name='Исторический пакет')
        session.add(account)
        session.flush()
        account_id = account.id
        grant_demo(session, account_id)
    with db.session() as session:
        session.get(ProductPolicy, 'default').demo_requests = 5
    with db.session() as session:
        assert demo_allowance(session, account_id)['limit'] == 1
        new = Account(display_name='Новый пакет')
        session.add(new)
        session.flush()
        grant_demo(session, new.id)
        assert demo_allowance(session, new.id)['limit'] == 5
