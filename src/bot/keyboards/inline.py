from collections.abc import Callable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.callback_data import (
    AddPromptModelChoice,
    I2VModelChoice,
    I2VPairCountChoice,
    I2VPromptChoice,
    I2VPromptsPage,
    PromptAction,
    PromptsPage,
)
from bot.models.prompt import PromptModelType
from bot.models.saved_prompt import SavedPrompt

_MODEL_LABELS: dict[PromptModelType, str] = {
    PromptModelType.I2V: "i2v",
    PromptModelType.F2V: "f2v",
    PromptModelType.T2V: "t2v",
}

PROMPTS_PAGE_SIZE = 8


def _add_pagination_row(
    builder: InlineKeyboardBuilder, page: int, total_items: int, make_callback: Callable[[int], CallbackData]
) -> None:
    total_pages = max(1, -(-total_items // PROMPTS_PAGE_SIZE))
    if total_pages <= 1:
        return

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=make_callback(page - 1).pack()))
    buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=make_callback(page + 1).pack()))
    builder.row(*buttons)

# (model id, label) — label shows output price per 1M tokens as a quick cost
# reference (output is the dominant cost driver and the more relevant number).
I2V_GENERATION_MODELS: list[tuple[str, str]] = [
    ("gemini-2.5-flash-lite", "Flash-Lite ($0.40)"),
    ("gemini-2.5-flash", "Flash ($2.50)"),
    ("gemini-2.5-pro", "Pro ($10)"),
    ("gemini-3.6-flash", "3.6 Flash ($7.50)"),
]


def add_prompt_model_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for model_type, label in _MODEL_LABELS.items():
        builder.button(
            text=label,
            callback_data=AddPromptModelChoice(model_type=model_type),
        )
    builder.adjust(3)
    return builder


def prompts_list_keyboard(prompts: list[SavedPrompt], page: int = 0) -> InlineKeyboardBuilder:
    start = page * PROMPTS_PAGE_SIZE
    page_items = prompts[start : start + PROMPTS_PAGE_SIZE]

    builder = InlineKeyboardBuilder()
    for prompt in page_items:
        builder.row(
            InlineKeyboardButton(
                text=prompt.data.title,
                callback_data=PromptAction(prompt_id=prompt.id, action="open", page=page).pack(),
            ),
            InlineKeyboardButton(
                text="✍️",
                callback_data=PromptAction(prompt_id=prompt.id, action="rename", page=page).pack(),
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=PromptAction(prompt_id=prompt.id, action="delete", page=page).pack(),
            ),
        )
    _add_pagination_row(builder, page, len(prompts), lambda p: PromptsPage(page=p))
    return builder


def back_to_prompts_list_keyboard(page: int = 0) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="К списку промптов",
            callback_data=PromptAction(prompt_id="", action="list", page=page).pack(),
        )
    )
    return builder


def i2v_prompts_list_keyboard(prompts: list[SavedPrompt], page: int = 0) -> InlineKeyboardBuilder:
    start = page * PROMPTS_PAGE_SIZE
    page_items = prompts[start : start + PROMPTS_PAGE_SIZE]

    builder = InlineKeyboardBuilder()
    for prompt in page_items:
        builder.button(
            text=prompt.data.title,
            callback_data=I2VPromptChoice(prompt_id=prompt.id),
        )
    builder.adjust(1)
    _add_pagination_row(builder, page, len(prompts), lambda p: I2VPromptsPage(page=p))
    return builder


def i2v_model_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for model_id, label in I2V_GENERATION_MODELS:
        builder.button(text=label, callback_data=I2VModelChoice(model=model_id))
    builder.adjust(1)
    return builder


_PAIR_COUNT_OPTIONS = (2, 4, 6, 8, 12)


def i2v_pair_count_keyboard(total_pairs: int) -> InlineKeyboardBuilder:
    # Fixed candidate counts, capped at what the template actually offers —
    # plus the template's own max as a final option even if it isn't one of
    # the fixed candidates (e.g. total_pairs=10 -> 2, 4, 6, 8, 10).
    counts = {n for n in _PAIR_COUNT_OPTIONS if n <= total_pairs}
    if total_pairs > 0:
        counts.add(total_pairs)

    builder = InlineKeyboardBuilder()
    for n in sorted(counts):
        builder.button(text=str(n), callback_data=I2VPairCountChoice(count=n))
    builder.adjust(4)
    return builder
