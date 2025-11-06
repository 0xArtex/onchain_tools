from __future__ import annotations

from ..base import BaseAgent
from ...logging_config import logger


class WalletAnalyzerAgent(BaseAgent):
    name = "wallet_analyzer"
    request_channel = "wallet_analyzer:request"

    async def on_start(self) -> None:
        logger.info("WalletAnalyzerAgent ready (stub)")

    async def on_stop(self) -> None:
        logger.info("WalletAnalyzerAgent stopped")

    async def handle_request(self, message: dict) -> dict:
        action = message.get("action", "check_top_wallets")
        if action == "check_top_wallets":
            return {
                "top_10_concentration": None,
                "smart_money_wallets": [],
                "recent_movements": [],
                "accumulation_score": None,
            }
        return {"unknown_action": action}

