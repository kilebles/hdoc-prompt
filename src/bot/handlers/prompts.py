from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.callback_data import PromptAction, PromptsPage
from bot.keyboards.inline import (
    PROMPTS_PAGE_SIZE,
    back_to_prompts_list_keyboard,
    prompts_list_keyboard,
)
from bot.services.prompt_storage_service import PromptStorageService
from bot.states.rename_prompt import RenamePromptForm
from bot.texts.ru import (
    ASK_NEW_TITLE,
    PROMPT_NOT_FOUND,
    PROMPTS_LIST_EMPTY,
    PROMPTS_LIST_TITLE,
    prompt_deleted,
    prompt_details,
    prompt_renamed,
)

router = Router(name="prompts")


def _last_valid_page(item_count: int, page: int) -> int:
    max_page = max(0, -(-item_count // PROMPTS_PAGE_SIZE) - 1)
    return min(page, max_page)


async def _show_prompts_list(
    message: Message, prompt_storage_service: PromptStorageService, page: int = 0
) -> None:
    prompts = await prompt_storage_service.list_all()
    if not prompts:
        await message.edit_text(PROMPTS_LIST_EMPTY)
        return
    page = _last_valid_page(len(prompts), page)
    await message.edit_text(
        PROMPTS_LIST_TITLE,
        reply_markup=prompts_list_keyboard(prompts, page).as_markup(),
    )


@router.message(Command("prompts"))
async def cmd_prompts(message: Message, prompt_storage_service: PromptStorageService) -> None:
    prompts = await prompt_storage_service.list_all()
    if not prompts:
        await message.answer(PROMPTS_LIST_EMPTY)
        return
    await message.answer(
        PROMPTS_LIST_TITLE,
        reply_markup=prompts_list_keyboard(prompts).as_markup(),
    )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(PromptsPage.filter())
async def cb_prompts_page(
    callback: CallbackQuery,
    callback_data: PromptsPage,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await _show_prompts_list(callback.message, prompt_storage_service, callback_data.page)
    await callback.answer()


@router.callback_query(PromptAction.filter(F.action == "list"))
async def cb_prompts_list(
    callback: CallbackQuery,
    callback_data: PromptAction,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await _show_prompts_list(callback.message, prompt_storage_service, callback_data.page)
    await callback.answer()


@router.callback_query(PromptAction.filter(F.action == "open"))
async def cb_prompt_open(
    callback: CallbackQuery,
    callback_data: PromptAction,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    prompt = await prompt_storage_service.get(callback_data.prompt_id)
    if prompt is None:
        await callback.answer(PROMPT_NOT_FOUND, show_alert=True)
        return
    await callback.message.edit_text(
        prompt_details(prompt.data),
        reply_markup=back_to_prompts_list_keyboard(callback_data.page).as_markup(),
    )
    await callback.answer()


@router.callback_query(PromptAction.filter(F.action == "delete"))
async def cb_prompt_delete(
    callback: CallbackQuery,
    callback_data: PromptAction,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    prompt = await prompt_storage_service.get(callback_data.prompt_id)
    if prompt is None:
        await callback.answer(PROMPT_NOT_FOUND, show_alert=True)
        return
    await prompt_storage_service.delete(callback_data.prompt_id)
    await callback.answer(prompt_deleted(prompt.data.title), show_alert=False)
    # If this was the last item on the page, land on the new last page instead.
    await _show_prompts_list(callback.message, prompt_storage_service, callback_data.page)


@router.callback_query(PromptAction.filter(F.action == "rename"))
async def cb_prompt_rename_start(
    callback: CallbackQuery,
    callback_data: PromptAction,
    state: FSMContext,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    prompt = await prompt_storage_service.get(callback_data.prompt_id)
    if prompt is None:
        await callback.answer(PROMPT_NOT_FOUND, show_alert=True)
        return
    await state.set_state(RenamePromptForm.waiting_for_title)
    await state.update_data(prompt_id=prompt.id, page=callback_data.page)
    await callback.message.edit_text(ASK_NEW_TITLE)
    await callback.answer()


@router.message(RenamePromptForm.waiting_for_title, F.text)
async def on_new_title(
    message: Message, state: FSMContext, prompt_storage_service: PromptStorageService
) -> None:
    if message.text is None:
        return
    data = await state.get_data()
    prompt_id = data.get("prompt_id")
    if prompt_id is None:
        await state.clear()
        return

    old_prompt = await prompt_storage_service.get(prompt_id)
    if old_prompt is None:
        await message.answer(PROMPT_NOT_FOUND)
        await state.clear()
        return

    new_title = message.text.strip()
    renamed = await prompt_storage_service.rename(prompt_id, new_title)
    await state.clear()
    if renamed is None:
        await message.answer(PROMPT_NOT_FOUND)
        return
    await message.answer(prompt_renamed(old_prompt.data.title, renamed.data.title))
