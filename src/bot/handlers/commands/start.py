from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.texts.ru import START_GREETING

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(START_GREETING)
