import asyncio

from app.agents.launch_monitor import agent as launch_agent
from app.agents.launch_monitor.agent import LaunchConfig, LaunchMonitorAgent


def test_all_chains_enabled_by_default():
    cfg = LaunchConfig()
    # Order is preserved as defined in _ALL_CHAINS
    assert cfg.CHAINS == ["bsc", "solana", "base"]


def test_disabling_one_chain_removes_only_that_chain(monkeypatch):
    monkeypatch.setattr(launch_agent.settings, "bsc", False)
    cfg = LaunchConfig()
    assert "bsc" not in cfg.CHAINS
    assert cfg.CHAINS == ["solana", "base"]


def test_disabling_all_chains_yields_empty_list(monkeypatch):
    monkeypatch.setattr(launch_agent.settings, "solana", False)
    monkeypatch.setattr(launch_agent.settings, "base", False)
    monkeypatch.setattr(launch_agent.settings, "bsc", False)
    cfg = LaunchConfig()
    assert cfg.CHAINS == []


def test_min_liquidity_is_read_from_settings(monkeypatch):
    monkeypatch.setattr(launch_agent.settings, "launch_min_liquidity", 25000.0)
    cfg = LaunchConfig()
    assert cfg.MIN_LIQUIDITY == 25000.0


def test_threshold_and_toggle_defaults_preserve_prior_behavior():
    cfg = LaunchConfig()
    assert cfg.MIN_LIQUIDITY == 6000.0
    assert cfg.MIN_MARKET_CAP == 0.0
    assert cfg.MAX_MARKET_CAP == 0.0
    assert cfg.MIN_TWITTER_FOLLOWERS == 0
    assert cfg.REQUIRE_TWITTER is True
    assert cfg.REQUIRE_WEBSITE is False
    assert cfg.POLL_SECONDS == 30
    assert cfg.LOOKBACK_HOURS == 1.0
    assert cfg.TOP_N_FOR_NO_TIME == 50


def test_cache_ttl_is_integer():
    # Regression: LOOKBACK_HOURS is a float, so the derived dedup TTL must be
    # coerced to int. A float TTL makes Redis SETEX fail, _mark_seen silently
    # errors, and tokens re-alert on every scan.
    agent = LaunchMonitorAgent()
    assert isinstance(agent._cache_ttl, int)


def test_mark_seen_sends_integer_ttl_to_redis():
    # Pins the Redis boundary: SETEX must receive an int even if _cache_ttl
    # somehow holds a float.
    class FakeRedis:
        def __init__(self):
            self.calls = []

        async def setex(self, key, ttl, value):
            self.calls.append((key, ttl, value))

    class FakeMQ:
        def __init__(self):
            self._redis = FakeRedis()

    agent = object.__new__(LaunchMonitorAgent)
    agent.mq = FakeMQ()
    agent._cache_ttl = 3900.0  # float, as a float LOOKBACK_HOURS would produce

    asyncio.run(agent._mark_seen("base", "0xpair"))

    assert agent.mq._redis.calls, "setex was not called"
    _key, ttl, _value = agent.mq._redis.calls[0]
    assert isinstance(ttl, int), f"Redis SETEX TTL must be int, got {type(ttl).__name__}={ttl!r}"
