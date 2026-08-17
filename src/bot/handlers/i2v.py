from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from loguru import logger

from bot.keyboards.callback_data import (
    I2VModelChoice,
    I2VPairCountChoice,
    I2VPromptChoice,
    I2VPromptsPage,
)
from bot.keyboards.inline import (
    i2v_model_keyboard,
    i2v_pair_count_keyboard,
    i2v_prompts_list_keyboard,
)
from bot.services.i2v_generation_service import I2VGenerationService
from bot.services.prompt_storage_service import PromptStorageService
from bot.services.scenario_parser_service import ScenarioParserService
from bot.services.xlsx_export_service import XlsxExportService
from bot.states.i2v_generation import I2VGenerationForm
from bot.texts.ru import (
    CHOOSE_GENERATION_MODEL_TITLE,
    CHOOSE_PAIR_COUNT_TITLE,
    I2V_PROMPTS_LIST_EMPTY,
    I2V_PROMPTS_LIST_TITLE,
    PROMPT_NOT_FOUND,
    SCENARIO_EMPTY,
    SEND_SCENARIO_DOCUMENT,
    scenario_progress,
)

router = Router(name="i2v")


@router.message(Command("i2v"))
async def cmd_i2v(
    message: Message, state: FSMContext, prompt_storage_service: PromptStorageService
) -> None:
    prompts = await prompt_storage_service.list_all()
    if not prompts:
        await message.answer(I2V_PROMPTS_LIST_EMPTY)
        return
    await state.set_state(I2VGenerationForm.choosing_prompt)
    await message.answer(
        I2V_PROMPTS_LIST_TITLE,
        reply_markup=i2v_prompts_list_keyboard(prompts).as_markup(),
    )


@router.callback_query(I2VGenerationForm.choosing_prompt, I2VPromptsPage.filter())
async def cb_i2v_prompts_page(
    callback: CallbackQuery,
    callback_data: I2VPromptsPage,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    prompts = await prompt_storage_service.list_all()
    await callback.message.edit_text(
        I2V_PROMPTS_LIST_TITLE,
        reply_markup=i2v_prompts_list_keyboard(prompts, callback_data.page).as_markup(),
    )
    await callback.answer()


@router.callback_query(I2VGenerationForm.choosing_prompt, I2VPromptChoice.filter())
async def cb_i2v_prompt_chosen(
    callback: CallbackQuery,
    callback_data: I2VPromptChoice,
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

    await state.update_data(prompt_id=prompt.id)
    await state.set_state(I2VGenerationForm.choosing_model)
    await callback.message.edit_text(
        CHOOSE_GENERATION_MODEL_TITLE,
        reply_markup=i2v_model_keyboard().as_markup(),
    )
    await callback.answer()
    logger.info("i2v: prompt chosen id={} title={!r}", prompt.id, prompt.data.title)


@router.callback_query(I2VGenerationForm.choosing_model, I2VModelChoice.filter())
async def cb_i2v_model_chosen(
    callback: CallbackQuery,
    callback_data: I2VModelChoice,
    state: FSMContext,
    prompt_storage_service: PromptStorageService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    data = await state.get_data()
    prompt = await prompt_storage_service.get(data.get("prompt_id", ""))
    if prompt is None:
        await callback.answer(PROMPT_NOT_FOUND, show_alert=True)
        return

    await state.update_data(model=callback_data.model)
    await state.set_state(I2VGenerationForm.choosing_pair_count)
    await callback.message.edit_text(
        CHOOSE_PAIR_COUNT_TITLE,
        reply_markup=i2v_pair_count_keyboard(len(prompt.data.pairs)).as_markup(),
    )
    await callback.answer()
    logger.info("i2v: model chosen {}", callback_data.model)


@router.callback_query(I2VGenerationForm.choosing_pair_count, I2VPairCountChoice.filter())
async def cb_i2v_pair_count_chosen(
    callback: CallbackQuery,
    callback_data: I2VPairCountChoice,
    state: FSMContext,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    await state.update_data(pair_count=callback_data.count)
    await state.set_state(I2VGenerationForm.waiting_for_scenario)
    await callback.message.edit_text(SEND_SCENARIO_DOCUMENT)
    await callback.answer()


@router.message(I2VGenerationForm.waiting_for_scenario, F.document)
async def on_scenario_document(
    message: Message,
    state: FSMContext,
    bot: Bot,
    prompt_storage_service: PromptStorageService,
    scenario_parser_service: ScenarioParserService,
    i2v_generation_service: I2VGenerationService,
    xlsx_export_service: XlsxExportService,
) -> None:
    document = message.document
    if document is None or document.file_name is None:
        return

    data = await state.get_data()
    prompt_id = data.get("prompt_id")
    pair_count = data.get("pair_count")
    model = data.get("model")
    if prompt_id is None or pair_count is None or model is None:
        return

    prompt = await prompt_storage_service.get(prompt_id)
    if prompt is None:
        await message.answer(PROMPT_NOT_FOUND)
        await state.clear()
        return

    file_name = document.file_name.lower()
    buffer = await bot.download(document.file_id)
    content = buffer.read() if buffer is not None else b""

    if file_name.endswith(".docx"):
        paragraphs = scenario_parser_service.parse_docx(content)
    else:
        paragraphs = scenario_parser_service.parse_txt(content)

    if not paragraphs:
        await message.answer(SCENARIO_EMPTY)
        return

    pairs = prompt.data.pairs[:pair_count]
    status = await message.answer(scenario_progress(0, len(paragraphs)))

    async def on_progress(done: int, total: int) -> None:
        logger.info("i2v: paragraph {}/{} processed", done, total)
        try:
            await status.edit_text(scenario_progress(done, total))
        except TelegramBadRequest:
            # e.g. "message is not modified" if progress text happens to repeat.
            logger.debug("Progress message edit rejected by Telegram, ignoring")

    logger.info(
        "i2v: starting generation prompt={} model={} pairs={} paragraphs={}",
        prompt.id,
        model,
        len(pairs),
        len(paragraphs),
    )
    generated = await i2v_generation_service.generate(
        prompt.data, pairs, paragraphs, model, on_progress
    )

    workbook_bytes = xlsx_export_service.build_i2v_workbook(generated)
    output_name = f"{prompt.data.title or 'i2v'}.xlsx"
    logger.info(
        "i2v: generation complete rows={} file={!r} size={} bytes",
        len(generated),
        output_name,
        len(workbook_bytes),
    )

    await state.clear()
    await status.delete()
    await message.answer_document(BufferedInputFile(workbook_bytes, filename=output_name))
