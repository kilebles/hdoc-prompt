from aiogram.fsm.state import State, StatesGroup


class RenamePromptForm(StatesGroup):
    """Flow: tap rename on a prompt -> bot asks for new title -> save."""

    waiting_for_title = State()
