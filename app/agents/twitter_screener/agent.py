from __future__ import annotations

from ..base import BaseAgent
from ...logging_config import logger


class TwitterScreenerAgent(BaseAgent):
    name = "twitter_screener"
    request_channel = "twitter_screener:request"

    async def on_start(self) -> None:
        logger.info("TwitterScreenerAgent ready (stub)")

    async def on_stop(self) -> None:
        logger.info("TwitterScreenerAgent stopped")

    async def handle_request(self, message: dict) -> dict:
        action = message.get("action", "analyze_sentiment")
        if action == "analyze_sentiment":
            return {
                "sentiment_score": None,
                "influencer_mentions": [],
                "tweet_velocity": None,
                "follower_count": None,
                "follower_quality": None,
                "score": None,
            }
        return {"unknown_action": action}

