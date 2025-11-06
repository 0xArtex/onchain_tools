from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as redis

from ..config import settings
from ..logging_config import logger


MessageHandler = Callable[[dict], Awaitable[None]]


class RedisMessageQueue:
    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.redis_url
        self._redis: redis.Redis = redis.from_url(self._url, decode_responses=True)

    async def publish(self, channel: str, message: dict | str) -> None:
        payload = message if isinstance(message, str) else json.dumps(message)
        await self._redis.publish(channel, payload)

    async def subscribe(self, channel: str, handler: MessageHandler) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        logger.info(f"Subscribed to {channel}")

        try:
            async for raw in pubsub.listen():
                if raw.get("type") != "message":
                    continue
                data = raw.get("data")
                try:
                    msg = json.loads(data)
                except Exception:
                    msg = {"data": data}
                try:
                    await handler(msg)
                except Exception as exc:
                    logger.exception(f"Handler error on {channel}: {exc}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    async def request(self, queue: str, payload: dict, timeout: float = 5.0) -> dict:
        request_id = str(uuid.uuid4())
        response_channel = f"{queue}:response:{request_id}"

        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

        async def _once_handler(msg: dict) -> None:
            if not future.done():
                future.set_result(msg)

        # Start response subscription
        async def _listen_once() -> None:
            pubsub = self._redis.pubsub()
            await pubsub.subscribe(response_channel)
            try:
                async for raw in pubsub.listen():
                    if raw.get("type") != "message":
                        continue
                    data = raw.get("data")
                    try:
                        msg = json.loads(data)
                    except Exception:
                        msg = {"data": data}
                    await _once_handler(msg)
                    break
            finally:
                await pubsub.unsubscribe(response_channel)
                await pubsub.close()

        listener_task = asyncio.create_task(_listen_once())

        # Publish request
        envelope = {
            **payload,
            "request_id": request_id,
            "response_channel": response_channel,
        }
        await self.publish(queue, envelope)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            listener_task.cancel()

    async def respond(self, response_channel: str, message: dict) -> None:
        await self.publish(response_channel, message)


__all__ = ["RedisMessageQueue"]
