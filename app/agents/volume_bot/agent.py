from __future__ import annotations

from ..base import BaseAgent
from ...logging_config import logger


class VolumeBotAgent(BaseAgent):
    name = "volume_bot"
    request_channel = "volume_bot:request"

    async def on_start(self) -> None:
        logger.info("VolumeBotAgent ready (stub)")

    async def on_stop(self) -> None:
        logger.info("VolumeBotAgent stopped")

    async def handle_request(self, message: dict) -> dict:
        action = message.get("action", "analyze_volume")
        if action == "analyze_volume":
            return {
                "volume_24h": None,
                "is_suspicious": None,
                "buy_pressure_pct": None,
                "unique_traders": None,
                "volume_trend": None,
                "score": None,
            }
        return {"unknown_action": action}

