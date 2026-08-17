import asyncio

from loguru import logger

from bot.config import get_settings
from bot.core.commands import set_bot_commands
from bot.core.loader import build_bot, build_dispatcher
from bot.core.logging import setup_logging


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.logging.level)

    bot = build_bot(settings)
    dp = build_dispatcher(settings)

    await set_bot_commands(bot)

    logger.info("Starting polling")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        handle_signals=True,
        drop_pending_updates=settings.bot.drop_pending_updates,
    )


if __name__ == "__main__":
    asyncio.run(main())
