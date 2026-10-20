"""Проверки короткой диагностики Venice; никакие реальные ключи не используются."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings


def test_unprefixed_venice_environment_key_is_supported(monkeypatch):
    monkeypatch.delenv('SHLUZ_VENICE_API_KEY', raising=False)
    monkeypatch.setenv('VENICE_API_KEY', 'synthetic-inference-key')
    settings = Settings(_env_file=None)
    assert settings.venice_api_key is not None
    assert settings.venice_api_key.get_secret_value() == 'synthetic-inference-key'
    assert settings.active_provider == 'mock'


def test_existing_shluz_key_takes_precedence(monkeypatch):
    monkeypatch.setenv('VENICE_API_KEY', 'synthetic-other')
    monkeypatch.setenv('SHLUZ_VENICE_API_KEY', 'synthetic-existing')
    assert Settings(_env_file=None).venice_api_key.get_secret_value() == 'synthetic-existing'


@pytest.fixture
def probe():
    path = Path('scripts/venice_probe.py')
    assert path.exists(), 'Нужна диагностика без публикации ключа и без лишних генераций'
    spec = importlib.util.spec_from_file_location('venice_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def client_for(calls, *, allowed=True, model=True, auth_status=200):
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/api_keys/rate_limits'):
            return httpx.Response(auth_status, json={'data': {
                'accessPermitted': allowed,
                'apiTier': {'id': 'paid', 'isCharged': True},
                'balances': {'USD': 1, 'DIEM': 0},
                'rateLimits': [{'apiModelId': 'example-model',
                                'rateLimits': [{'type': 'RPM', 'amount': 3}]}],
            }})
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'example-model',
                'model_spec': {'capabilities': {'supportsFunctionCalling': True},
                               'name': 'Example'}}] if model else []})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}}],
                                        'usage': {'prompt_tokens': 8, 'completion_tokens': 2}})
    return httpx.Client(base_url='https://api.venice.ai/api/v1',
                        transport=httpx.MockTransport(handler), follow_redirects=False)


def test_inspect_auth_first_and_never_generates_by_default(probe):
    calls = []
    with client_for(calls) as client:
        result = probe.inspect(client, 'synthetic-secret', 'example-model')
    assert [r.method for r in calls] == ['GET', 'GET']
    assert calls[0].url.path.endswith('/api_keys/rate_limits')
    assert result['access_permitted'] is True
    assert result['model']['id'] == 'example-model'
    assert result['model_rate_limits'] == [{'type': 'RPM', 'amount': 3}]
    assert 'synthetic-secret' not in json.dumps(result)


def test_chat_is_one_bounded_call_and_retains_tls_host(probe):
    calls = []
    with client_for(calls) as client:
        result = probe.inspect(client, 'synthetic-secret', 'example-model', chat=True)
    assert len(calls) == 3
    assert str(calls[-1].url) == 'https://api.venice.ai/api/v1/chat/completions'
    body = json.loads(calls[-1].content)
    assert body['model'] == 'example-model'
    assert body['max_completion_tokens'] == 128
    assert body['stream'] is False
    assert result['chat']['content_nonempty'] is True


@pytest.mark.parametrize('kw', [{'allowed': False}, {'model': False}, {'auth_status': 401}])
def test_invalid_access_never_sends_paid_request(probe, kw):
    calls = []
    with client_for(calls, **kw) as client:
        result = probe.inspect(client, 'synthetic-secret', 'example-model', chat=True)
    assert not any(r.method == 'POST' for r in calls)
    assert result['chat']['sent'] is False


def test_timeout_is_not_retried_or_leaked(probe):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError('synthetic-secret and private url', request=request)
    with httpx.Client(base_url='https://api.venice.ai/api/v1',
                      transport=httpx.MockTransport(handler)) as client:
        result = probe.inspect(client, 'synthetic-secret', 'example-model', chat=True)
    assert len(calls) == 1
    assert result['checks'][0]['error'] == 'ConnectError'
    assert 'synthetic-secret' not in json.dumps(result)
    assert 'private url' not in json.dumps(result)


def test_redirection_not_followed(probe):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://untrusted.invalid'})
    with httpx.Client(base_url='https://api.venice.ai/api/v1',
                      transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = probe.inspect(client, 'synthetic-secret', 'example-model', chat=True)
    assert len(calls) == 1
    assert result['checks'][0]['status'] == 302
    assert result['chat']['sent'] is False
