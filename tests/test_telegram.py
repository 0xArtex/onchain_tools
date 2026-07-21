import asyncio

from app.agents.launch_monitor import agent as launch_agent
from app.agents.launch_monitor.agent import TelegramService


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def _fake_client_with_responses(responses, calls):
    """AsyncClient double that pops one canned response per POST."""

    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            calls.append((url, json))
            return _FakeResp(responses.pop(0))

    return _FakeClient


def test_send_message_returns_message_when_parse_retry_succeeds(monkeypatch):
    # Regression: the HTML parse-error retry used to fall through to an
    # unconditional `return None`, so a rescued send lost its message id and
    # Claude Code verdict replies could no longer thread onto the alert.
    calls = []
    responses = [
        {"ok": False, "description": "Bad Request: can't parse entities"},
        {"ok": True, "result": {"message_id": 5, "chat": {"id": 42}}},
    ]
    monkeypatch.setattr(
        launch_agent.httpx, "AsyncClient", _fake_client_with_responses(responses, calls)
    )

    tg = TelegramService(bot_token="t", chat_id="42")
    sent = asyncio.run(tg.send_message("<b>broken html"))

    assert sent == {"message_id": 5, "chat": {"id": 42}}
    assert len(calls) == 2
    assert "parse_mode" not in calls[1][1]  # retry went out without parse_mode


def test_send_message_returns_none_when_retry_also_fails(monkeypatch):
    calls = []
    responses = [
        {"ok": False, "description": "Bad Request: can't parse entities"},
        {"ok": False, "description": "Bad Request: message is too long"},
    ]
    monkeypatch.setattr(
        launch_agent.httpx, "AsyncClient", _fake_client_with_responses(responses, calls)
    )

    tg = TelegramService(bot_token="t", chat_id="42")
    assert asyncio.run(tg.send_message("<b>broken html")) is None
    assert len(calls) == 2
