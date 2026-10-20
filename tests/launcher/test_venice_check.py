"""Однокнопочная проверка: один платный POST, без смены рабочего провайдера."""

import importlib.util
import io
import json
from pathlib import Path

import pytest


def load():
    path = Path(__file__).parents[2] / "launcher/venice_check.py"
    spec = importlib.util.spec_from_file_location("venice_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def transport(access=True, truncated=False, bad_price=False):
    calls = []

    def request(method, path, key, body=None):
        calls.append((method, path, body))
        if path.endswith("rate_limits"):
            payload = {"data": {"accessPermitted": access, "balances": {"USD": 2, "DIEM": 0}}}
        elif path.startswith("/models"):
            payload = {"data": [{"id": "zai-org-glm-5-2", "model_spec": {"pricing": {
                "input": {"usd": "NaN" if bad_price else 1.4}, "output": {"usd": 4.4},
            }}}]}
        else:
            frames = [
                {"choices": [{"delta": {"content": "OK"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                 "cost": {"usd": 0.0000228}},
            ]
            raw = "".join("data: " + json.dumps(p) + "\n\n" for p in frames)
            if not truncated:
                raw += "data: [DONE]\n\n"
            return io.BytesIO(raw.encode()), lambda: None
        return io.BytesIO(json.dumps(payload).encode()), lambda: None

    return request, calls


def test_read_only_default_and_access_denied_never_generate():
    u = load()
    for enabled, chat in [(True, False), (False, True)]:
        request, calls = transport(enabled)
        report = u.probe("private-secret", chat=chat, request=request)
        assert report["generations_attempted"] == 0
        assert not any(c[0] == "POST" for c in calls)


def test_one_bounded_stream_and_no_content_or_key_in_report():
    u = load()
    request, calls = transport()
    report = u.probe("private-secret", chat=True, request=request)
    assert report["generations_attempted"] == 1 and report["stream_complete"] is True
    assert report["content_nonempty"] is True
    assert report["usage"]["completion_tokens"] == 2
    post = [c for c in calls if c[0] == "POST"]
    assert len(post) == 1 and post[0][2]["max_completion_tokens"] == 128
    assert "tools" not in post[0][2]
    assert "OK" not in json.dumps(report) and "private-secret" not in json.dumps(report)


def test_incomplete_stream_does_not_retry_or_claim_success():
    u = load()
    request, calls = transport(truncated=True)
    report = u.probe("key", chat=True, request=request)
    assert report["stream_complete"] is False
    assert len([c for c in calls if c[0] == "POST"]) == 1


def test_unknown_pricing_prevents_paid_probe():
    u = load()
    request, calls = transport(bad_price=True)
    report = u.probe("key", chat=True, request=request)
    assert report["generations_attempted"] == 0
    assert not any(c[0] == "POST" for c in calls)


def test_network_failure_is_not_key_rejection_and_not_retried():
    u = load()
    calls = []

    def fail(*args, **kwargs):
        calls.append(args[:2])
        raise OSError("private-secret in exception")

    report = u.probe("private-secret", chat=True, request=fail)
    assert report["access_permitted"] is None and report["generations_attempted"] == 0
    assert len(calls) == 1
    assert "private-secret" not in json.dumps(report)


def test_production_transport_only_fixed_venice_host(monkeypatch):
    u = load()
    with pytest.raises(ValueError):
        u.http_request("GET", "https://attacker.invalid", "key")
