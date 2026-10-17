"""Регрессии упрощённой панели владельца; финансы и доступ не подменяются UI."""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.admin.presentation import database_info, datetime_ru, form_number, money, plural
from app.db.models import Account, ApiToken, Plan, UsageEvent

AUTH = ('test-admin', 'test-admin-password')


@pytest.mark.parametrize(('raw', 'expected'), [
    ('0E-8', '0,00'), ('-0.00000000', '0,00'), ('3', '3,00'),
    ('1234.567', '1\u00a0234,57'), ('0.00000006', '0,00000006'),
    ('0.00350000', '0,0035'), ('-0.00350000', '−0,0035'), ('-42.005', '−42,01'),
])
def test_money(raw, expected):
    assert money(Decimal(raw)) == expected


def test_form_precision_and_readable_date():
    assert form_number(Decimal('6E-8')) == '0.00000006'
    assert form_number(Decimal('0E-8')) == '0'
    assert form_number(None) == ''
    assert datetime_ru(datetime(2026, 10, 17, 19, 54, 8, 123456, tzinfo=UTC)) == (
        '17.10.2026, 19:54:08 UTC'
    )


@pytest.mark.parametrize(('count', 'expected'), [
    (0, 'тарифов'), (1, 'тариф'), (2, 'тарифа'), (5, 'тарифов'),
    (11, 'тарифов'), (21, 'тариф'), (24, 'тарифа'), (111, 'тарифов'),
])
def test_plural(count, expected):
    assert plural(count, 'тариф', 'тарифа', 'тарифов') == expected


@pytest.mark.parametrize(('url', 'kind'), [
    ('sqlite+pysqlite:///./data/shluz.db', 'SQLite'),
    ('sqlite+pysqlite:///:memory:', 'SQLite · в памяти'),
    ('postgresql+psycopg://u:PRIVATE@db.abc.supabase.co/postgres', 'Supabase'),
    ('postgresql+psycopg://u:PRIVATE@aws-0-eu.pooler.supabase.com/postgres', 'Supabase'),
    ('postgresql+psycopg://u:PRIVATE@my-postgres/postgres', 'PostgreSQL'),
    ('postgresql+psycopg://u:PRIVATE@not-supabase.co.evil.test/postgres', 'PostgreSQL'),
])
def test_safe_database_label(url, kind):
    info = database_info(url)
    assert info['name'] == kind
    assert 'PRIVATE' not in str(info) and 'postgresql+psycopg://' not in str(info)


@pytest.mark.parametrize('path', [
    '/admin', '/admin/users', '/admin/plans', '/admin/usage',
    '/admin/logs', '/admin/test', '/admin/providers/venice',
])
def test_shared_navigation(client, path):
    response = client.get(path, auth=AUTH)
    assert response.status_code == 200
    assert response.text.count('aria-label="Разделы Shluz"') == 1
    assert 'aria-current="page"' in response.text
    assert '/static/admin.js' in response.text and 'data-tooltip=' in response.text


def seed(client, name='Алексей · тест', status='active'):
    with client.app.state.database.session() as session:
        account = Account(display_name=name, status=status)
        session.add(account)
        session.flush()
        session.add(UsageEvent(account_id=account.id, source='harness', provider='mock',
                               model='mock-chat', status='success'))
        return account.id


def csrf(client):
    return re.search(r'name="csrf_token" value="([^"]+)"',
                     client.get('/admin/users', auth=AUTH).text).group(1)


def test_named_expense_links_and_escaping(client):
    one = seed(client)
    two = seed(client)
    unsafe = seed(client, '<script>alert(1)</script>')
    archived = seed(client, 'Архив', 'archived')
    html = client.get('/admin/usage', auth=AUTH).text
    assert html.count('Алексей · тест') >= 4
    for account_id in (one, two, unsafe):
        assert f'/admin/users#user-{account_id}' in html
    assert f'/admin/users?archive=1#user-{archived}' in html
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '0E-8' not in html and '0,00' in html and 'За всё время' in html
    assert 'Успешно' in html and 'Десктоп / API-клиент' in html


def test_advanced_fields_keep_precision_and_help_is_not_inside_labels(client):
    with client.app.state.database.session() as session:
        session.add(Plan(code='tiny', name='Малый бюджет',
                         monthly_cost_limit_usd=Decimal('6E-8')))
    html = client.get('/admin/plans', auth=AUTH).text
    assert 'Расширенные настройки' in html
    assert '0,00000006' in html and 'value="0.00000006"' in html
    assert '6E-8' not in html and 'не дополнительная плата' in html
    for path in ['/admin/plans', '/admin/test', '/admin/users']:
        html = client.get(path, auth=AUTH).text
        for body in re.findall(r'<label\b[^>]*>(.*?)</label>', html, flags=re.S):
            assert 'help-trigger' not in body


def test_token_label_optional_but_validation_and_csrf_remain(client):
    account_id = seed(client)
    url = f'/admin/users/{account_id}/tokens'
    response = client.post(url, auth=AUTH, data={'csrf_token': csrf(client),
                                               'source': 'telegram', 'name': ' '})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert 'data-copy' in response.text
    with client.app.state.database.session() as session:
        token = session.scalar(select(ApiToken).where(ApiToken.account_id == account_id))
        assert token.name.startswith('Telegram') and token.source == 'telegram'
    assert client.post(url, auth=AUTH, data={'source': 'telegram'}).status_code == 403
    assert client.post(url, auth=AUTH, data={'csrf_token': csrf(client),
                       'source': 'telegram', 'name': 'x' * 201}).status_code == 422


def test_overview_safe_database_info(client):
    html = client.get('/admin', auth=AUTH).text
    assert 'SQLite' in html and 'База данных' in html
    client.app.state.settings.database_url = SecretStr(
        'postgresql+psycopg://USER:PRIVATE@db.a.supabase.co/postgres'
    )
    html = client.get('/admin', auth=AUTH).text
    assert 'Supabase' in html and 'PRIVATE' not in html
    assert 'test-admin-password' not in html
