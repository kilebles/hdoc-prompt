"""Проверки и чистка текста промптов — всё, что делает код, а не модель.

- дословность фиксированных блоков шаблона (модель не должна их пересказывать);
- запрещённые для модерации слова (жёсткие → переписать сцену, мягкие → пометить);
- тире и дефисы как пунктуация, «..», «Avoid: avoid», скобки-заглушки;
- годы, которых нет ни в абзаце, ни в эпохе абзаца;
- реплики ведущей (аватар): длина, повтор, дословное копирование сценария.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

# Слова, на которых модерация Google чаще всего режет промпт. Жёсткие — сцену переписываем,
# мягкие — только помечаем (в фильмах о сражениях без них не обойтись). Список можно
# переопределить в config.json → checks.moderation_hard / moderation_soft.
MODERATION_HARD = [
    "blood", "bloody", "bloodied", "bleeding", "gore", "gory", "corpse", "corpses", "dead body", "dead bodies",
    "murder", "murdered", "slaughter", "slaughtered", "massacre", "torture", "tortured", "rape", "raped",
    "naked", "nude", "nudity", "suicide", "decapitated", "beheaded", "severed", "mutilated", "dismembered",
    "abuse", "abused", "sexual", "erotic", "child labor", "child labour",
]
MODERATION_SOFT = [
    "wound", "wounds", "wounded", "injury", "injuries", "injured", "kill", "killed", "killing", "dying",
    "death", "dead", "violence", "violent", "cruelty", "starvation", "starving", "execution", "executed",
    "gallows", "whip", "whipped", "beating", "beaten", "weapon", "weapons", "crime", "intimacy",
]

# внешность ведущей в аватар-режиме берётся с референсов; описания фоновых персонажей сюда не попадают
FACE_WORDS = re.compile(
    r"\b(freckle[sd]?|auburn|her (?:eyes|hair|skin|complexion|cheekbones|lashes|brows)|"
    r"his (?:eyes|hair|skin|complexion|cheekbones|lashes|brows|beard)|her face is|his face is)\b", re.IGNORECASE)
SOUND_IN_IMAGE = re.compile(r"\b(can be heard|is heard|are heard|the sound of|sounds of|audible|hiss of|"
                            r"murmur of|clatter of|AUDIO:)", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>")
TEMPLATE_WORDS = ("insert here", "slot", "current paragraph", "line or silence", "scene action",
                  "selector", "placeholder")
YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
QUOTE = re.compile(r'"([^"]*)"')
DASH_PUNCT = re.compile(r"\s*[—–]\s*|\s+-\s+|\s*--\s*")
HYPHEN_COMPOUND = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")
SPACES = re.compile(r"\s+")


# ------------------------------------------------------------------ сравнение текста
def norm(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[`*_#>]", " ", t)  # разметка markdown в шаблоне
    t = t.replace('"', " ")
    return SPACES.sub(" ", t).strip(" .,;:")


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 2]


def verbatim_in(fragment: str, source: str) -> bool:
    """Каждое предложение фрагмента встречается в источнике (без учёта регистра, пробелов, кавычек)."""
    src = norm(source)
    return all(norm(s) in src for s in sentences(fragment))


# ------------------------------------------------------------------ модерация
def moderation_hits(text: str, words: Sequence[str]) -> list[str]:
    low = (text or "").lower()
    hits = []
    for w in words:
        if re.search(r"\b" + re.escape(w.lower()) + r"\b", low):
            hits.append(w)
    return hits


# ------------------------------------------------------------------ чистка
def fix_dashes(text: str) -> str:
    """«—», «–», « - » → запятая; «on-screen» → «on screen» (шаблоны требуют открытые составные слова)."""
    t = DASH_PUNCT.sub(", ", text)
    t = HYPHEN_COMPOUND.sub(" ", t)
    return re.sub(r",\s*,", ",", t)


def one_line(text: str) -> str:
    """Одна строка для ячейки таблицы (так удобнее вставлять в Google Sheets)."""
    return SPACES.sub(" ", (text or "").replace("\r", " ").replace("\n", " ")).strip()


def tidy(text: str) -> str:
    t = one_line(text)
    t = re.sub(r"(?<!\.)\.\s*\.(?!\.)", ".", t)          # «..» (но не «...»)
    t = re.sub(r"\.\s*,", ",", t)
    t = re.sub(r",\s*\.", ".", t)
    t = re.sub(r"\bAvoid:\s*avoid\b", "Avoid:", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+([.,;:])", r"\1", t)
    return t.strip()


def join_blocks(*parts: str) -> str:
    """Сцена + фиксированные блоки: каждый блок заканчивается точкой, без «..»."""
    out: list[str] = []
    for p in parts:
        p = one_line(p)
        if not p:
            continue
        if out and not re.search(r"[.!?\"']$", out[-1]):
            out[-1] += "."
        out.append(p)
    return tidy(" ".join(out))


def strip_fixed(scene: str, blocks: Iterable[str]) -> str:
    """Если модель сама вставила фиксированный блок (стиль, негативы), вырезаем его: код добавит свой."""
    s = one_line(scene)
    for b in blocks:
        for sent in sentences(b):
            if len(sent) < 25:
                continue
            idx = s.lower().find(one_line(sent).lower())
            if idx >= 0:
                s = (s[:idx] + s[idx + len(one_line(sent)):]).strip()
    # хвост «Avoid: …» / «Style: …», который модель дописала от себя
    s = re.split(r"\b(?:Avoid:|Style:|VLOG PERSPECTIVE|CONTINUITY:|CAMERA:\s+Constant)", s)[0]
    return tidy(s)


# ------------------------------------------------------------------ годы
def years(text: str) -> set[str]:
    return set(YEAR.findall(text or ""))


def year_drift(scene: str, allowed: set[str]) -> set[str]:
    """Годы в сцене, которых нет ни в абзаце, ни в датах его эпохи (признак ухода в чужое время)."""
    found = years(scene)
    if not found or not allowed:
        return set()
    return found - allowed


# ------------------------------------------------------------------ реплики
def spoken(text: str) -> list[str]:
    return [q.strip() for q in QUOTE.findall(text or "")]


def copied_from(line: str, source: str, run: int = 8) -> bool:
    """В реплике есть run слов подряд из сценария — значит, переписано, а не пересказано."""
    words = re.findall(r"[a-z0-9']+", (line or "").lower())
    src = " " + " ".join(re.findall(r"[a-z0-9']+", (source or "").lower())) + " "
    for i in range(max(0, len(words) - run + 1)):
        if " " + " ".join(words[i:i + run]) + " " in src:
            return True
    return False


def placeholder_issue(text: str, extra_words: Sequence[str] = ()) -> str | None:
    m = PLACEHOLDER.search(text or "")
    if m:
        return f"заглушка {m.group(0)[:40]}"
    low = (text or "").lower()
    for w in list(TEMPLATE_WORDS) + [x.lower() for x in extra_words if x]:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            return f"служебное слово «{w}»"
    return None


def describe(problems: dict[str, str]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in problems.items())
