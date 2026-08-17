import uuid
from pathlib import Path
from typing import Protocol

from bot.models.prompt import I2VPrompt, PromptModelType
from bot.models.saved_prompt import SavedPrompt


class PromptStorageService(Protocol):
    """Persists parsed prompts. File-backed for now, no database."""

    async def save_i2v(self, data: I2VPrompt) -> SavedPrompt: ...

    async def list_all(self) -> list[SavedPrompt]: ...

    async def get(self, prompt_id: str) -> SavedPrompt | None: ...

    async def delete(self, prompt_id: str) -> bool: ...

    async def rename(self, prompt_id: str, new_title: str) -> SavedPrompt | None: ...


class FilePromptStorageService:
    """One JSON file per prompt under `directory`, named by prompt id."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)

    async def save_i2v(self, data: I2VPrompt) -> SavedPrompt:
        saved = SavedPrompt(id=uuid.uuid4().hex, model_type=PromptModelType.I2V, data=data)
        self._path_for(saved.id).write_text(saved.model_dump_json(indent=2), encoding="utf-8")
        return saved

    async def list_all(self) -> list[SavedPrompt]:
        prompts = [
            SavedPrompt.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self._directory.glob("*.json")
        ]
        return sorted(prompts, key=lambda p: p.data.title.casefold())

    async def get(self, prompt_id: str) -> SavedPrompt | None:
        path = self._path_for(prompt_id)
        if not path.exists():
            return None
        return SavedPrompt.model_validate_json(path.read_text(encoding="utf-8"))

    async def delete(self, prompt_id: str) -> bool:
        path = self._path_for(prompt_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def rename(self, prompt_id: str, new_title: str) -> SavedPrompt | None:
        saved = await self.get(prompt_id)
        if saved is None:
            return None
        saved.data.title = new_title
        self._path_for(saved.id).write_text(saved.model_dump_json(indent=2), encoding="utf-8")
        return saved

    def _path_for(self, prompt_id: str) -> Path:
        return self._directory / f"{prompt_id}.json"
