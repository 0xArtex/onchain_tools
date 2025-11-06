from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import settings
from ..logging_config import configure_logging, logger
from ..mq.redis_mq import RedisMessageQueue


class TokenRequest(BaseModel):
    contract_address: str
    chain: str


class WalletRequest(TokenRequest):
    min_holding_percentage: Optional[float] = 1.0


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=settings.app_name)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    mq = RedisMessageQueue()

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "env": settings.environment}

    @app.post("/v1/wallet/analysis")
    async def wallet_analysis(req: WalletRequest) -> dict:
        try:
            result = await mq.request(
                "wallet_analyzer:request",
                {"action": "check_top_wallets", **req.model_dump()},
                timeout=5.0,
            )
            return result
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Wallet analyzer timeout")

    @app.post("/v1/volume/analysis")
    async def volume_analysis(req: TokenRequest) -> dict:
        try:
            result = await mq.request(
                "volume_bot:request",
                {"action": "analyze_volume", **req.model_dump()},
                timeout=5.0,
            )
            return result
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Volume bot timeout")

    @app.post("/v1/twitter/analysis")
    async def twitter_analysis(req: TokenRequest) -> dict:
        try:
            result = await mq.request(
                "twitter_screener:request",
                {"action": "analyze_sentiment", **req.model_dump()},
                timeout=5.0,
            )
            return result
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Twitter screener timeout")

    return app


app = create_app()
