"""Откуда берутся ниша и сценарий.

- Ниши, паспорт ниши и готовые сценарии — у вкладки «Сценарий» (fl-scenario, API на этом же сервере,
  по умолчанию http://127.0.0.1:8020). Сценарий берётся из script.json проекта: абзацы уже пронумерованы
  сквозь все главы.
- Сценарий можно и загрузить файлом .docx / .txt / .md (для сценариев, написанных вне генератора).
  Заголовки («Глава 2», «Introduction», строки капсом, оглавление) отбрасываются тем же разбором,
  что у бота; нумерация «12.» в начале абзаца снимается.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from bot.services.scenario_parser_service import ScenarioParserServiceImpl

LEADING_NUMBER = re.compile(r"^\s*\(?\d{1,4}[.)]\s+")
MAX_PARAGRAPHS = 1500


class SourceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ======================================================================= файл
def parse_file(name: str, data: bytes) -> list[str]:
    """Абзацы сценария из файла."""
    low = (name or "").lower()
    parser = ScenarioParserServiceImpl()
    try:
        if low.endswith(".docx"):
            paragraphs = parser.parse_docx(data)
        elif low.endswith((".txt", ".md")):
            paragraphs = parser.parse_txt(data.decode("utf-8-sig", errors="replace").encode("utf-8"))
        else:
            raise SourceError("bad_file", "Подходят файлы .docx, .txt и .md")
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001 — битый docx и т.п.
        raise SourceError("bad_file", f"Не удалось прочитать {name}: {e}") from None
    return clean_paragraphs(paragraphs)


def clean_paragraphs(paragraphs: list[str]) -> list[str]:
    out = []
    for p in paragraphs:
        p = LEADING_NUMBER.sub("", re.sub(r"\s+", " ", p or "")).strip()
        if p:
            out.append(p)
    if not out:
        raise SourceError("empty", "В сценарии не нашлось ни одного абзаца")
    if len(out) > MAX_PARAGRAPHS:
        raise SourceError("too_large", f"В сценарии {len(out)} абзацев — больше {MAX_PARAGRAPHS}")
    return out


# ======================================================================= вкладка «Сценарий»
class ScenarioClient:
    """Клиент API fl-scenario (/api/scenario/*)."""

    def __init__(self, config: dict[str, Any]):
        sc = config.get("scenario_api") or {}
        self.url = (os.environ.get("SCENARIO_API_URL") or sc.get("url") or "http://127.0.0.1:8020").rstrip("/")
        self.token = os.environ.get("SCENARIO_API_TOKEN") or sc.get("token") or os.environ.get("API_TOKEN", "")

    def _get(self, path: str) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = httpx.get(self.url + "/api/scenario" + path, headers=headers, timeout=15)
        except httpx.HTTPError as e:
            raise SourceError("scenario_offline", f"Вкладка «Сценарий» недоступна ({self.url}): {e}") from None
        if r.status_code == 404:
            raise SourceError("not_found", "Во вкладке «Сценарий» такого нет")
        if r.status_code == 401:
            raise SourceError("scenario_auth", "API «Сценария» не принял токен — проверьте API_TOKEN в .env")
        if r.status_code >= 400:
            raise SourceError("scenario_error", f"«Сценарий» ответил {r.status_code}: {r.text[:200]}")
        return r.json()

    def niches(self) -> list[dict[str, Any]]:
        return [{"slug": n["slug"], "name": n.get("name", n["slug"]), "description": n.get("description", ""),
                 "has_passport": bool(n.get("has_profile"))} for n in self._get("/niches")]

    def niche(self, slug: str) -> dict[str, Any]:
        n = self._get(f"/niches/{slug}")
        return {"slug": n["slug"], "name": n.get("name", slug), "description": n.get("description", ""),
                "passport": n.get("profile", "") or ""}

    def scripts(self, niche: str = "") -> list[dict[str, Any]]:
        """Проекты «Сценария», у которых сценарий уже готов."""
        out = []
        for p in self._get("/projects"):
            if p.get("status") != "done":
                continue
            if niche and p.get("niche") != niche:
                continue
            out.append({"id": p["id"], "title": p.get("title", ""), "niche": p.get("niche", ""),
                        "niche_name": p.get("niche_name", ""), "updated": p.get("updated"),
                        "language": (p.get("params") or {}).get("language", "")})
        return out

    def script(self, pid: str) -> dict[str, Any]:
        s = self._get(f"/projects/{pid}/script")
        paragraphs = [it.get("text", "") for ch in s.get("chapters", []) for it in ch.get("paragraphs", [])]
        return {"title": s.get("title", ""), "language": s.get("language", ""),
                "paragraphs": clean_paragraphs(paragraphs)}


def passport_or_empty(client: ScenarioClient | None, slug: str) -> dict[str, str]:
    """Паспорт ниши; если «Сценарий» недоступен — пусто (бриф строится по сценарию и шаблону)."""
    if not client or not slug:
        return {"name": slug, "passport": ""}
    try:
        n = client.niche(slug)
        return {"name": n["name"], "passport": n["passport"]}
    except SourceError:
        return {"name": slug, "passport": ""}
