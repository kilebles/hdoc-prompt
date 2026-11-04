"""Тесты модуля промптов: временная папка данных, тестовый исполнитель, подставной API «Сценария»."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
DATA = Path(__file__).resolve().parent / "data"

FAKE_NICHES = [{"slug": "victorian", "name": "Victorian London", "description": "", "has_profile": True}]
FAKE_PASSPORT = "Audience: adults 35+. Tone: calm documentary."


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPTGEN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("PROMPTGEN_BACKEND", "fake")
    for k in ("FAKE_BAD", "FAKE_DELAY", "FAKE_LIMIT_ON", "FAKE_BRIEF_SKIP"):
        monkeypatch.delenv(k, raising=False)
    from promptgen import sources

    def fake_get(self, path):
        if path == "/niches":
            return FAKE_NICHES
        if path == "/niches/victorian":
            return {"slug": "victorian", "name": "Victorian London", "profile": FAKE_PASSPORT}
        if path == "/projects":
            return [{"id": "abc123", "title": "Victorian 283", "niche": "victorian", "niche_name": "Victorian London",
                     "status": "done", "params": {"language": "en"}},
                    {"id": "draft1", "title": "Draft", "niche": "victorian", "status": "draft"}]
        if path == "/projects/abc123/script":
            paras = script_paragraphs()
            return {"title": "Victorian 283", "language": "en",
                    "chapters": [{"number": 1, "title": "Intro", "paragraphs": [{"n": i + 1, "text": t} for i, t in
                                                                                enumerate(paras[:10])]},
                                 {"number": 2, "title": "Body", "paragraphs": [{"n": i + 11, "text": t} for i, t in
                                                                               enumerate(paras[10:])]}]}
        raise sources.SourceError("not_found", "нет")

    monkeypatch.setattr(sources.ScenarioClient, "_get", fake_get)
    yield tmp_path


def script_paragraphs():
    return [p.strip() for p in (DATA / "script_victorian_24.txt").read_text(encoding="utf-8").split("\n\n") if p.strip()]


def template_text(kind: str) -> str:
    name = "vlog_victorian_v2.txt" if kind == "avatar" else "victorian_veo_3_1.txt"
    return (DATA / name).read_text(encoding="utf-8")


@pytest.fixture
def templates_ready():
    from promptgen import templates as tpl
    from promptgen import worker

    tpl.save("victorian", "regular", template_text("regular"), "Victorian_VEO_3_1.txt")
    tpl.save("victorian", "avatar", template_text("avatar"), "VLOG_VICTORIAN_v2.txt")
    worker.run_tasks_once()
    return tpl


os.environ.setdefault("PROMPTGEN_BACKEND", "fake")
