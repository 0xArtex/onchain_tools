import asyncio
import json
import sys

from app.services import claude_code as cc
from app.services.claude_code import AnalysisResult, ClaudeCodeAnalyzer, VERDICT_SCHEMA

FAKE_BIN = "C:/fake/claude.cmd"

VERDICT = {
    "verdict": "WATCH",
    "confidence": 62,
    "summary": "Interesting narrative, thin liquidity.",
    "reasons": ["Active dev wallet", "Real X community"],
    "risks": ["LP unlock in 3 days"],
}

TOKEN = {
    "pair_id": "0xpair",
    "pair_address": "0xpair",
    "token_address": "0x" + "e" * 40,
    "chain": "base",
    "base_symbol": "TEST",
    "base_name": "Test Token",
    "quote_symbol": "WETH",
    "liquidity_usd": 25000.0,
    "market_cap": 100000,
    "volume_24h": 5000,
    "age_minutes": 12,
    "twitter_url": "https://x.com/test",
    "twitter_followers": 1234,
    "dex_url": "https://dexscreener.com/base/0xpair",
    "red_flags": [("Website is a YouTube link", "danger")],
    "smart_wallet_count": 2,
    "rug_check": None,
}


def _make(monkeypatch, *, which=FAKE_BIN, **over):
    defaults = dict(
        claude_code_enabled=True,
        claude_code_model="claude-opus-4-8",
        claude_code_bin="claude",
        claude_code_timeout_seconds=600,
        claude_code_max_budget_usd=1.0,
        claude_code_max_concurrent=1,
        claude_code_max_pending=3,
        claude_code_permission_mode="dontAsk",
        claude_code_allowed_tools="WebSearch,WebFetch,Read,Glob,Grep",
        claude_code_effort="",
        claude_code_workdir="",
    )
    defaults.update(over)
    for k, v in defaults.items():
        monkeypatch.setattr(cc.settings, k, v, raising=False)
    monkeypatch.setattr(cc.shutil, "which", lambda _b: which)
    return ClaudeCodeAnalyzer()


class _FakeProc:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 4242
        self._hang = hang
        self.killed = False
        self.stdin_payload = None

    async def communicate(self, input=None):
        self.stdin_payload = input
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _patch_subprocess(monkeypatch, proc):
    captured = {"calls": []}

    async def fake_exec(*cmd, **kwargs):
        captured["calls"].append(list(cmd))
        if cmd[0] != "taskkill":  # keep the CLI invocation, not the tree-kill
            captured["cmd"] = list(cmd)
            captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", fake_exec)
    return captured


def _cli_json(*, result=None, structured=None, is_error=False, cost=0.42):
    body = {
        "type": "result",
        "is_error": is_error,
        "result": result,
        "session_id": "sess-1",
        "total_cost_usd": cost,
        "num_turns": 7,
    }
    if structured is not None:
        body["structured_output"] = structured
    return json.dumps(body).encode()


# ── enablement ───────────────────────────────────────────────────────
def test_disabled_by_default(monkeypatch):
    t = _make(monkeypatch, claude_code_enabled=False)
    assert t.enabled is False
    assert asyncio.run(t.analyze(TOKEN)) is None


def test_enabled_but_binary_missing_disables_with_warning(monkeypatch):
    t = _make(monkeypatch, which=None)
    assert t.enabled is False
    assert "not found on PATH" in t.describe()


# ── command construction ─────────────────────────────────────────────
def test_build_cmd_contains_headless_safe_flags(monkeypatch):
    t = _make(monkeypatch)
    cmd = t._build_cmd()
    assert cmd[0] == FAKE_BIN
    assert "-p" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in cmd
    assert cmd[cmd.index("--max-budget-usd") + 1] == "1.0"
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch,Read,Glob,Grep"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == VERDICT_SCHEMA
    assert "--effort" not in cmd  # empty -> omitted


def test_build_cmd_optional_flags(monkeypatch):
    t = _make(monkeypatch, claude_code_model="claude-fable-5",
              claude_code_effort="high", claude_code_max_budget_usd=0)
    cmd = t._build_cmd()
    assert cmd[cmd.index("--model") + 1] == "claude-fable-5"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert "--max-budget-usd" not in cmd  # 0 -> uncapped, flag omitted


# ── prompt ───────────────────────────────────────────────────────────
def test_prompt_contains_key_token_facts():
    prompt = ClaudeCodeAnalyzer.build_prompt(TOKEN)
    assert "TEST" in prompt
    assert TOKEN["token_address"] in prompt
    assert "base" in prompt
    assert "https://dexscreener.com/base/0xpair" in prompt
    assert "Website is a YouTube link" in prompt
    assert "SKIP" in prompt and "WATCH" in prompt and "APE" in prompt and "BUY" in prompt


# ── output parsing ───────────────────────────────────────────────────
def test_analyze_uses_structured_output(monkeypatch):
    t = _make(monkeypatch)
    proc = _FakeProc(stdout=_cli_json(result="prose", structured=VERDICT))
    captured = _patch_subprocess(monkeypatch, proc)

    result = asyncio.run(t.analyze(TOKEN))

    assert result.ok is True
    assert result.verdict == "WATCH"
    assert result.confidence == 62
    assert result.cost_usd == 0.42
    assert result.session_id == "sess-1"
    # Prompt travels over stdin, not argv (Windows argv limits / quoting).
    assert proc.stdin_payload and b"TEST" in proc.stdin_payload
    assert captured["kwargs"]["stdin"] == asyncio.subprocess.PIPE


def test_analyze_falls_back_to_result_text_json(monkeypatch):
    t = _make(monkeypatch)
    proc = _FakeProc(stdout=_cli_json(result=json.dumps(VERDICT)))
    _patch_subprocess(monkeypatch, proc)
    result = asyncio.run(t.analyze(TOKEN))
    assert result.ok is True and result.verdict == "WATCH"


def test_analyze_reports_cli_error(monkeypatch):
    t = _make(monkeypatch)
    proc = _FakeProc(stdout=_cli_json(result="budget exceeded", is_error=True))
    _patch_subprocess(monkeypatch, proc)
    result = asyncio.run(t.analyze(TOKEN))
    assert result.ok is False
    assert "budget exceeded" in result.error


def test_analyze_handles_non_json_output(monkeypatch):
    t = _make(monkeypatch)
    proc = _FakeProc(stdout=b"OAuth token expired, run /login")
    _patch_subprocess(monkeypatch, proc)
    result = asyncio.run(t.analyze(TOKEN))
    assert result.ok is False
    assert "unparseable" in result.error


def test_analyze_timeout_kills_process_tree(monkeypatch):
    t = _make(monkeypatch, claude_code_timeout_seconds=1)
    proc = _FakeProc(hang=True)
    captured = _patch_subprocess(monkeypatch, proc)
    result = asyncio.run(t.analyze(TOKEN))
    assert result.ok is False
    assert "timeout" in result.error
    assert proc.killed is True
    if sys.platform == "win32":
        # proc.kill() alone only hits the claude.CMD cmd.exe shim and orphans
        # the real (billing) session — the whole tree must go via taskkill.
        assert any(c[0] == "taskkill" and "/T" in c for c in captured["calls"])


def test_analyze_skips_when_backlog_full(monkeypatch):
    t = _make(monkeypatch, claude_code_max_pending=1)
    t._pending = 1  # one analysis already queued/running
    captured = _patch_subprocess(monkeypatch, _FakeProc(stdout=_cli_json(structured=VERDICT)))
    assert asyncio.run(t.analyze(TOKEN)) is None
    assert captured["calls"] == []  # no session spawned
    assert t._pending == 1          # skip must not touch the counter


def test_pending_counter_resets_after_run(monkeypatch):
    t = _make(monkeypatch)
    _patch_subprocess(monkeypatch, _FakeProc(stdout=_cli_json(structured=VERDICT)))
    result = asyncio.run(t.analyze(TOKEN))
    assert result.ok is True
    assert t._pending == 0


def test_default_allowed_tools_are_web_only():
    # Security default: the prompt embeds untrusted token metadata, so the
    # allow-list must not include local-file tools (Read/Glob/Grep).
    from app.config import Settings
    assert Settings(_env_file=None).claude_code_allowed_tools == "WebSearch,WebFetch"


# ── launch-monitor wiring ────────────────────────────────────────────
def test_run_claude_analysis_publishes_and_replies(monkeypatch):
    from app.agents.launch_monitor.agent import LaunchMonitorAgent

    agent = LaunchMonitorAgent()

    published = []

    class FakeMQ:
        async def publish(self, channel, payload):
            published.append((channel, payload))

    class FakeAnalyzer:
        enabled = True

        async def analyze(self, token_data):
            return AnalysisResult(
                ok=True, verdict="APE", confidence=80, summary="Strong momentum.",
                reasons=["r1"], risks=["k1"], cost_usd=0.5, duration_seconds=90,
                model="claude-fable-5",
            )

    replies = []

    async def fake_reply(chat_id, text, reply_to=None):
        replies.append((chat_id, text, reply_to))

    agent.mq = FakeMQ()
    agent.claude_code = FakeAnalyzer()
    agent.telegram.enabled = True
    agent.telegram.chat_id = "42"
    agent.telegram.send_reply = fake_reply

    asyncio.run(agent._run_claude_analysis(TOKEN, {"message_id": 7}))

    assert published and published[0][0] == "token_analysis"
    assert published[0][1]["data"]["verdict"] == "APE"
    assert replies and replies[0][2] == 7
    assert "APE" in replies[0][1] and "🦍" in replies[0][1]


def test_run_claude_analysis_failed_run_publishes_but_no_telegram(monkeypatch):
    from app.agents.launch_monitor.agent import LaunchMonitorAgent

    agent = LaunchMonitorAgent()
    published = []

    class FakeMQ:
        async def publish(self, channel, payload):
            published.append((channel, payload))

    class FakeAnalyzer:
        enabled = True

        async def analyze(self, token_data):
            return AnalysisResult(ok=False, error="timeout after 600s", model="claude-opus-4-8")

    replies = []

    async def fake_reply(chat_id, text, reply_to=None):
        replies.append(text)

    agent.mq = FakeMQ()
    agent.claude_code = FakeAnalyzer()
    agent.telegram.enabled = True
    agent.telegram.chat_id = "42"
    agent.telegram.send_reply = fake_reply

    asyncio.run(agent._run_claude_analysis(TOKEN, None))

    assert published[0][1]["data"]["ok"] is False
    assert replies == []
