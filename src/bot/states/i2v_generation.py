from aiogram.fsm.state import State, StatesGroup


class I2VGenerationForm(StatesGroup):
    """Flow: /i2v -> pick prompt -> pick model -> pick pair count -> send scenario -> receive xlsx."""

    choosing_prompt = State()
    choosing_model = State()
    choosing_pair_count = State()
    waiting_for_scenario = State()
