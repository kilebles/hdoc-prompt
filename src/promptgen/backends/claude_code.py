"""Исполнитель через Claude Code CLI (работает на подписке Claude).

- без ANTHROPIC_API_KEY — расход только из подписки, не с API-счёта;
- модели — псевдонимы Claude Code («opus», «sonnet»): CLI сам берёт новейшую версию,
  поэтому новые модели подхватываются без правки списка;
- уровень в id модели: «opus-medium» → --model opus --effort medium; на low размышления
  выключены (MAX_THINKING_TOKENS=0) — так в разы дешевле;
- своя папка входа (CLAUDE_CONFIG_DIR), если сервер общий;
- токены каждого вызова возвращаются в CallResult.tokens для учёта расхода.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .base import (
    BADFLAG_RE,
    IS_WINDOWS,
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

EFFORT_SUPPORTED = True  # старая версия Claude Code без --effort — продолжаем без флага


class ClaudeCodeBackend(Backend):
    id = "claude"

    def binary(self) -> str | None:
        home = Path.home()
        extra = [
            home / ".local" / "bin" / "claude",
            home / ".claude" / "local" / "claude",
            Path("/opt/homebrew/bin/claude"),
            Path("/usr/local/bin/claude"),
        ]
        if IS_WINDOWS:
            extra += [
                home / ".local" / "bin" / "claude.exe",
                home / "AppData" / "Roaming" / "npm" / "claude.cmd",
            ]
        return find_binary(["claude"], extra, self.cfg.get("binary_path", ""))

    def check(self) -> tuple[bool, str]:
        b = self.binary()
        if not b:
            return False, "Claude Code не найден. Установка: https://code.claude.com/docs/en/setup"
        return True, b

    def env_extra(self) -> dict[str, str]:
        extra = dict(self.cfg.get("env") or {})
        if self.cfg.get("config_dir"):
            extra["CLAUDE_CONFIG_DIR"] = self.cfg["config_dir"]  # свой вход для генератора на общем сервере
        return extra

    def effort_for(self, stage: str) -> str:
        eff = self.cfg.get("effort", {})
        if isinstance(eff, str):
            return eff
        return str(eff.get(stage) or eff.get("default") or "")

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
        global EFFORT_SUPPORTED
        b = self.binary()
        if not b:
            return CallResult("error", error=self.check()[1])

        workdir.mkdir(parents=True, exist_ok=True)
        sys_file = workdir / f"system_prompt_{stage or 'call'}_{uuid.uuid4().hex[:6]}.txt"  # параллельные вызовы
        sys_file.write_text(system, encoding="utf-8")
        model, level = split_effort(model)
        effort = level or self.effort_for(stage)
        extra_env = self.env_extra()
        if effort == "low":
            extra_env["MAX_THINKING_TOKENS"] = "0"  # без размышлений — дешевле в разы
        else:
            extra_env.setdefault("MAX_THINKING_TOKENS", "")

        def build(with_effort: bool):
            cmd = [b, "-p", "--output-format", "json", "--no-session-persistence",
                   "--system-prompt-file", str(sys_file)]
            if model:
                cmd += ["--model", model]
            if with_effort and effort:
                cmd += ["--effort", effort]
            cmd += [str(a) for a in self.cfg.get("extra_args", [])]
            if web_search:
                # проверка фактов: разрешён только веб-поиск
                cmd += ["--tools", "WebSearch", "--allowedTools", "WebSearch", "--max-turns", "20"]
            else:
                # обычная генерация текста: никаких инструментов, только ответ
                cmd += ["--tools", ""]
            return cmd

        t0 = time.monotonic()
        while True:
            code, out, err, timed_out = run_process(
                build(EFFORT_SUPPORTED), workdir, timeout, stdin_text=prompt,
                drop_env=["ANTHROPIC_API_KEY"], extra_env=extra_env)
            blob = out + "\n" + err
            if EFFORT_SUPPORTED and effort and BADFLAG_RE.search(blob) and "effort" in blob.lower():
                EFFORT_SUPPORTED = False
                continue
            break
        secs = elapsed_since(t0)
        raw = (out + ("\n[stderr]\n" + err if err.strip() else "")).strip()

        if timed_out:
            return CallResult("error", error=f"Таймаут {timeout} с", raw=raw, seconds=secs)

        data = _parse_json(out)
        if data is not None:
            tokens = _tokens(data)
            text = str(data.get("result", "") or "")
            failed = bool(data.get("is_error")) or data.get("subtype") not in (None, "success")
            if not failed and text.strip():
                return CallResult("ok", text=text, raw=raw, seconds=secs, tokens=tokens)
            msg = text or str(data.get("subtype", "")) or err
            res = _error_or_limit(msg + "\n" + err, raw, secs, model)
            res.tokens = tokens
            return res

        # JSON не пришёл — смотрим на текст ошибки
        return _error_or_limit((out + "\n" + err).strip() or f"код выхода {code}", raw, secs, model)


LEVELS = ("low", "medium", "high")


def split_effort(model: str | None) -> tuple[str | None, str]:
    """«opus-medium» → («opus», «medium»); «opus» → («opus», «»)."""
    if not model:
        return model, ""
    base, sep, level = model.rpartition("-")
    if sep and level in LEVELS and base:
        return base, level
    return model, ""


def _tokens(data: dict) -> dict[str, int] | None:
    u = data.get("usage") or {}
    if not isinstance(u, dict) or not u:
        return None
    return {
        "input": int(u.get("input_tokens") or 0),
        "cache": int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
        "cache_read": int(u.get("cache_read_input_tokens") or 0),
        "output": int(u.get("output_tokens") or 0),
    }


def _parse_json(out: str) -> dict | None:
    out = (out or "").strip()
    if not out:
        return None
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):  # на случай потокового формата
            for item in reversed(data):
                if isinstance(item, dict) and item.get("type") == "result":
                    return item
    except json.JSONDecodeError:
        pass
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    return None


def _error_or_limit(msg: str, raw: str, secs: float, model: str | None = None) -> CallResult:
    msg = (msg or "").strip()
    if looks_like_auth_error(msg) and not looks_like_limit(msg):
        return CallResult("auth", error="Claude Code не вошёл в аккаунт: запусти `claude` в терминале (на сервере — "
                                        "с той же папкой входа, что в config.local.json → backends.claude.config_dir), "
                                        "войди аккаунтом с подпиской и продолжи задачу. " + msg[-300:],
                          raw=raw, seconds=secs)
    if model and UNKNOWN_MODEL_RE.search(msg):
        return CallResult("auth", error=f"Claude Code не знает модель «{model}». Выбери другую в задании или "
                                        f"config.local.json → backends.claude.models. " + msg[-300:],
                          raw=raw, seconds=secs)
    if looks_like_limit(msg):
        retry_at = parse_reset_time(msg)
        if retry_at is None and "overloaded" in msg.lower():
            retry_at = time.time() + 120  # перегрузка серверов — пробуем через пару минут
        return CallResult("limit", error=msg[-800:], retry_at=retry_at, raw=raw, seconds=secs)
    return CallResult("error", error=msg[-800:] or "неизвестная ошибка", raw=raw, seconds=secs)
