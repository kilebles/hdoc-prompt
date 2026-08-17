import logging
import sys
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level:<8}</level> | "
    "<cyan>{name}</cyan> | <level>{message}</level>"
)

_LOG_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name} | {message}"

LOGS_DIR = Path("data/logs")


class InterceptHandler(logging.Handler):
    """Routes stdlib `logging` records (aiogram, aiohttp, asyncio...) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        next_frame: FrameType | None = frame.f_back
        while next_frame and next_frame.f_code.co_filename == logging.__file__:
            next_frame = next_frame.f_back
            depth += 1

        # `{name}` in the format above is normally derived from the caller's
        # module via `depth`; here we override it with the stdlib logger's
        # own name (e.g. "aiogram.event") so intercepted records keep their origin.
        def _set_name(r: "Record") -> None:
            r["name"] = record.name

        patched = logger.patch(_set_name)
        patched.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=level.upper(),
        format=_LOG_FORMAT,
        backtrace=False,
        diagnose=False,
    )

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        LOGS_DIR / "bot.log",
        level=level.upper(),
        format=_LOG_FILE_FORMAT,
        rotation="10 MB",
        retention=5,
        backtrace=False,
        diagnose=False,
        encoding="utf-8",
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Silence noisy third-party loggers unless we're debugging.
    if level.upper() != "DEBUG":
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)
