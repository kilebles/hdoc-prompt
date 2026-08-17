from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from loguru import logger


class UpdateLoggingMiddleware(BaseMiddleware):
    """Logs every incoming update at DEBUG level; keeps handlers noise-free."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            logger.debug("Update {} received: {}", event.update_id, event.event_type)
        return await handler(event, data)
