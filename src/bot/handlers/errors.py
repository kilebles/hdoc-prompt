from aiogram import F, Router
from aiogram.types import ErrorEvent, Message
from loguru import logger

from bot.texts.ru import UNKNOWN_COMMAND

router = Router(name="errors")


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer(UNKNOWN_COMMAND)


@router.errors()
async def handle_errors(event: ErrorEvent) -> None:
    logger.opt(exception=event.exception).error(
        "Update {} caused an error", event.update.update_id
    )
