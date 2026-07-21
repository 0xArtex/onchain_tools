import asyncio

from app.services import smart_wallets as sw
from app.services.smart_wallets import SmartWalletTracker, classify_wallet, parse_wallets

EVM = "0x" + "a" * 40
EVM2 = "0x" + "b" * 40
EVM3 = "0x" + "c" * 40
EVM4 = "0x" + "d" * 40
TOKEN_EVM = "0x" + "e" * 40
SOL = "So11111111111111111111111111111111111111112"
SOL2 = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _make(monkeypatch, **over):
    defaults = dict(
        launch_smart_wallets="",
        launch_min_smart_wallets=0,
        launch_min_smart_wallet_pct=0.0,
        launch_smart_wallet_refresh_seconds=60,
        helius_api_key=None,
        alchemy_api_key=None,
    )
    defaults.update(over)
    for k, v in defaults.items():
        monkeypatch.setattr(sw.settings, k, v)
    return SmartWalletTracker()


# ── address parsing ──────────────────────────────────────────────────
def test_classify_wallet():
    assert classify_wallet(EVM) == "evm"
    assert classify_wallet("0xAbC" + "1" * 37) == "evm"   # mixed-case hex still EVM
    assert classify_wallet(SOL) == "solana"
    assert classify_wallet("0X" + "a" * 40) is None        # wrong 0x case
    assert classify_wallet("not-an-address!!") is None
    assert classify_wallet("") is None


def test_parse_wallets_normalizes_dedupes_and_skips_junk():
    raw = f"0x{'A'*40} , 0x{'a'*40}\n{SOL} garbage!!"
    parsed = parse_wallets(raw)
    assert parsed["evm"] == ["0x" + "a" * 40]   # lowercased + deduped
    assert parsed["solana"] == [SOL]


# ── enable / threshold logic ─────────────────────────────────────────
def test_disabled_when_no_wallets(monkeypatch):
    assert _make(monkeypatch).enabled is False


def test_min_count_defaults_to_one_once_wallets_set(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=EVM)
    assert t.enabled is True
    assert t.min_count == 1


def test_required_for_uses_percentage(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=f"{EVM} {EVM2} {EVM3} {EVM4}",
              launch_min_smart_wallet_pct=50)
    assert t.required_for("base") == 2          # ceil(50% of 4)


def test_passes_fail_open_when_data_unavailable(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=EVM)
    passed, count, required = t.passes("base", None)
    assert passed is True and count == -1


def test_passes_threshold(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=f"{EVM} {EVM2}", launch_min_smart_wallets=2)
    assert t.passes("base", {EVM})[0] is False
    assert t.passes("base", {EVM, EVM2})[0] is True


# ── holders_of ───────────────────────────────────────────────────────
def test_holders_of_none_when_chain_unhealthy(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=EVM)
    assert asyncio.run(t.holders_of("base", TOKEN_EVM)) is None


def test_holders_of_set_when_healthy_case_insensitive(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=EVM)
    t._healthy = {"base": True}
    t._map = {"base": {TOKEN_EVM: {EVM}}}
    assert asyncio.run(t.holders_of("base", TOKEN_EVM.upper())) == {EVM}  # held
    assert asyncio.run(t.holders_of("base", "0x" + "f" * 40)) == set()    # healthy, not held


# ── provider response parsing ────────────────────────────────────────
class _FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def _fake_client(response_data):
    class _FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            return _FakeResp(response_data)

    return _FakeClient


def test_fetch_evm_holdings_keeps_only_nonzero(monkeypatch):
    data = {"result": {"tokenBalances": [
        {"contractAddress": "0x" + "1" * 40, "tokenBalance": "0x01"},
        {"contractAddress": "0x" + "2" * 40, "tokenBalance": "0x0"},
        {"contractAddress": "0x" + "3" * 40, "tokenBalance": "0x" + "0" * 64},
    ], "pageKey": None}}
    monkeypatch.setattr(sw.httpx, "AsyncClient", _fake_client(data))
    t = _make(monkeypatch, launch_smart_wallets=EVM, alchemy_api_key="k")
    held = asyncio.run(t._fetch_evm_holdings("base", EVM))
    assert held == {"0x" + "1" * 40}


def test_fetch_solana_holdings_uses_raw_amount(monkeypatch):
    # MINT_A: uiAmount is null (valid edge case) but raw amount is positive -> held.
    # MINT_B: raw amount 0 -> not held.
    data = {"result": {"value": [
        {"account": {"data": {"parsed": {"info": {"mint": "MINT_A", "tokenAmount": {"amount": "5000000", "uiAmount": None}}}}}},
        {"account": {"data": {"parsed": {"info": {"mint": "MINT_B", "tokenAmount": {"amount": "0", "uiAmount": 0}}}}}},
    ]}}
    monkeypatch.setattr(sw.httpx, "AsyncClient", _fake_client(data))
    t = _make(monkeypatch, launch_smart_wallets=SOL, helius_api_key="k")
    mints = asyncio.run(t._fetch_solana_holdings(SOL))
    assert mints == {"MINT_A"}


def test_fetch_raises_on_jsonrpc_error_body(monkeypatch):
    # JSON-RPC errors return HTTP 200 with an `error` body; both providers must raise
    # so the refresh counts a failure and the chain fails OPEN rather than "0 held".
    import pytest
    err = {"jsonrpc": "2.0", "id": 1, "error": {"code": 429, "message": "rate limit exceeded"}}
    monkeypatch.setattr(sw.httpx, "AsyncClient", _fake_client(err))
    t = _make(monkeypatch, launch_smart_wallets=f"{SOL} {EVM}", helius_api_key="h", alchemy_api_key="a")
    with pytest.raises(RuntimeError):
        asyncio.run(t._fetch_evm_holdings("base", EVM))
    with pytest.raises(RuntimeError):
        asyncio.run(t._fetch_solana_holdings(SOL))


def test_refresh_jsonrpc_error_keeps_chain_unhealthy_and_fails_open(monkeypatch):
    err = {"error": {"message": "rate limit exceeded"}}
    monkeypatch.setattr(sw.httpx, "AsyncClient", _fake_client(err))
    t = _make(monkeypatch, launch_smart_wallets=EVM, alchemy_api_key="a")
    asyncio.run(t.refresh_if_stale())
    assert t._healthy.get("base") is False
    assert asyncio.run(t.holders_of("base", TOKEN_EVM)) is None
    assert t.passes("base", None)[0] is True


def test_below_threshold_fails_open_when_data_incomplete(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=f"{EVM} {EVM2}", launch_min_smart_wallets=2)
    t._complete = {"base": False}                 # some wallet fetches failed
    assert t.passes("base", {EVM})[0] is True      # 1/2 but incomplete -> fail open
    t._complete = {"base": True}                  # full picture
    assert t.passes("base", {EVM})[0] is False     # 1/2 with complete data -> filtered


# ── refresh ──────────────────────────────────────────────────────────
def test_alchemy_networks_cover_every_evm_chain():
    # Every EVM chain the tracker refreshes needs an Alchemy network slug, or
    # _fetch_evm_holdings KeyErrors on it.
    for chain in sw._EVM_CHAINS:
        assert chain in sw._ALCHEMY_NETWORKS
    assert sw._ALCHEMY_NETWORKS["robinhood"] == "robinhood-mainnet"


def test_refresh_builds_map_and_marks_health(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=f"{SOL} {EVM}", helius_api_key="h", alchemy_api_key="a")

    async def fake_sol(w):
        return {"MINT_A"}

    async def fake_evm(chain, w):
        return {TOKEN_EVM} if chain == "base" else set()

    t._fetch_solana_holdings = fake_sol
    t._fetch_evm_holdings = fake_evm
    asyncio.run(t.refresh_if_stale())

    assert t._healthy["solana"] is True
    assert t._healthy["base"] is True
    assert t._healthy["bsc"] is True            # call succeeded even though empty
    assert t._healthy["robinhood"] is True      # EVM wallets are checked here too
    assert SOL in t._map["solana"]["MINT_A"]
    assert EVM in t._map["base"][TOKEN_EVM]


def test_refresh_skips_chains_disabled_in_env(monkeypatch):
    # ROBINHOOD=false must disable the chain end to end: the scan loop skips it
    # AND the tracker must not burn Alchemy quota polling holdings there.
    monkeypatch.setattr(sw.settings, "robinhood", False)
    t = _make(monkeypatch, launch_smart_wallets=f"{SOL} {EVM}", helius_api_key="h", alchemy_api_key="a")

    fetched_chains = []

    async def fake_sol(w):
        return set()

    async def fake_evm(chain, w):
        fetched_chains.append(chain)
        return set()

    t._fetch_solana_holdings = fake_sol
    t._fetch_evm_holdings = fake_evm
    asyncio.run(t.refresh_if_stale())

    assert "robinhood" not in fetched_chains
    assert set(fetched_chains) == {"base", "bsc"}


def test_refresh_failure_marks_chain_unhealthy_and_fails_open(monkeypatch):
    t = _make(monkeypatch, launch_smart_wallets=EVM, alchemy_api_key="a")

    async def boom(chain, w):
        raise RuntimeError("provider down")

    t._fetch_evm_holdings = boom
    asyncio.run(t.refresh_if_stale())

    assert t._healthy.get("base") is False
    assert asyncio.run(t.holders_of("base", TOKEN_EVM)) is None
    assert t.passes("base", None)[0] is True    # fail open


# ── end-to-end filter inside _scan_once ──────────────────────────────
def test_scan_once_filters_tokens_by_smart_wallet_holdings(monkeypatch):
    from app.agents.launch_monitor.agent import LaunchMonitorAgent

    class FakeRedis:
        def __init__(self):
            self.store = {}
            self.published = []

        async def get(self, key):
            return self.store.get(key)

        async def setex(self, key, ttl, value):
            assert isinstance(ttl, int)
            self.store[key] = value

        async def publish(self, channel, payload):
            self.published.append(payload)

    held_token = "0x" + "1" * 40
    missed_token = "0x" + "2" * 40

    def pair(addr, sym):
        return {
            "pairAddress": "pair_" + sym, "chainId": "base",
            "baseToken": {"symbol": sym, "name": sym, "address": addr},
            "quoteToken": {"symbol": "WETH", "name": "WETH"},
            "liquidity": {"usd": 50000}, "priceUsd": "0.01",
            "volume": {"h24": 1000}, "fdv": 100000,
            "pairCreatedAt": None,  # no timestamp -> kept via TOP_N_FOR_NO_TIME
            "url": "https://dexscreener.com/base/pair_" + sym,
            "info": {"socials": [{"type": "twitter", "url": "https://x.com/" + sym}], "websites": []},
        }

    agent = LaunchMonitorAgent()
    agent.mq._redis = FakeRedis()
    agent.telegram.enabled = False
    agent.config.CHAINS = ["base"]

    # Force the tracker on, with a known holdings map (held_token held by 1 wallet)
    sw_t = agent.smart_wallets
    sw_t.enabled = True
    sw_t.min_count = 1
    sw_t.min_pct = 0.0
    sw_t.wallets = {"solana": [], "evm": [EVM]}
    sw_t._healthy = {"base": True, "bsc": True, "solana": True}
    sw_t._map = {"base": {held_token: {EVM}}, "bsc": {}, "solana": {}}

    async def no_refresh():
        return None
    sw_t.refresh_if_stale = no_refresh

    async def fake_fetch(chain):
        return [pair(held_token, "HELD"), pair(missed_token, "MISS")]
    agent._fetch_new_pairs = fake_fetch

    async def no_profile(username):
        return None
    agent.twitter.get_profile = no_profile

    asyncio.run(agent._scan_once())

    # mq.publish JSON-encodes the payload before it reaches Redis
    import json
    published = [json.loads(p) for p in agent.mq._redis.published]
    published_syms = [p["data"]["base_symbol"] for p in published]
    assert published_syms == ["HELD"]   # only the smart-wallet-held token alerted
    assert published[0]["data"]["smart_wallet_count"] == 1
