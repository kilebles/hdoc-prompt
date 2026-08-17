from aiogram.filters.callback_data import CallbackData

from bot.models.prompt import PromptModelType


class AddPromptModelChoice(CallbackData, prefix="add_prompt_model"):
    model_type: PromptModelType


class PromptAction(CallbackData, prefix="prompt"):
    prompt_id: str
    action: str
    page: int = 0


class PromptsPage(CallbackData, prefix="prompts_page"):
    page: int


class I2VPromptChoice(CallbackData, prefix="i2v_prompt"):
    prompt_id: str


class I2VPromptsPage(CallbackData, prefix="i2v_prompts_page"):
    page: int


class I2VModelChoice(CallbackData, prefix="i2v_model"):
    model: str


class I2VPairCountChoice(CallbackData, prefix="i2v_pair_count"):
    count: int
