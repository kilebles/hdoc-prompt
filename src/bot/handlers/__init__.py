from aiogram import Router

from bot.handlers import add_prompt, errors, i2v, prompts
from bot.handlers.commands import start


def get_root_router() -> Router:
    """Aggregates all domain routers. New feature = new router + one line here."""
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(add_prompt.router)
    root.include_router(prompts.router)
    root.include_router(i2v.router)
    root.include_router(errors.router)
    return root
