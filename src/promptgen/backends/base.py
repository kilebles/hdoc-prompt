"""Общий интерфейс исполнителей (backends) и вспомогательные функции.

Генератор знает только одно: «выполни задание и верни текст».
Каждый исполнитель (Claude Code, Antigravity, ...) реализует метод run().
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"

# Признаки того, что закончилась квота / сработал лимит подписки
LIMIT_MARKERS = (
    "usage limit",
    "limit reached",
    "hit your limit",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "quota",
    "too many requests",
    " 429",
    "(429)",
    "overloaded",
    "exhausted your",
    "out of credits",
)
LOCATION_RE = re.compile(r"location is not supported|not (?:available|supported) in your (?:country|region)|"
                         r"unsupported (?:location|region|country)", re.IGNORECASE)
# CLI не знает флаг (старая версия) — тогда повторяем вызов без него, как в переводчике
BADFLAG_RE = re.compile(r"unknown (?:option|flag|argument)|unrecognized (?:option|argument)|"
                        r"flag provided but not defined|no such option|invalid option", re.IGNORECASE)
UNKNOWN_MODEL_RE = re.compile(
    r"(unknown|invalid|unsupported|not found|no such)\W+(?:\w+\W+){0,3}model|"
    r"model\W+(?:\S+\W+){0,3}(?:not found|unknown|invalid|unsupported|not available)", re.IGNORECASE)


@dataclass
class CallResult:
    """Результат одного вызова модели."""

    status: str  # "ok" | "limit" | "auth" | "error"
    text: str = ""
    error: str = ""
    retry_at: float | None = None  # unix-время, когда можно повторить (если известно)
    raw: str = ""  # сырой вывод CLI — для логов
    seconds: float = 0.0
    tokens: dict[str, int] | None = None  # расход Claude: input, cache, output


class Backend:
    """Базовый класс исполнителя."""

    id = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    # --- описание для CLI/фронтенда -------------------------------------
    @property
    def label(self) -> str:
        return self.cfg.get("label", self.id)

    def models(self) -> list[dict]:
        return list(self.cfg.get("models", []))

    def stage_model(self, stage: str) -> str | None:
        return self.cfg.get("stage_models", {}).get(stage)

    def check(self) -> tuple[bool, str]:
        """Проверка, что исполнитель установлен. Возвращает (ok, сообщение)."""
        return True, "ok"

    # --- основной метод ---------------------------------------------------
    def run(
        self,
        system: str,
        prompt: str,
        model: str | None,
        workdir: Path,
        timeout: int,
        stage: str = "",
        web_search: bool = False,
    ) -> CallResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def find_binary(names: list[str], extra: list[Path] | None = None, explicit: str = "") -> str | None:
    """Ищет исполняемый файл: явный путь из конфига -> PATH -> типичные места."""
    if explicit:
        p = Path(os.path.expandvars(explicit)).expanduser()
        if p.exists():
            return str(p)
        found = shutil.which(explicit)
        if found:
            return found
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for c in extra or []:
        if c.exists():
            return str(c)
    return None


def decode(data: bytes | None) -> str:
    return data.decode("utf-8", errors="replace") if data else ""


def child_env(drop: list[str] | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Окружение для claude/agy (как в переводчике): UTF-8 и папка текущего Python первой в PATH —
    на сервере с .venv команда python иначе может открыть Microsoft Store."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    exe_dir = str(Path(os.path.abspath(sys.executable)).parent)  # без resolve: в .venv python — ссылка
    env["PATH"] = exe_dir + os.pathsep + env.get("PATH", "")
    for key in drop or []:
        env.pop(key, None)
    for k, v in (extra or {}).items():
        if v is None or v == "":
            env.pop(k, None)
        else:
            env[k] = os.path.expandvars(os.path.expanduser(str(v)))
    return env


def run_process(
    cmd: list[str], cwd: Path, timeout: int, stdin_text: str | None = None,
    drop_env: list[str] | None = None, extra_env: dict[str, str] | None = None,
) -> tuple[int | None, str, str, bool]:
    """Запускает процесс. Возвращает (код, stdout, stderr, был_таймаут)."""
    env = child_env(drop_env, extra_env)
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
        return r.returncode, decode(r.stdout), decode(r.stderr), False
    except subprocess.TimeoutExpired as e:
        return None, decode(e.stdout), decode(e.stderr), True
    except FileNotFoundError as e:
        return None, "", f"Не найден исполняемый файл: {e}", False


_LIMIT_RE = re.compile(
    r"(?:hit|reached|exceeded|exhausted)\b.{0,25}\blimit|\blimit\b.{0,25}\b(?:reached|resets?|exceeded)",
    re.IGNORECASE,
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


_AUTH_RE = re.compile(
    r"not logged in|please run /login|run `?claude`? to (?:log ?in|sign in)|invalid api key|"
    r"oauth token (?:has )?expired|authentication[_ ](?:error|failed)|\b401\b|unauthori[sz]ed|"
    r"credit balance is too low",
    re.IGNORECASE,
)


def looks_like_auth_error(text: str) -> bool:
    """Ошибка входа: повторять бессмысленно, нужен человек."""
    return bool(_AUTH_RE.search(text or ""))


def looks_like_limit(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in LIMIT_MARKERS) or bool(_LIMIT_RE.search(text or ""))


def parse_retry_in(text: str) -> float | None:
    """«retry in 1h2m», «try again after 30m», «retryDelay: 3600s» → unix-время повтора."""
    m = re.search(r"retry ?delay\W+(\d+)s", text or "", re.IGNORECASE)
    if m:
        return time.time() + int(m.group(1))
    m = re.search(r"(?:retry|try again|resets?|refreshes)\s+(?:in|after)\s+(?:(\d+)\s*d\w*\s*)?(?:(\d+)\s*h\w*)?\s*"
                  r"(?:(\d+)\s*m\w*)?\s*(?:(\d+)\s*s\w*)?", text or "", re.IGNORECASE)
    if m and any(m.groups()):
        d, h, mi, sec = (int(x or 0) for x in m.groups())
        return time.time() + d * 86400 + h * 3600 + mi * 60 + sec
    return None


def parse_reset_time(text: str) -> float | None:
    """Достаёт из сообщения об ошибке время сброса лимита (unix-время).

    Понимает: "limit reached|1760720400", "resets 3pm", "resets at 15:30",
    "resets Oct 20 at 6am (Europe/Moscow)".
    """
    if not text:
        return None
    m = re.search(r"\|(\d{10})\b", text)
    if m:
        return float(m.group(1))
    retry = parse_retry_in(text)
    if retry:
        return retry
    m = re.search(
        r"resets?\s+(?:on\s+)?(?:([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+)?(?:at\s+)?"
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:\(([^)]+)\))?",
        text, re.IGNORECASE)
    if not m:
        return None
    mon_s, day_s, hour_s, min_s, ampm, tz_name = m.groups()
    hour, minute = int(hour_s), int(min_s or 0)
    ampm = (ampm or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    tz = None
    if tz_name:
        try:
            from zoneinfo import ZoneInfo  # Python 3.9+

            tz = ZoneInfo(tz_name.strip())
        except Exception:
            tz = None
    now = datetime.now(tz) if tz else datetime.now()
    try:
        if mon_s and mon_s.lower()[:3] in _MONTHS:
            target = now.replace(month=_MONTHS[mon_s.lower()[:3]], day=int(day_s),
                                 hour=hour, minute=minute, second=0, microsecond=0)
            if target < now - timedelta(days=1):
                target = target.replace(year=target.year + 1)
        else:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
    except ValueError:
        return None
    return target.timestamp()


def elapsed_since(t0: float) -> float:
    return round(time.monotonic() - t0, 1)
