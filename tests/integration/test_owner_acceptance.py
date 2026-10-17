"""Техническая приёмка выполняется без участия владельца и платных моделей."""

import re

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, ApiToken, Plan
from tests.server_support import GatewayProcess


def test_http_access_quotas_revocation_and_restart(tmp_path):
    server = GatewayProcess(tmp_path)
    server.migrate()
    database = Database(Settings(database_url=SecretStr(server.database_url), _env_file=None))
    server.start()
    try:
        with httpx.Client(base_url=server.url, trust_env=False, timeout=10) as client:
            assert client.get('/health').status_code == 200
            assert client.get('/admin').status_code == 401
            assert client.get('/v1/models').status_code == 401
            assert client.get('/v1/models', headers={
                'Authorization': 'Bearer invalid-test-key',
            }).status_code == 401
            page = client.get('/admin/plans', auth=server.auth)
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            response = client.post('/admin/plans', auth=server.auth, data={
                'csrf_token': csrf, 'code': 'acceptance', 'name': 'Приёмка',
                'monthly_price_usd': '0', 'monthly_request_limit': '2',
                'request_credit_reserve': '0', 'markup_percent': '0', 'active': 'on',
            })
            assert response.status_code == 303
            with database.session() as session:
                plan_id = session.scalar(select(Plan).where(Plan.code == 'acceptance')).id
            keys, ids = {}, {}
            for name in ['А', 'Б', 'Без тарифа']:
                assert client.post('/admin/users', auth=server.auth, data={
                    'csrf_token': csrf, 'display_name': name, 'role': 'user', 'status': 'active',
                }).status_code == 303
                with database.session() as session:
                    ids[name] = session.scalar(
                        select(Account).where(Account.display_name == name)
                    ).id
                if name != 'Без тарифа':
                    assert client.post(
                        f'/admin/users/{ids[name]}/plan', auth=server.auth,
                        data={'csrf_token': csrf, 'plan_id': plan_id},
                    ).status_code == 303
                response = client.post(f'/admin/users/{ids[name]}/tokens', auth=server.auth,
                                       data={'csrf_token': csrf, 'source': 'api'})
                assert response.status_code == 200
                keys[name] = re.search(r'<code[^>]*class="token-value"[^>]*>([^<]+)',
                                       response.text).group(1)

            def headers(name):
                return {'Authorization': f'Bearer {keys[name]}'}

            payload = {'model': 'mock-chat', 'messages': [{'role': 'user', 'content': 'проверка'}]}
            for _ in range(2):
                result = client.post('/v1/chat/completions', headers=headers('А'), json=payload)
                assert result.status_code == 200
                assert result.json()['choices'][0]['message']['content'] == (
                    'Shluz mock reply: проверка'
                )
            exhausted = client.post('/v1/chat/completions', headers=headers('А'), json=payload)
            assert exhausted.status_code == 429
            assert exhausted.json()['error']['code'] == 'request_limit_exceeded'
            assert client.post('/v1/chat/completions', headers=headers('Б'),
                               json=payload).status_code == 200
            assert client.post('/v1/chat/completions', headers=headers('Без тарифа'),
                               json=payload).status_code == 402
            before = client.get('/v1/me', headers=headers('А')).json()
            assert before['allowance']['requests']['remaining'] == 0
            with database.session() as session:
                token_id = session.scalar(select(ApiToken).where(
                    ApiToken.account_id == ids['Б']
                )).id
            assert client.get('/v1/models', headers=headers('Б')).status_code == 200
            assert client.post(f'/admin/tokens/{token_id}/revoke', auth=server.auth,
                               data={'csrf_token': csrf}).status_code == 303
            assert client.get('/v1/models', headers=headers('Б')).status_code == 401
            server.restart()
            assert client.get('/v1/me', headers=headers('А')).json() == before
            assert client.get('/v1/models', headers=headers('Б')).status_code == 401
            assert client.post('/v1/chat/completions', headers=headers('А'),
                               json=payload).status_code == 429
            html = client.get('/admin/users', auth=server.auth).text
            assert f'user-{ids["А"]}' in html and f'user-{ids["Б"]}' in html
            assert 'Приёмка' in html
    finally:
        server.stop()
        database.engine.dispose()
