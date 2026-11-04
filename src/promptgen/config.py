"""Настройки и папки модуля промптов.

config.json (в git) + config.local.json (только на сервере, не в git) — локальный накладывается поверх.
Все данные сайта лежат в data/web/ (папка data/ не в git):
    data/web/niches/<ниша>/templates/<regular|avatar>.{md,json}  — мастер-промпты ниши и их разбор
    data/web/projects/<id>/                                       — проекты (см. promptgen/project.py)
    data/web/tasks/<id>.json                                      — короткие задачи (разбор шаблона)
    data/web/worker.lock                                          — признак, что worker запущен
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("PROMPTGEN_ROOT") or Path(__file__).resolve().parent.parent.parent)
PACKAGE = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE / "defaults"


def data_dir() -> Path:
    return Path(os.environ.get("PROMPTGEN_DATA") or ROOT / "data" / "web")


def projects_dir() -> Path:
    return data_dir() / "projects"


def niches_dir() -> Path:
    return data_dir() / "niches"


def tasks_dir() -> Path:
    return data_dir() / "tasks"


def lock_file() -> Path:
    return data_dir() / "worker.lock"


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    local = ROOT / "config.local.json"
    if local.exists():
        cfg = _merge(cfg, json.loads(local.read_text(encoding="utf-8")))
    if os.environ.get("PROMPTGEN_BACKEND"):  # тесты: PROMPTGEN_BACKEND=fake
        cfg["default_backend"] = os.environ["PROMPTGEN_BACKEND"]
    return cfg


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Запись через временный файл: API читает файлы, пока worker их пишет."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: файл в этот миг открыт на чтение
            import time

            time.sleep(0.05)
    os.replace(tmp, path)
