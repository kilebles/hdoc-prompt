"""Исполнители: agy (Google Antigravity, подписка Google AI) и Claude Code (подписка Claude).

Код исполнителей тот же, что в fl-scenario и переводчике: задание уходит в CLI, ответ возвращается текстом.
Новый исполнитель = новый класс + строка в REGISTRY + блок в config.json.
"""

from __future__ import annotations


from .antigravity import AntigravityBackend
from .base import Backend, CallResult
from .claude_code import ClaudeCodeBackend
from .fake import FakeBackend

REGISTRY = {
    "antigravity": AntigravityBackend,
    "claude": ClaudeCodeBackend,
    "fake": FakeBackend,
}

# этапы, для которых выбирается модель: разбор шаблона, бриф, промпты
STAGES = [("template", "Разбор шаблона"), ("brief", "Бриф"), ("prompts", "Промпты")]


def get_backend(backend_id: str, config: dict) -> Backend:
    if backend_id not in REGISTRY:
        raise ValueError(f"Неизвестный исполнитель: {backend_id}. Доступны: {', '.join(REGISTRY)}")
    return REGISTRY[backend_id](config.get("backends", {}).get(backend_id, {}))


def list_backends(config: dict) -> list[dict]:
    """Исполнители и модели для сайта (выпадающий список)."""
    out = []
    for bid, cls in REGISTRY.items():
        cfg = config.get("backends", {}).get(bid, {})
        if cfg.get("hidden"):
            continue
        backend = cls(cfg)
        ok, info = backend.check()
        out.append({"id": bid, "label": backend.label, "enabled": bool(cfg.get("enabled", False)),
                    "installed": ok, "info": "" if ok else info, "models": backend.models(),
                    "stage_models": cfg.get("stage_models", {})})
    return out


def resolve_model(config: dict, value: str) -> tuple[str | None, str]:
    """id или слово модели → (исполнитель, id). «antigravity:gemini-4-argon-high» — исполнитель указан явно.
    Модель не найдена ни у кого — (None, value)."""
    value = (value or "").strip()
    if not value:
        return None, ""
    head, sep, tail = value.partition(":")
    backends = config.get("backends", {})
    if sep and head in backends:
        return head, tail
    v = value.lower()
    order = [config.get("default_backend", "antigravity")] + [b for b in backends if b != config.get("default_backend")]
    for bid in order:
        for m in backends.get(bid, {}).get("models", []):
            if v in (str(m.get("id", "")).lower(), str(m.get("word", "")).lower()):
                return bid, m["id"]
    return None, value


def stage_choice(config: dict, stage: str, chosen: str = "", per_stage: dict | None = None) -> tuple[str, str]:
    """Исполнитель и модель этапа. Порядок: модель этапа в проекте → одна модель проекта →
    модель этапа исполнителя по умолчанию в config.json."""
    main = config.get("default_backend", "antigravity")
    for value in ((per_stage or {}).get(stage), chosen):
        if value:
            bid, mid = resolve_model(config, value)
            return bid or main, mid
    return main, config.get("backends", {}).get(main, {}).get("stage_models", {}).get(stage, "")


def model_label(config: dict, backend_id: str, model_id: str) -> str:
    for m in config.get("backends", {}).get(backend_id, {}).get("models", []):
        if m.get("id") == model_id:
            return m.get("label", model_id)
    return model_id or "по умолчанию"


def short_backend(backend_id: str) -> str:
    return {"antigravity": "agy", "claude": "Claude Code"}.get(backend_id, backend_id)


__all__ = [
    "REGISTRY",
    "STAGES",
    "Backend",
    "CallResult",
    "get_backend",
    "list_backends",
    "model_label",
    "resolve_model",
    "short_backend",
    "stage_choice",
]
