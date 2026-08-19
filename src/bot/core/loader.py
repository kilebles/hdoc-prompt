from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import Settings
from bot.handlers import get_root_router
from bot.middlewares.request_logging import UpdateLoggingMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.services.album_collector_service import (
    AlbumCollectorService,
    DebounceAlbumCollectorService,
)
from bot.services.i2v_generation_service import (
    GeminiI2VGenerationService,
    I2VGenerationService,
    OpenAII2VGenerationService,
    RoutingI2VGenerationService,
)
from bot.services.prompt_parser_service import GeminiPromptParserService, PromptParserService
from bot.services.prompt_storage_service import FilePromptStorageService, PromptStorageService
from bot.services.scenario_parser_service import (
    ScenarioParserService,
    ScenarioParserServiceImpl,
)
from bot.services.xlsx_export_service import XlsxExportService, XlsxExportServiceImpl

PROMPTS_DATA_DIR = Path("data/prompts")


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher(
    settings: Settings,
    *,
    prompt_parser_service: PromptParserService | None = None,
    prompt_storage_service: PromptStorageService | None = None,
    scenario_parser_service: ScenarioParserService | None = None,
    i2v_generation_service: I2VGenerationService | None = None,
    xlsx_export_service: XlsxExportService | None = None,
    album_collector_service: AlbumCollectorService | None = None,
) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    # Middlewares: order matters, first registered runs first (outermost).
    dp.update.outer_middleware(UpdateLoggingMiddleware())
    dp.message.middleware(ThrottlingMiddleware(rate=0.1))
    dp.callback_query.middleware(ThrottlingMiddleware(rate=0.5))

    # Dependency injection: available to every handler via its signature.
    dp.workflow_data.update(
        prompt_parser_service=prompt_parser_service
        or GeminiPromptParserService(settings.google_ai),
        prompt_storage_service=prompt_storage_service
        or FilePromptStorageService(PROMPTS_DATA_DIR),
        scenario_parser_service=scenario_parser_service or ScenarioParserServiceImpl(),
        i2v_generation_service=i2v_generation_service
        or RoutingI2VGenerationService(
            gemini=GeminiI2VGenerationService(settings.google_ai),
            openai=OpenAII2VGenerationService(settings.openai),
        ),
        xlsx_export_service=xlsx_export_service or XlsxExportServiceImpl(),
        album_collector_service=album_collector_service or DebounceAlbumCollectorService(),
    )

    dp.include_router(get_root_router())
    return dp
