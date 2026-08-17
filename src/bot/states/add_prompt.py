from aiogram.fsm.state import State, StatesGroup


class AddPromptForm(StatesGroup):
    """Flow: /add_prompt -> pick model -> send brief -> parse and save."""

    choosing_model = State()
    waiting_for_i2v_document = State()
