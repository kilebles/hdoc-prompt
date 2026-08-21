import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from aiogram.types import Message

AlbumCallback = Callable[[list[Message]], Awaitable[None]]


class AlbumCollectorService(Protocol):
    """Buffers messages sharing a Telegram media_group_id into one batch.

    Telegram delivers each file in an album as a separate Message update, all
    carrying the same media_group_id, with no explicit "this is the last one"
    marker. Gaps between parts of the same album can occasionally run into
    several seconds (observed: 3 files arriving ~30s apart via polling), so
    the debounce delay must be generous — too short and the first part fires
    on its own, and since the handler clears FSM state once it settles, any
    later part of the same album arrives with no matching state to land in.
    """

    def add(self, media_group_id: str, message: Message, on_settled: AlbumCallback) -> None: ...


class DebounceAlbumCollectorService:
    def __init__(self, delay_seconds: float = 3.0) -> None:
        self._delay = delay_seconds
        self._buffers: dict[str, list[Message]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def add(self, media_group_id: str, message: Message, on_settled: AlbumCallback) -> None:
        self._buffers.setdefault(media_group_id, []).append(message)

        if media_group_id in self._tasks:
            self._tasks[media_group_id].cancel()
        self._tasks[media_group_id] = asyncio.create_task(
            self._wait_and_fire(media_group_id, on_settled)
        )

    async def _wait_and_fire(self, media_group_id: str, on_settled: AlbumCallback) -> None:
        await asyncio.sleep(self._delay)
        messages = self._buffers.pop(media_group_id, [])
        self._tasks.pop(media_group_id, None)
        if messages:
            await on_settled(messages)
