import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from cachetools import TTLCache


class ThrottlingMiddleware(BaseMiddleware):
    """Drops updates from a user that arrive faster than `rate` seconds apart."""

    def __init__(self, rate: float = 0.7) -> None:
        self._rate = rate
        self._cache: TTLCache[int, float] = TTLCache(maxsize=10_000, ttl=rate)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            if user.id in self._cache:
                return None
            self._cache[user.id] = time.monotonic()
        return await handler(event, data)
