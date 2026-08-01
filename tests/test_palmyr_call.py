import asyncio

from app.services.palmyr_call import PalmyrCallNotifier


class FakeSettings:
    palmyr_call_enabled = True
    palmyr_call_phone_id = "phone_123"
    palmyr_call_to = "+15551234567"
    palmyr_call_verdicts = "WATCH,APE,BUY"
    palmyr_call_min_confidence = 0
    palmyr_call_bin = "palmyr"
    palmyr_call_script = ""
    palmyr_call_timeout_seconds = 20


class FakeResult:
    verdict = "APE"
    confidence = 82
    summary = "Strong traction."


TOKEN = {
    "base_symbol": "TEST",
    "chain": "base",
    "token_address": "0x" + "e" * 40,
    "dex_url": "https://dexscreener.com/base/0xpair",
}


def test_disabled_without_phone_config():
    class Missing(FakeSettings):
        palmyr_call_phone_id = ""

    notifier = PalmyrCallNotifier(Missing())
    assert notifier.enabled is False
    assert "disabled" in notifier.describe()


def test_should_call_for_not_skip_verdicts_only():
    notifier = PalmyrCallNotifier(FakeSettings())
    assert notifier.should_call("WATCH", 10) is True
    assert notifier.should_call("APE", 10) is True
    assert notifier.should_call("BUY", 10) is True
    assert notifier.should_call("SKIP", 99) is False


def test_should_call_respects_min_confidence():
    class Conf(FakeSettings):
        palmyr_call_min_confidence = 75

    notifier = PalmyrCallNotifier(Conf())
    assert notifier.should_call("APE", 74) is False
    assert notifier.should_call("APE", 75) is True


def test_build_cmd_direct_palmyr_bin():
    notifier = PalmyrCallNotifier(FakeSettings())
    cmd = notifier.build_cmd(FakeResult(), TOKEN)
    assert cmd[:4] == ["palmyr", "phone", "call", "--id"]
    assert "phone_123" in cmd
    assert "+15551234567" in cmd
    tts = cmd[cmd.index("--tts") + 1]
    assert "APE" in tts and "TEST" in tts and TOKEN["token_address"] in tts


def test_build_cmd_node_script_mode():
    class Node(FakeSettings):
        palmyr_call_bin = "/usr/bin/node"
        palmyr_call_script = "/usr/lib/node_modules/@palmyr/cli/dist/cli.js"

    notifier = PalmyrCallNotifier(Node())
    cmd = notifier.build_cmd(FakeResult(), TOKEN)
    assert cmd[:3] == ["/usr/bin/node", "/usr/lib/node_modules/@palmyr/cli/dist/cli.js", "phone"]


def test_notify_invokes_cli_for_configured_verdict(monkeypatch):
    calls = []

    class Proc:
        returncode = 0

        async def communicate(self):
            return b'{"ok":true}', b""

    async def fake_exec(*cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return Proc()

    monkeypatch.setattr("app.services.palmyr_call.asyncio.create_subprocess_exec", fake_exec)
    notifier = PalmyrCallNotifier(FakeSettings())
    result = asyncio.run(notifier.notify(FakeResult(), TOKEN))
    assert result.ok is True
    assert calls
    assert calls[0][1]["stdout"] == asyncio.subprocess.PIPE


def test_notify_skips_unconfigured_verdict(monkeypatch):
    calls = []

    async def fake_exec(*cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("should not spawn")

    monkeypatch.setattr("app.services.palmyr_call.asyncio.create_subprocess_exec", fake_exec)
    notifier = PalmyrCallNotifier(FakeSettings())

    class Skip(FakeResult):
        verdict = "SKIP"

    result = asyncio.run(notifier.notify(Skip(), TOKEN))
    assert result.ok is False
    assert result.skipped is True
    assert calls == []
