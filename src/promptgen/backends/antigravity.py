"""Исполнитель через Google Antigravity CLI (agy), подписка Google AI.

Схема та же, что в переводчике (v37, работает на сервере):
задание кладётся в файл, agy получает короткую команду «прочитай файл и выполни», ответ пишет
в файл — так не мешают ни ограничение длины командной строки на Windows, ни пустой stdout.
Квота Google ловится и пережидается; «регион не поддерживается», вход и неизвестная модель
останавливают задачу с понятным сообщением.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from .base import (
    BADFLAG_RE,
    IS_WINDOWS,
    LOCATION_RE,
    UNKNOWN_MODEL_RE,
    Backend,
    CallResult,
    elapsed_since,
    find_binary,
    looks_like_auth_error,
    looks_like_limit,
    parse_reset_time,
    run_process,
)


class AntigravityBackend(Backend):
    id = "antigravity"

    def binary(self) -> str | None:
        return agy_binary(self.cfg.get("binary_path", ""))

    def check(self) -> tuple[bool, str]:
        b = self.binary()
        if not b:
            return False, ("Antigravity CLI (agy) не найден. Установка: macOS — "
                           "curl -fsSL https://antigravity.google/cli/install.sh | bash; Windows — "
                           "irm https://antigravity.google/cli/install.ps1 | iex. Потом один раз запусти agy и войди. "
                           "Или укажи путь в config.local.json → backends.antigravity.binary_path")
        return True, b

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
        global TIMEOUT_FLAG
        b = self.binary()
        if not b:
            return CallResult("error", error=self.check()[1])

        workdir.mkdir(parents=True, exist_ok=True)
        tag = f"{stage or 'call'}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"  # параллельные вызовы не путаются
        task_file = workdir / f"{tag}.task.md"
        answer_file = workdir / f"{tag}.answer.md"

        search_note = (
            "You may use web search to verify facts.\n" if web_search
            else "Do not use web search or run any commands.\n"
        )
        task_file.write_text("# INSTRUCTIONS\n\n" + system + "\n\n# TASK\n\n" + prompt + "\n", encoding="utf-8")
        short_prompt = (
            f"Read the file {task_file.as_posix()} and complete the task described in it. "
            f"{search_note}"
            f"Write ONLY the final answer (no comments, no preamble) as UTF-8 text to the file "
            f"{answer_file.as_posix()} . Do not create or modify any other files. Do not ask questions."
        )

        t0 = time.monotonic()
        while True:
            cmd = [b]
            if model:
                cmd += ["--model", model]
            cmd += ["--add-dir", str(workdir), "--dangerously-skip-permissions"]
            if TIMEOUT_FLAG:
                cmd += ["--print-timeout", f"{max(1, timeout // 60)}m"]
            cmd += [str(a) for a in self.cfg.get("extra_args", [])] + ["--print", short_prompt]
            code, out, err, timed_out = run_process(cmd, workdir, timeout + 120)
            blob = out + "\n" + err
            if not answer_file.exists() and TIMEOUT_FLAG and BADFLAG_RE.search(blob) and "timeout" in blob.lower():
                TIMEOUT_FLAG = False  # эта версия agy не знает --print-timeout
                continue
            break
        secs = elapsed_since(t0)
        raw = (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()
        (workdir / f"{tag}.log").write_text(raw, encoding="utf-8")

        text = answer_file.read_text(encoding="utf-8", errors="replace").strip() if answer_file.exists() else ""
        if text:
            return CallResult("ok", text=text, raw=raw, seconds=secs)
        if timed_out:
            return CallResult("error", error=f"Таймаут {timeout} с", raw=raw, seconds=secs)

        msg = (out + "\n" + err).strip() or f"код выхода {code}, ответ не записан"
        if LOCATION_RE.search(msg):
            return CallResult("auth", error="Google отклонил запрос: «регион не поддерживается». Обычно это VPN или "
                                            "прокси. Переключись на Claude Code или запусти на сервере. " + msg[-400:],
                              raw=raw, seconds=secs)
        if model and UNKNOWN_MODEL_RE.search(msg):
            return CallResult("auth", error=f"agy не знает модель «{model}». Список моделей: `agy models`; "
                                            f"поправь config.local.json → backends.antigravity. " + msg[-300:],
                              raw=raw, seconds=secs)
        if looks_like_auth_error(msg) and not looks_like_limit(msg):
            return CallResult("auth", error="Antigravity не вошёл в аккаунт: запусти `agy` и войди. " + msg[-300:],
                              raw=raw, seconds=secs)
        if looks_like_limit(msg):
            return CallResult("limit", error=msg[-800:], retry_at=parse_reset_time(msg), raw=raw, seconds=secs)
        return CallResult("error", error=msg[-800:], raw=raw, seconds=secs)


TIMEOUT_FLAG = True


def agy_binary(explicit: str = "") -> str | None:
    """Путь к agy: настройка → PATH → стандартные места (в т.ч. %LOCALAPPDATA%\\agy\\bin, как на сервере)."""
    home = Path.home()
    extra = [
        home / ".local" / "bin" / "agy",
        home / ".gemini" / "antigravity-cli" / "bin" / "agy",
        Path("/usr/local/bin/agy"),
        Path("/opt/homebrew/bin/agy"),
    ]
    if IS_WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        extra = [
            local / "agy" / "bin" / "agy.exe",
            home / ".local" / "bin" / "agy.exe",
            local / "Programs" / "agy" / "agy.exe",
            home / ".gemini" / "antigravity-cli" / "bin" / "agy.exe",
        ] + extra
    return find_binary(["agy"], extra, explicit)
