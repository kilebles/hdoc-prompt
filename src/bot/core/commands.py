from aiogram import Bot
from aiogram.types import BotCommand

# Single source of truth for bot commands: used both to register them
# with Telegram (set_my_commands) and to render /help text.
BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="add_prompt", description="Добавить промпт"),
    BotCommand(command="prompts", description="Список сохранённых промптов"),
    BotCommand(command="i2v", description="Сгенерировать i2v-промпты по сценарию"),
]


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS)
