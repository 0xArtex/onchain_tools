from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

from ..logging_config import logger
from ..mq.redis_mq import RedisMessageQueue


class BaseAgent(ABC):
    name: str = "base"
    request_channel: str | None = None

    def __init__(self, mq: RedisMessageQueue | None = None) -> None:
        self.mq = mq or RedisMessageQueue()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        logger.info(f"Starting agent: {self.name}")
        await self.on_start()
        if self.request_channel:
            self._tasks.append(asyncio.create_task(self._serve_requests()))

    async def stop(self) -> None:
        logger.info(f"Stopping agent: {self.name}")
        for t in self._tasks:
            t.cancel()
        await self.on_stop()

    async def _serve_requests(self) -> None:
        assert self.request_channel is not None
        async def handler(message: dict) -> None:
            try:
                response_channel = message.get("response_channel")
                result = await self.handle_request(message)
                if response_channel:
                    await self.mq.respond(response_channel, {"status": "ok", "data": result})
            except Exception as exc:
                logger.exception(f"{self.name} handle_request failed: {exc}")
                response_channel = message.get("response_channel")
                if response_channel:
                    await self.mq.respond(response_channel, {"status": "error", "error": str(exc)})
        await self.mq.subscribe(self.request_channel, handler)

    @abstractmethod
    async def on_start(self) -> None:
        ...

    @abstractmethod
    async def on_stop(self) -> None:
        ...

    @abstractmethod
    async def handle_request(self, message: dict) -> dict:
        ...
