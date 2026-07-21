"""Claude Code token-analysis service.

Runs a local headless Claude Code session (``claude -p``) per token alert as a
second-stage research step — the local sibling of the Hermes webhook forwarder.
Claude Code researches the token (token-research / web skills) and returns a
strict-JSON verdict (SKIP / WATCH / APE / BUY) that the launch monitor posts
back to Telegram as a reply to the original alert and publishes on the MQ.

Design notes:
  - OFF by default: analysis spawns real (billed) Claude Code sessions, so it
    only runs when CLAUDE_CODE_ENABLED=true and the CLI binary is found.
  - Bounded: per-run --max-budget-usd cap, wall-clock timeout, and a
    max-concurrent semaphore so a burst of alerts can't fork-bomb sessions.
  - Headless-safe: --permission-mode dontAsk (denied tools fail closed instead
    of hanging on a prompt) with an allow-list of research tools, and
    --json-schema so the final answer is machine-parseable.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from dataclasses import dataclass, field

from ..config import settings
from ..logging_config import logger

# Verdict contract, enforced by Claude Code via --json-schema.
VERDICT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["SKIP", "WATCH", "APE", "BUY"]},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "confidence", "summary", "reasons", "risks"],
}


@dataclass
class AnalysisResult:
    ok: bool
    verdict: str = ""
    confidence: int = 0
    summary: str = ""
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    num_turns: int = 0
    session_id: str = ""
    model: str = ""
    error: str = ""


class ClaudeCodeAnalyzer:
    def __init__(self) -> None:
        self.configured = bool(getattr(settings, "claude_code_enabled", False))
        self.model = getattr(settings, "claude_code_model", "claude-opus-4-8")
        self.bin = getattr(settings, "claude_code_bin", "claude") or "claude"
        self.timeout = int(getattr(settings, "claude_code_timeout_seconds", 600) or 600)
        self.max_budget_usd = float(getattr(settings, "claude_code_max_budget_usd", 1.0) or 0)
        self.permission_mode = getattr(settings, "claude_code_permission_mode", "dontAsk") or "dontAsk"
        self.allowed_tools = (getattr(settings, "claude_code_allowed_tools", "") or "").strip()
        self.effort = (getattr(settings, "claude_code_effort", "") or "").strip()
        self.workdir = (getattr(settings, "claude_code_workdir", "") or "").strip() or None
        self.max_concurrent = max(1, int(getattr(settings, "claude_code_max_concurrent", 1) or 1))
        self.max_pending = max(1, int(getattr(settings, "claude_code_max_pending", 3) or 3))
        self._sem = asyncio.Semaphore(self.max_concurrent)
        # Queued + running analyses. Alerts beyond max_pending are skipped
        # outright: a verdict hours late is full cost for zero utility.
        self._pending = 0

        # Resolve the CLI once. On Windows the PATH entry is a .cmd/.ps1 shim;
        # shutil.which honors PATHEXT and returns the executable variant.
        self.bin_path = shutil.which(self.bin) if self.configured else None
        self.enabled = self.configured and bool(self.bin_path)

    # ── introspection ────────────────────────────────────────────────
    def describe(self) -> str:
        if not self.configured:
            return "ClaudeCode: disabled (CLAUDE_CODE_ENABLED not set)"
        if not self.bin_path:
            return f"ClaudeCode: ⚠️ CLAUDE_CODE_ENABLED but '{self.bin}' not found on PATH — analysis disabled"
        return (
            f"ClaudeCode: ENABLED | model={self.model}"
            + (f" effort={self.effort}" if self.effort else "")
            + f" | budget=${self.max_budget_usd:.2f}/run | timeout={self.timeout}s"
            + f" | max_concurrent={self.max_concurrent} | max_pending={self.max_pending}"
            + f" | permission_mode={self.permission_mode}"
        )

    # ── command / prompt construction ────────────────────────────────
    def _build_cmd(self) -> list[str]:
        cmd = [
            self.bin_path or self.bin,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(VERDICT_SCHEMA, separators=(",", ":")),
            "--model", self.model,
            "--permission-mode", self.permission_mode,
            "--no-session-persistence",
        ]
        if self.max_budget_usd > 0:
            cmd += ["--max-budget-usd", str(self.max_budget_usd)]
        if self.allowed_tools:
            cmd += ["--allowedTools", self.allowed_tools]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    @staticmethod
    def build_prompt(token_data: dict) -> str:
        sym = token_data.get("base_symbol", "?")
        name = token_data.get("base_name") or sym
        chain = token_data.get("chain", "?")
        liq = token_data.get("liquidity_usd", 0) or 0
        mcap = token_data.get("market_cap", 0) or 0
        volume = token_data.get("volume_24h")
        age = token_data.get("age_minutes")
        twitter = token_data.get("twitter_url", "")
        followers = token_data.get("twitter_followers")
        rug = token_data.get("rug_check")
        flags = token_data.get("red_flags", []) or []
        sw_count = token_data.get("smart_wallet_count")

        lines = [
            "You are a crypto token analyst running headlessly inside an automated launch-monitor pipeline.",
            "Research the token below and decide a verdict. Work autonomously — nobody can answer questions.",
            "",
            f"Token: ${sym} ({name}) on chain '{chain}'",
            f"Token CA: {token_data.get('token_address') or 'unknown'}",
            f"Pair: {token_data.get('pair_address') or token_data.get('pair_id') or 'unknown'}",
            f"Liquidity: ${liq:,.0f} | Market cap: ${mcap:,.0f}",
        ]
        if volume is not None:
            lines.append(f"Volume 24h: ${float(volume):,.0f}")
        if age is not None:
            lines.append(f"Age: {age} minutes")
        if twitter:
            suffix = f" ({followers:,} followers)" if followers else ""
            lines.append(f"Twitter/X: {twitter}{suffix}")
        if rug:
            lines.append(
                f"RugCheck: {rug.get('risk_level', '?')} ({rug.get('score_normalised', '?')}/100), "
                f"LP locked {rug.get('lp_locked_pct', 0):.0f}%"
            )
        if flags:
            normalized = [f[0] if isinstance(f, (list, tuple)) and f else str(f) for f in flags]
            lines.append("Scanner red flags: " + ", ".join(normalized))
        if sw_count:
            lines.append(f"Tracked smart wallets holding: {sw_count}")
        cabal = token_data.get("cabalspy")
        if cabal:
            names = ", ".join(
                f"{b.get('name') or b.get('wallet', '')[:10]} ({b.get('type', '?')}"
                f"{', sold' if not b.get('still_holding', True) else ''})"
                for b in (cabal.get("buyers") or [])[:8]
            )
            lines.append(
                f"CabalSpy labeled buyers: legitimacy {cabal.get('score', 0)}/100 — "
                f"{cabal.get('kol_buyers', 0)} KOL, {cabal.get('smart_buyers', 0)} smart, "
                f"{cabal.get('still_holding', 0)}/{cabal.get('total_buyers', 0)} still holding"
                + (f". Buyers: {names}" if names else "")
            )
        lines += [
            f"DexScreener: {token_data.get('dex_url', '')}",
            "",
            "Use the /token-research skill if available, plus web search/fetch, to assess:",
            "team/deployer history, holder distribution, liquidity lock, socials authenticity,",
            "narrative strength, and momentum. Cross-check the contract address on-chain.",
            "",
            "Verdict rules:",
            "  SKIP  = weak/scam/no edge.",
            "  WATCH = interesting; worth monitoring, not buying yet.",
            "  APE   = strong short-term momentum play, act fast, higher risk tolerated.",
            "  BUY   = high-conviction entry with fundamentals.",
            "Be skeptical by default: most new launches deserve SKIP.",
            "",
            "Your final answer MUST be only the JSON object matching the enforced schema",
            "(verdict, confidence 0-100, summary, reasons[], risks[]).",
        ]
        return "\n".join(lines)

    # ── execution ────────────────────────────────────────────────────
    async def analyze(self, token_data: dict) -> AnalysisResult | None:
        """Run one headless Claude Code analysis.

        Returns None when disabled or when the backlog is full (skipped).
        """
        if not self.enabled:
            return None
        sym = token_data.get("base_symbol", "?")
        if self._pending >= self.max_pending:
            logger.warning(
                f"ClaudeCode: skipping ${sym} — {self._pending} analyses already "
                f"pending (CLAUDE_CODE_MAX_PENDING={self.max_pending})"
            )
            return None
        self._pending += 1
        try:
            return await self._analyze_locked(token_data, sym)
        finally:
            self._pending -= 1

    async def _analyze_locked(self, token_data: dict, sym: str) -> AnalysisResult:
        prompt = self.build_prompt(token_data)
        async with self._sem:
            started = time.monotonic()
            logger.info(f"🤖 ClaudeCode: analyzing ${sym} with {self.model}…")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._build_cmd(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workdir,
                )
                try:
                    # Prompt goes via stdin: avoids Windows argv length limits
                    # and shell-quoting issues with arbitrary token metadata.
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(prompt.encode("utf-8")),
                        timeout=self.timeout,
                    )
                except asyncio.TimeoutError:
                    await self._kill_tree(proc)
                    logger.warning(f"ClaudeCode: ${sym} analysis timed out after {self.timeout}s")
                    return AnalysisResult(ok=False, model=self.model, error=f"timeout after {self.timeout}s")
            except Exception as e:
                logger.warning(f"ClaudeCode: failed to launch CLI: {e}")
                return AnalysisResult(ok=False, model=self.model, error=f"launch failed: {e}")

            duration = time.monotonic() - started
            out_text = (stdout or b"").decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not out_text:
                err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
                logger.warning(f"ClaudeCode: exit {proc.returncode} for ${sym}: {err_text[:300]}")
                return AnalysisResult(
                    ok=False, model=self.model, duration_seconds=duration,
                    error=f"exit {proc.returncode}: {err_text[:300]}",
                )
            return self._parse_output(out_text, duration, sym)

    @staticmethod
    async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
        """Kill the CLI process and all its children.

        On Windows the resolved binary is the claude.CMD shim, so the tracked
        process is a cmd.exe wrapper and the real session runs as its child.
        TerminateProcess (proc.kill) does not cascade — the orphaned session
        would keep running and billing — so take down the whole tree.
        """
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            except Exception as e:
                logger.debug(f"ClaudeCode: taskkill failed: {e}")
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass  # already gone (e.g. taskkill got it first)
        await proc.wait()

    def _parse_output(self, out_text: str, duration: float, sym: str) -> AnalysisResult:
        try:
            data = json.loads(out_text)
        except (ValueError, TypeError):
            logger.warning(f"ClaudeCode: non-JSON output for ${sym}: {out_text[:200]}")
            return AnalysisResult(
                ok=False, model=self.model, duration_seconds=duration,
                error=f"unparseable output: {out_text[:200]}",
            )

        cost = float(data.get("total_cost_usd") or 0.0)
        turns = int(data.get("num_turns") or 0)
        session_id = str(data.get("session_id") or "")

        if data.get("is_error"):
            err = str(data.get("result") or data.get("error") or "unknown error")
            logger.warning(f"ClaudeCode: ${sym} run errored: {err[:300]}")
            return AnalysisResult(
                ok=False, model=self.model, duration_seconds=duration,
                cost_usd=cost, num_turns=turns, session_id=session_id,
                error=err[:300],
            )

        # --json-schema runs expose the validated object; fall back to parsing
        # the result text so a CLI without that field still works.
        verdict_obj = data.get("structured_output")
        if not isinstance(verdict_obj, dict):
            try:
                verdict_obj = json.loads(data.get("result") or "")
            except (ValueError, TypeError):
                verdict_obj = None
        if not isinstance(verdict_obj, dict) or "verdict" not in verdict_obj:
            preview = str(data.get("result"))[:200]
            logger.warning(f"ClaudeCode: ${sym} returned no verdict JSON: {preview}")
            return AnalysisResult(
                ok=False, model=self.model, duration_seconds=duration,
                cost_usd=cost, num_turns=turns, session_id=session_id,
                error=f"no verdict in output: {preview}",
            )

        try:
            confidence = int(verdict_obj.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0
        result = AnalysisResult(
            ok=True,
            verdict=str(verdict_obj.get("verdict", "")).upper(),
            confidence=confidence,
            summary=str(verdict_obj.get("summary") or ""),
            reasons=[str(r) for r in (verdict_obj.get("reasons") or [])],
            risks=[str(r) for r in (verdict_obj.get("risks") or [])],
            cost_usd=cost,
            duration_seconds=duration,
            num_turns=turns,
            session_id=session_id,
            model=self.model,
        )
        logger.info(
            f"🤖 ClaudeCode: ${sym} → {result.verdict} ({result.confidence}%) "
            f"in {duration:.0f}s, {turns} turns, ${cost:.2f}"
        )
        return result


__all__ = ["ClaudeCodeAnalyzer", "AnalysisResult", "VERDICT_SCHEMA"]
