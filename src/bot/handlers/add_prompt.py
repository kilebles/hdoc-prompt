from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Document, Message
from loguru import logger

from bot.keyboards.callback_data import AddPromptModelChoice
from bot.keyboards.inline import add_prompt_model_keyboard
from bot.models.prompt import PromptModelType
from bot.services.album_collector_service import AlbumCollectorService
from bot.services.prompt_parser_service import PromptParserService
from bot.services.prompt_storage_service import PromptStorageService
from bot.states.add_prompt import AddPromptForm
from bot.texts.ru import (
    CHOOSE_MODEL_TITLE,
    MODEL_IN_DEVELOPMENT,
    PARSING_IN_PROGRESS,
    SEND_I2V_DOCUMENT,
    album_file_failed,
    prompt_saved,
    prompts_saved_batch,
)

router = Router(name="add_prompt")


@router.message(Command("add_prompt"))
async def cmd_add_prompt(message: Message, state: FSMContext) -> None:
    await state.set_state(AddPromptForm.choosing_model)
    await message.answer(CHOOSE_MODEL_TITLE, reply_markup=add_prompt_model_keyboard().as_markup())


@router.callback_query(
    AddPromptForm.choosing_model,
    AddPromptModelChoice.filter(F.model_type != PromptModelType.I2V),
)
async def cb_model_not_ready(callback: CallbackQuery) -> None:
    await callback.answer(MODEL_IN_DEVELOPMENT, show_alert=True)


@router.callback_query(
    AddPromptForm.choosing_model,
    AddPromptModelChoice.filter(F.model_type == PromptModelType.I2V),
)
async def cb_model_i2v(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddPromptForm.waiting_for_i2v_document)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(SEND_I2V_DOCUMENT)
    await callback.answer()


async def _parse_and_save(
    document: Document,
    bot: Bot,
    prompt_parser_service: PromptParserService,
    prompt_storage_service: PromptStorageService,
) -> str:
    """Downloads, parses, and saves one brief document. Returns the saved title."""
    buffer = await bot.download(document.file_id)
    raw_text = buffer.read().decode("utf-8") if buffer is not None else ""

    logger.debug(
        "Parsing i2v brief, {} chars: {!r}...{!r}", len(raw_text), raw_text[:80], raw_text[-80:]
    )
    parsed = await prompt_parser_service.parse_i2v(raw_text)
    saved = await prompt_storage_service.save_i2v(parsed)
    logger.info(
        "add_prompt: saved id={} title={!r} pairs={}",
        saved.id,
        saved.data.title,
        len(saved.data.pairs),
    )
    return saved.data.title


@router.message(AddPromptForm.waiting_for_i2v_document, F.document, F.media_group_id.is_not(None))
async def on_i2v_document_album(
    message: Message,
    state: FSMContext,
    bot: Bot,
    prompt_parser_service: PromptParserService,
    prompt_storage_service: PromptStorageService,
    album_collector_service: AlbumCollectorService,
) -> None:
    if message.media_group_id is None:
        return

    async def on_settled(messages: list[Message]) -> None:
        status = await message.answer(PARSING_IN_PROGRESS)
        titles: list[str] = []
        for msg in messages:
            if msg.document is None:
                continue
            try:
                title = await _parse_and_save(
                    msg.document, bot, prompt_parser_service, prompt_storage_service
                )
                titles.append(title)
            except Exception:  # noqa: BLE001 — one bad file in the album must not abort the rest
                logger.exception(
                    "add_prompt: failed to process album file {!r}", msg.document.file_name
                )
                await message.answer(album_file_failed(msg.document.file_name or "?"))

        await state.clear()
        if titles:
            await status.edit_text(prompts_saved_batch(titles))
        else:
            await status.delete()

    album_collector_service.add(message.media_group_id, message, on_settled)


@router.message(AddPromptForm.waiting_for_i2v_document, F.document)
async def on_i2v_document(
    message: Message,
    state: FSMContext,
    bot: Bot,
    prompt_parser_service: PromptParserService,
    prompt_storage_service: PromptStorageService,
) -> None:
    document = message.document
    if document is None:
        return

    status = await message.answer(PARSING_IN_PROGRESS)
    title = await _parse_and_save(document, bot, prompt_parser_service, prompt_storage_service)

    await state.clear()
    await status.edit_text(prompt_saved(title))
