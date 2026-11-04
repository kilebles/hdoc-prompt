"""Вызов модели: повторы при ошибках, ожидание сброса лимита, остановка, разбор JSON из ответа.

Логика та же, что в fl-scenario (caller.py): лимит подписки пережидается до времени сброса,
ошибка входа останавливает задачу сразу, прочие ошибки — до max_errors_per_step попыток.
Вызовы идут из нескольких потоков одновременно, поэтому счётчик и журнал под блокировкой.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .backends import get_backend, model_label, short_backend, stage_choice


class CallError(Exception):
    """Вызов не удался окончательно (вход, неизвестная модель, ошибки подряд)."""


class StopRequested(Exception):
    """Пользователь нажал «Остановить»."""


class BadAnswer(Exception):
    """Модель ответила, но не тем (не JSON, не та структура)."""


class FormatFailed(CallError):
    """Модель несколько раз подряд ответила не по формату — этот кусок можно повторить отдельно."""


Emit = Callable[[str, str], None]


class Caller:
    def __init__(self, config: dict, calls_dir: Path, emit: Emit, should_stop: Callable[[], bool],
                 model: str = "", models: dict[str, str] | None = None,
                 on_status: Callable[..., None] | None = None):
        self.config = config
        self.calls_dir = calls_dir
        self.emit = emit
        self.should_stop = should_stop
        self.model = model
        self.models = models or {}
        self.on_status = on_status or (lambda **kw: None)
        self._lock = threading.Lock()
        self.n = 0
        self.seconds = 0.0
        self.tokens: dict[str, int] = {}

    def choice(self, stage: str) -> tuple:
        return stage_choice(self.config, stage, self.model, self.models)

    def label(self, stage: str) -> str:
        bid, mid = self.choice(stage)
        return f"{model_label(self.config, bid, mid)} [{short_backend(bid)}]"

    def parallel(self, stage: str) -> int:
        bid, _ = self.choice(stage)
        return max(1, int((self.config.get("parallel") or {}).get(bid, 2)))

    def __call__(self, stage: str, system: str, prompt: str, label: str) -> str:
        bid, model = self.choice(stage)
        backend = get_backend(bid, self.config)
        retry = self.config.get("retry", {})
        max_errors = int(retry.get("max_errors_per_step", 3))
        default_wait = float(retry.get("limit_wait_minutes_default", 30)) * 60
        max_wait = float(retry.get("limit_wait_max_hours", 8)) * 3600
        timeout = int(self.config.get("timeouts", {}).get("call_seconds", 900))
        errors = 0
        while True:
            if self.should_stop():
                raise StopRequested()
            with self._lock:
                self.n += 1
                n = self.n
            self.calls_dir.mkdir(parents=True, exist_ok=True)
            (self.calls_dir / f"{n:04d}_{label}.prompt.txt").write_text(
                "# SYSTEM\n\n" + system + "\n\n# PROMPT\n\n" + prompt, encoding="utf-8")
            res = backend.run(system=system, prompt=prompt, model=model or None, workdir=self.calls_dir / "work",
                              timeout=timeout, stage=stage)
            (self.calls_dir / f"{n:04d}_{label}.answer.txt").write_text(
                res.text if res.status == "ok" else res.error + "\n\n--- raw ---\n" + res.raw, encoding="utf-8")
            with self._lock:
                self.seconds += res.seconds
                for k, v in (res.tokens or {}).items():
                    self.tokens[k] = self.tokens.get(k, 0) + int(v)
            if res.status == "ok":
                return res.text
            if res.status == "limit":
                wait = (max(30.0, min(res.retry_at - time.time() + 60, 8 * 24 * 3600)) if res.retry_at
                        else max(30.0, min(default_wait, max_wait)))
                until = datetime.fromtimestamp(time.time() + wait)
                self.on_status(waiting_until=until.isoformat(timespec="seconds"))
                self.emit("limit", f"Лимит {short_backend(bid)} исчерпан. Жду до {until:%d.%m %H:%M} и продолжу сам.")
                _sleep(wait, self.should_stop)
                self.on_status(waiting_until=None)
                continue
            if res.status == "auth":
                raise CallError(res.error)
            errors += 1
            self.emit("error", f"{label}: ошибка ({errors}/{max_errors}): {res.error[:300]}")
            if errors >= max_errors:
                raise CallError(f"{label}: не удалось после {max_errors} попыток. {res.error[:500]}")
            _sleep(min(60, 10 * errors), self.should_stop)

    def json(self, stage: str, system: str, prompt: str, label: str, check: Callable[[Any], None],
             attempts: int = 2) -> Any:
        """Вызов, ответ которого — JSON. check() бросает BadAnswer, если структура не та; тогда ещё попытка
        с объяснением ошибки."""
        note = ""
        last = ""
        for i in range(attempts):
            text = self(stage, system, prompt + note, label if i == 0 else f"{label}_fix{i}")
            try:
                data = extract_json(text)
                check(data)
                return data
            except BadAnswer as e:
                last = str(e)
                self.emit("warn", f"{label}: ответ не по формату ({last[:200]}), прошу ещё раз")
                note = ("\n\nIMPORTANT: your previous answer could not be used: " + last[:400] +
                        ". Answer again with ONLY the JSON object described above.")
        raise FormatFailed(f"{label}: модель {attempts} раза ответила не по формату ({last[:300]})")


def _sleep(seconds: float, should_stop: Callable[[], bool]) -> None:
    end = time.time() + seconds
    while True:
        if should_stop():
            raise StopRequested()
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(left, 2))


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """JSON из ответа модели: блок ```json, иначе от первой { до парной }."""
    text = (text or "").strip().lstrip("﻿")
    candidates = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    for c in candidates:
        c = c.strip()
        try:
            return json.loads(c)
        except ValueError:
            pass
        start = c.find("{")
        if start < 0:
            continue
        obj = _balanced(c, start)
        if obj:
            try:
                return json.loads(obj)
            except ValueError:
                try:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", obj))  # лишняя запятая в конце списка
                except ValueError:
                    continue
    raise BadAnswer("в ответе нет корректного JSON")


def _balanced(s: str, start: int) -> str:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return ""
