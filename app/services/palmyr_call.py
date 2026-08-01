"""Palmyr voice-call notifier for urgent Claude Code token verdicts."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from ..config import settings
from ..logging_config import logger


@dataclass
class PalmyrCallResult:
    ok: bool
    skipped: bool = False
    error: str = ""
    stdout: str = ""
    stderr: str = ""


class PalmyrCallNotifier:
    """Place a Palmyr voice call when a researched verdict is worth waking Z.

    The notifier is disabled unless all required env/config values are present:
    PALMYR_CALL_ENABLED=true, PALMYR_CALL_PHONE_ID, and PALMYR_CALL_TO.
    """

    def __init__(self, cfg: Any = settings) -> None:
        self.enabled_config = bool(getattr(cfg, "palmyr_call_enabled", False))
        self.phone_id = (getattr(cfg, "palmyr_call_phone_id", "") or "").strip()
        self.to = (getattr(cfg, "palmyr_call_to", "") or "").strip()
        verdicts = (getattr(cfg, "palmyr_call_verdicts", "WATCH,APE,BUY") or "WATCH,APE,BUY")
        self.verdicts = {v.strip().upper() for v in re.split(r"[,\s]+", verdicts) if v.strip()}
        try:
            self.min_confidence = int(getattr(cfg, "palmyr_call_min_confidence", 0) or 0)
        except (TypeError, ValueError):
            self.min_confidence = 0
        self.bin = (getattr(cfg, "palmyr_call_bin", "palmyr") or "palmyr").strip()
        self.script = (getattr(cfg, "palmyr_call_script", "") or "").strip()
        self.timeout = int(getattr(cfg, "palmyr_call_timeout_seconds", 20) or 20)
        self.enabled = self.enabled_config and bool(self.phone_id) and bool(self.to)

    def describe(self) -> str:
        if not self.enabled_config:
            return "PalmyrCall: disabled (PALMYR_CALL_ENABLED not set)"
        if not self.phone_id or not self.to:
            return "PalmyrCall: disabled (missing PALMYR_CALL_PHONE_ID or PALMYR_CALL_TO)"
        return (
            f"PalmyrCall: ENABLED | verdicts={sorted(self.verdicts)} "
            f"| min_confidence={self.min_confidence} | to={self._masked_phone(self.to)}"
        )

    def should_call(self, verdict: str, confidence: int) -> bool:
        return verdict.upper() in self.verdicts and int(confidence or 0) >= self.min_confidence

    def build_tts(self, result: Any, token_data: dict) -> str:
        sym = token_data.get("base_symbol", "unknown")
        chain = str(token_data.get("chain", "unknown")).upper()
        verdict = str(getattr(result, "verdict", "")).upper()
        confidence = int(getattr(result, "confidence", 0) or 0)
        summary = str(getattr(result, "summary", "") or "")
        token_address = token_data.get("token_address") or "unknown contract"
        dex_url = token_data.get("dex_url") or ""
        parts = [
            f"Urgent onchain alert. Claude Opus verdict is {verdict} with {confidence} percent confidence.",
            f"Token {sym} on {chain}.",
            f"Contract address: {token_address}.",
        ]
        if summary:
            parts.append(f"Summary: {summary}")
        if dex_url:
            parts.append("DexScreener link was sent in Telegram.")
        return " ".join(parts)[:1200]

    def build_cmd(self, result: Any, token_data: dict) -> list[str]:
        base = [self.bin]
        if self.script:
            base.append(self.script)
        return base + [
            "phone", "call",
            "--id", self.phone_id,
            "--to", self.to,
            "--tts", self.build_tts(result, token_data),
            "--json",
        ]

    async def notify(self, result: Any, token_data: dict) -> PalmyrCallResult:
        verdict = str(getattr(result, "verdict", "")).upper()
        confidence = int(getattr(result, "confidence", 0) or 0)
        sym = token_data.get("base_symbol", "?")
        if not self.enabled:
            return PalmyrCallResult(ok=False, skipped=True, error="not configured")
        if not self.should_call(verdict, confidence):
            return PalmyrCallResult(ok=False, skipped=True, error="verdict not configured for calls")

        cmd = self.build_cmd(result, token_data)
        logger.warning(f"☎️ PalmyrCall: calling operator for ${sym} {verdict} ({confidence}%)")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return PalmyrCallResult(ok=False, error=f"timeout after {self.timeout}s")
        except Exception as e:
            logger.warning(f"PalmyrCall: failed to launch CLI: {e}")
            return PalmyrCallResult(ok=False, error=f"launch failed: {e}")

        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            logger.warning(f"PalmyrCall: CLI exit {proc.returncode}: {err[:300] or out[:300]}")
            return PalmyrCallResult(ok=False, stdout=out, stderr=err, error=f"exit {proc.returncode}")
        logger.info(f"✅ PalmyrCall: call triggered for ${sym}")
        return PalmyrCallResult(ok=True, stdout=out, stderr=err)

    @staticmethod
    def _masked_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        if len(digits) <= 4:
            return "***"
        return f"***{digits[-4:]}"


__all__ = ["PalmyrCallNotifier", "PalmyrCallResult"]
