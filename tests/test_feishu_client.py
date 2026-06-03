import sys
import types

import pytest

from autocoder.adapters.feishu.client import FeishuClient


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_requests(monkeypatch, post_payloads):
    """注入假的 requests 模块，post 按调用次序返回预设 payload。"""
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        payload = post_payloads[calls["n"]]
        calls["n"] += 1
        return _Resp(payload)

    fake = types.ModuleType("requests")
    fake.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", fake)
    return calls


def test_tenant_token_success(monkeypatch):
    _fake_requests(monkeypatch, [
        {"code": 0, "tenant_access_token": "t-abc", "expire": 7200},
    ])
    c = FeishuClient(app_id="cli_x", app_secret="s")
    assert c._tenant_token() == "t-abc"


def test_tenant_token_failure_raises(monkeypatch):
    _fake_requests(monkeypatch, [
        {"code": 10003, "msg": "app not found"},
    ])
    c = FeishuClient(app_id="cli_x", app_secret="s")
    with pytest.raises(RuntimeError) as exc:
        c._tenant_token()
    assert "tenant_access_token" in str(exc.value)
    assert "10003" in str(exc.value)


def test_send_card_success(monkeypatch):
    _fake_requests(monkeypatch, [
        {"code": 0, "tenant_access_token": "t-abc"},   # token
        {"code": 0, "msg": "success"},                  # send
    ])
    c = FeishuClient(app_id="cli_x", app_secret="s")
    assert c.send_card("oc_x", "{}") is True


def test_send_card_failure_raises(monkeypatch):
    _fake_requests(monkeypatch, [
        {"code": 0, "tenant_access_token": "t-abc"},   # token
        {"code": 230002, "msg": "bot not in chat"},     # send 失败
    ])
    c = FeishuClient(app_id="cli_x", app_secret="s")
    with pytest.raises(RuntimeError) as exc:
        c.send_card("oc_x", "{}")
    assert "230002" in str(exc.value)
