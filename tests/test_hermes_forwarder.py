import asyncio
import json

from app.agents.launch_monitor import agent as launch_agent
from app.agents.launch_monitor.agent import LaunchMonitorAgent


SAMPLE_TOKEN = {
    "pair_id": "0xpair",
    "pair_address": "0xpair",
    "token_address": "0xtoken",
    "chain": "base",
    "base_symbol": "TEST",
    "base_name": "Test Token",
    "quote_symbol": "WETH",
    "price_usd": "0.0001",
    "liquidity_usd": 12000,
    "market_cap": 85000,
    "volume_24h": 300000,
    "twitter_url": "https://x.com/testtoken",
    "twitter_followers": 1234,
    "rug_check": {"risk_level": "good", "score_normalised": 12},
    "red_flags": [("low liquidity", "warn")],
    "dex_url": "https://dexscreener.com/base/0xpair",
    "created_at": "2026-05-04T00:00:00",
    "age_minutes": 8,
}


def test_builds_structured_hermes_payload_for_token_research():
    agent = object.__new__(LaunchMonitorAgent)

    payload = agent._build_hermes_alert_payload(SAMPLE_TOKEN)

    assert payload["type"] == "new_token_alert"
    assert payload["source"] == "onchain_tools.launch_monitor"
    assert payload["wakeMode"] == "now"
    assert payload["delivery_policy"] == "only_watch_ape_buy"
    assert payload["token"]["chain"] == "base"
    assert payload["token"]["token_address"] == "0xtoken"
    assert payload["token"]["pair_address"] == "0xpair"
    assert payload["token"]["symbol"] == "TEST"
    assert payload["token"]["metrics"]["liquidity_usd"] == 12000
    assert payload["token"]["socials"]["twitter_url"] == "https://x.com/testtoken"
    assert payload["required_skills"] == ["token-research", "x-research", "agents-infra"]
    assert "WATCH" in payload["instructions"]
    assert "APE" in payload["instructions"]
    assert "SKIP" in payload["instructions"]
    json.dumps(payload)


def test_forwarder_posts_structured_payload_to_hermes_webhook(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 202
        text = "accepted"

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(launch_agent.settings, "abot_webhook_url", "https://hermes.example/webhooks/onchain-alerts")
    monkeypatch.setattr(launch_agent.settings, "abot_proxy_token", "secret-token")
    monkeypatch.setattr(launch_agent.httpx, "AsyncClient", FakeAsyncClient)

    agent = object.__new__(LaunchMonitorAgent)

    asyncio.run(agent._forward_to_abot(SAMPLE_TOKEN))

    assert len(calls) == 1
    assert calls[0]["url"] == "https://hermes.example/webhooks/onchain-alerts"
    assert calls[0]["headers"]["X-Proxy-Token"] == "secret-token"
    assert calls[0]["json"]["type"] == "new_token_alert"
    assert calls[0]["json"]["token"]["token_address"] == "0xtoken"
    assert calls[0]["json"]["token"]["metrics"]["market_cap"] == 85000
