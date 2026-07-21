import asyncio
import json

from app.services import cabalspy as cs
from app.services.cabalspy import CabalReport, CabalSpyService

TOKEN = "0x" + "e" * 40


def _make(monkeypatch, **over):
    defaults = dict(
        cabalspy_api_key="test-key",
        cabalspy_chains="robinhood",
        cabalspy_wallet_type="",
        cabalspy_min_buyers=0,
        cabalspy_tx_limit=100,
        cabalspy_max_checks=6,
    )
    defaults.update(over)
    for k, v in defaults.items():
        monkeypatch.setattr(cs.settings, k, v, raising=False)
    return CabalSpyService()


class _FakeResp:
    def __init__(self, status_code, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text or (json.dumps(data) if data else "")

    def json(self):
        return self._data


def _fake_client(script, calls):
    """AsyncClient double; `script` maps blockchain code -> response."""

    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
            return script[params["blockchain"]]

    return _FakeClient


def _tx(wallet, wtype, holding=True, action="buy", name="", twitter=""):
    return {
        "transaction_type": action,
        "wallet_address": wallet,
        "profile": {"name": name or wallet[:6], "twitter": twitter, "type": wtype},
        "holdings_after": {"still_holding": holding},
    }


def _body(*txs):
    return {"success": True, "data": {"transactions": list(txs)}}


# ── enablement / coverage ────────────────────────────────────────────
def test_disabled_without_key(monkeypatch):
    t = _make(monkeypatch, cabalspy_api_key="")
    assert t.enabled is False
    assert t.covers("robinhood") is False
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is None


def test_covers_only_configured_chains(monkeypatch):
    t = _make(monkeypatch, cabalspy_chains="robinhood")
    assert t.covers("robinhood") is True
    assert t.covers("base") is False       # supported by CabalSpy, not configured
    assert t.covers("solana") is False


def test_covers_ignores_unknown_chain(monkeypatch):
    t = _make(monkeypatch, cabalspy_chains="robinhood, nosuchchain")
    assert t.covers("nosuchchain") is False  # no CabalSpy chain code


# ── scoring ──────────────────────────────────────────────────────────
def test_score_weights_and_exit_penalty():
    buyers = [
        {"type": "kol", "still_holding": True},     # 3.0
        {"type": "smart", "still_holding": True},   # 3.0
        {"type": "kol", "still_holding": False},    # 1.5 (exited -> half)
        {"type": "whale", "still_holding": True},   # 2.0
        {"type": "", "still_holding": True},        # 1.0 (unknown label)
    ]
    assert CabalSpyService.score_buyers(buyers) == 100  # 10.5 * 10 capped
    assert CabalSpyService.score_buyers(buyers[:2]) == 60
    assert CabalSpyService.score_buyers([]) == 0


# ── parsing ──────────────────────────────────────────────────────────
def test_analyze_parses_buys_dedupes_and_counts(monkeypatch):
    # Newest-first feed. holdings_after is POST-transaction state: a buy row
    # is always "holding"; the exit signal only appears on sell rows.
    t = _make(monkeypatch)
    calls = []
    body = _body(
        _tx("0xbbb", "smart", holding=False, action="sell"),  # newest: bbb exited
        _tx("0xaaa", "kol", holding=True, name="Alpha", twitter="alpha"),
        _tx("0xaaa", "kol", holding=True),                    # older dup -> ignored
        _tx("0xbbb", "smart", holding=True),                  # bbb's original buy
        _tx("0xccc", "whale", holding=False, action="sell"),  # sell-only -> not a buyer
    )
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client({"rh": _FakeResp(200, body)}, calls)
    )

    report = asyncio.run(t.analyze("robinhood", TOKEN))

    assert report.total_buyers == 2
    assert report.kol_buyers == 1 and report.smart_buyers == 1
    assert report.still_holding == 1  # bbb's newest row is the exit sell
    assert report.score == round((3.0 + 3.0 * 0.5) * 10)  # kol holding + smart exited
    assert report.chain_code == "rh"
    assert {b["name"] for b in report.buyers} == {"Alpha", "0xbbb"[:6]}
    exited = next(b for b in report.buyers if b["wallet"] == "0xbbb")
    assert exited["still_holding"] is False
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert calls[0]["params"]["mint"] == TOKEN


def test_analyze_soft_failure_body_fails_open(monkeypatch):
    # A 200 {"success": false} error envelope must fail OPEN (None), not
    # parse as "zero buyers" — the min-buyers gate would drop the alert.
    t = _make(monkeypatch)
    body = {"success": False, "error": {"code": "internal_error"}}
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client({"rh": _FakeResp(200, body)}, [])
    )
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is None


def test_analyze_falls_back_to_robinhood_alias_on_400(monkeypatch):
    t = _make(monkeypatch)
    calls = []
    script = {
        "rh": _FakeResp(400, text='{"error":{"code":"invalid_parameter"}}'),
        "robinhood": _FakeResp(200, _body(_tx("0xaaa", "kol"))),
    }
    monkeypatch.setattr(cs.httpx, "AsyncClient", _fake_client(script, calls))

    report = asyncio.run(t.analyze("robinhood", TOKEN))
    assert report is not None and report.total_buyers == 1
    assert [c["params"]["blockchain"] for c in calls] == ["rh", "robinhood"]
    # The working code is remembered — the next (uncached) token goes straight
    # to the alias instead of burning a 400 first.
    asyncio.run(t.analyze("robinhood", "0x" + "f" * 40))
    assert calls[-1]["params"]["blockchain"] == "robinhood"


def test_analyze_fails_open_on_auth_error(monkeypatch):
    t = _make(monkeypatch)
    monkeypatch.setattr(
        cs.httpx, "AsyncClient",
        _fake_client({"rh": _FakeResp(403, text='{"error":{"code":"invalid_api_key"}}')}, []),
    )
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is None


def test_analyze_fails_open_on_rate_limit_and_server_error(monkeypatch):
    t = _make(monkeypatch)
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client({"rh": _FakeResp(429, text="rate limited")}, [])
    )
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is None

    t2 = _make(monkeypatch)
    monkeypatch.setattr(
        cs.httpx, "AsyncClient", _fake_client({"rh": _FakeResp(500, text="boom")}, [])
    )
    assert asyncio.run(t2.analyze("robinhood", TOKEN)) is None


def test_analyze_fails_open_when_all_chain_codes_rejected(monkeypatch):
    t = _make(monkeypatch)
    calls = []
    script = {
        "rh": _FakeResp(400, text='{"error":{"code":"invalid_parameter"}}'),
        "robinhood": _FakeResp(400, text='{"error":{"code":"invalid_parameter"}}'),
    }
    monkeypatch.setattr(cs.httpx, "AsyncClient", _fake_client(script, calls))
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is None
    assert len(calls) == 2
    assert t._working_code == {}  # nothing latched on failure


def test_analyze_fails_open_on_network_exception(monkeypatch):
    t = _make(monkeypatch)

    class _BoomClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            raise OSError("connection reset")

    monkeypatch.setattr(cs.httpx, "AsyncClient", _BoomClient)
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is None


def test_working_code_invalidated_when_api_stops_accepting_it(monkeypatch):
    t = _make(monkeypatch)
    calls = []
    script = {"rh": _FakeResp(200, _body(_tx("0xaaa", "kol"))), "robinhood": _FakeResp(400)}
    monkeypatch.setattr(cs.httpx, "AsyncClient", _fake_client(script, calls))
    assert asyncio.run(t.analyze("robinhood", TOKEN)) is not None
    assert t._working_code["robinhood"] == "rh"

    # The API stops accepting "rh": the cached code must not be terminal —
    # the alias list is retried and the new working code latched.
    script["rh"] = _FakeResp(400, text='{"error":{"code":"invalid_parameter"}}')
    script["robinhood"] = _FakeResp(200, _body(_tx("0xaaa", "kol")))
    report = asyncio.run(t.analyze("robinhood", "0x" + "f" * 40))
    assert report is not None and report.total_buyers == 1
    assert t._working_code["robinhood"] == "robinhood"


def test_recheck_budget_caps_metered_calls(monkeypatch):
    t = _make(monkeypatch, cabalspy_max_checks=2)
    calls = []
    monkeypatch.setattr(
        cs.httpx, "AsyncClient",
        _fake_client({"rh": _FakeResp(200, _body(_tx("0xaaa", "kol")))}, calls),
    )
    key = ("robinhood", TOKEN)

    def _expire():
        ts, rep, cnt = t._cache[key]
        t._cache[key] = (ts - 9999, rep, cnt)

    asyncio.run(t.analyze("robinhood", TOKEN))
    _expire()
    asyncio.run(t.analyze("robinhood", TOKEN))
    _expire()
    third = asyncio.run(t.analyze("robinhood", TOKEN))  # budget spent

    assert len(calls) == 2  # third analyze served the stale report, no HTTP
    assert third is not None and third.total_buyers == 1


def test_analyze_caches_result(monkeypatch):
    t = _make(monkeypatch)
    calls = []
    monkeypatch.setattr(
        cs.httpx, "AsyncClient",
        _fake_client({"rh": _FakeResp(200, _body(_tx("0xaaa", "kol")))}, calls),
    )
    asyncio.run(t.analyze("robinhood", TOKEN))
    asyncio.run(t.analyze("robinhood", TOKEN.upper()))  # same token, case-insensitive
    assert len(calls) == 1


# ── gate ─────────────────────────────────────────────────────────────
def test_passes_gate(monkeypatch):
    t = _make(monkeypatch, cabalspy_min_buyers=2)
    two = CabalReport(score=60, total_buyers=2, kol_buyers=2, smart_buyers=0,
                      other_buyers=0, still_holding=2)
    one = CabalReport(score=30, total_buyers=1, kol_buyers=1, smart_buyers=0,
                      other_buyers=0, still_holding=1)
    assert t.passes(two) is True
    assert t.passes(one) is False
    assert t.passes(None) is True  # fail-open on missing data

    t0 = _make(monkeypatch, cabalspy_min_buyers=0)
    assert t0.passes(one) is True  # enrich-only mode never filters


# ── formatting / prompt propagation ──────────────────────────────────
def test_telegram_message_includes_cabalspy_section():
    from app.agents.launch_monitor.agent import TelegramService

    tg = TelegramService(bot_token="t", chat_id="1")
    token = {
        "base_symbol": "TEST", "quote_symbol": "WETH", "chain": "robinhood",
        "liquidity_usd": 10000, "market_cap": 0,
        "cabalspy": {
            "score": 75, "total_buyers": 2, "kol_buyers": 1, "smart_buyers": 1,
            "other_buyers": 0, "still_holding": 2,
            "buyers": [
                {"wallet": "0xaaa", "name": "Alpha", "twitter": "alpha",
                 "type": "kol", "still_holding": True},
                {"wallet": "0xbbb", "name": "Beta <x>", "twitter": "",
                 "type": "smart", "still_holding": False},
            ],
        },
    }
    message, _ = tg.format_token_message(token)
    assert "CabalSpy:" in message and "75/100" in message
    assert "1 KOL + 1 smart" in message
    assert "👑 Alpha (@alpha) ✊" in message
    assert "Beta &lt;x&gt;" in message  # HTML-escaped
    assert "💨 sold" in message


def test_claude_prompt_includes_cabalspy():
    from app.services.claude_code import ClaudeCodeAnalyzer

    prompt = ClaudeCodeAnalyzer.build_prompt({
        "base_symbol": "TEST", "chain": "robinhood", "liquidity_usd": 1000,
        "cabalspy": {
            "score": 60, "total_buyers": 1, "kol_buyers": 1, "smart_buyers": 0,
            "still_holding": 1,
            "buyers": [{"name": "Alpha", "type": "kol", "still_holding": True}],
        },
    })
    assert "legitimacy 60/100" in prompt
    assert "Alpha (kol)" in prompt


def test_hermes_payload_includes_cabalspy():
    from app.agents.launch_monitor.agent import LaunchMonitorAgent

    agent = LaunchMonitorAgent()
    token_data = {
        "base_symbol": "TEST", "chain": "robinhood", "liquidity_usd": 1000,
        "market_cap": 0, "red_flags": [],
        "cabalspy": {
            "score": 80, "total_buyers": 3, "kol_buyers": 2, "smart_buyers": 1,
            "other_buyers": 0, "still_holding": 3, "buyers": [],
        },
    }
    payload = agent._build_hermes_alert_payload(token_data)
    assert payload["token"]["cabalspy"]["score"] == 80
    assert "CabalSpy legitimacy: 80/100" in payload["text"]


# ── scan wiring ──────────────────────────────────────────────────────
def test_scan_once_enriches_and_gates_on_cabalspy(monkeypatch):
    from app.agents.launch_monitor.agent import LaunchMonitorAgent

    class FakeRedis:
        def __init__(self):
            self.store = {}
            self.published = []

        async def get(self, key):
            return self.store.get(key)

        async def setex(self, key, ttl, value):
            self.store[key] = value

        async def publish(self, channel, payload):
            self.published.append(payload)

    scored_token = "0x" + "1" * 40
    bare_token = "0x" + "2" * 40

    def pair(addr, sym):
        return {
            "pairAddress": "pair_" + sym, "chainId": "robinhood",
            "baseToken": {"symbol": sym, "name": sym, "address": addr},
            "quoteToken": {"symbol": "WETH", "name": "WETH"},
            "liquidity": {"usd": 50000}, "priceUsd": "0.01",
            "volume": {"h24": 1000}, "fdv": 100000,
            "pairCreatedAt": None,
            "url": "https://dexscreener.com/robinhood/pair_" + sym,
            "info": {"socials": [{"type": "twitter", "url": "https://x.com/" + sym}], "websites": []},
        }

    agent = LaunchMonitorAgent()
    agent.mq._redis = FakeRedis()
    agent.telegram.enabled = False
    agent.config.CHAINS = ["robinhood"]
    agent.smart_wallets.enabled = False
    agent.claude_code.enabled = False

    class FakeCabal:
        min_buyers = 1

        def covers(self, chain):
            return chain == "robinhood"

        def passes(self, report):
            return report is not None and report.total_buyers >= self.min_buyers

        async def analyze(self, chain, token_address):
            if token_address == scored_token:
                return CabalReport(score=60, total_buyers=2, kol_buyers=2, smart_buyers=0,
                                   other_buyers=0, still_holding=2,
                                   buyers=[{"wallet": "0xaaa", "name": "Alpha",
                                            "twitter": "", "type": "kol", "still_holding": True}])
            return CabalReport(score=0, total_buyers=0, kol_buyers=0, smart_buyers=0,
                               other_buyers=0, still_holding=0)

    agent.cabalspy = FakeCabal()

    async def fake_fetch(chain):
        return [pair(scored_token, "SCORED"), pair(bare_token, "BARE")]
    agent._fetch_new_pairs = fake_fetch

    async def no_profile(username):
        return None
    agent.twitter.get_profile = no_profile

    asyncio.run(agent._scan_once())

    published = [json.loads(p) for p in agent.mq._redis.published]
    syms = [p["data"]["base_symbol"] for p in published]
    assert syms == ["SCORED"]  # BARE filtered by min-buyers gate
    assert published[0]["data"]["cabalspy"]["score"] == 60
