"""Бриф («библия сценария») — один вызов на весь сценарий.

Модель читает весь сценарий целиком вместе с мастер-промптом, паспортом ниши и заметками
и выписывает то, что должно быть одинаковым во всех промптах:
    эпоха и под-периоды с материальной культурой, палитрой, светом и фактурами;
    ведущий (аватар): одна линия одежды, голос, смены одежды по абзацам;
    повторяющиеся персонажи и места — фиксированные описания, которые потом вставляются слово в слово;
    особые ограничения для отдельных абзацев;
    по каждому абзацу: под-период, место, что происходит (заменяет посабзацную «сводку»),
    кто в кадре и до 3 предметов для поиска на стоках (заменяет отдельный подбор объектов).
Сценарий длиннее brief.max_paragraphs_per_call абзацев делится на части: общая часть берётся
из первого ответа, следующие вызовы дописывают только абзацы.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import textcheck as tc
from .caller import BadAnswer, Caller, FormatFailed

STOCK_RULES = """Stock subjects ("stock"): up to 3 per paragraph, only concrete, physically real, photographable things \
a documentary camera could film a real surviving example of today: an artifact, tool, weapon, container, garment, \
piece of regalia, a building or structure itself, a written record or symbol, or a person described by role and \
attire (never by proper name). Never landscapes or scenery, events or actions, abstract ideas, or proper names. \
Never replace a historical object with its modern equivalent (a carrying frame is not a "backpack"); if you do not \
know the exact historical term, describe material, shape and function. Do not add the era, the program adds it. \
Fewer than 3, or none, is fine."""

SYSTEM = """You prepare the PRODUCTION BRIEF for an AI prompt writer. Later, the writer turns every paragraph of the \
script into image and video prompts following the MASTER TEMPLATE below, a few paragraphs at a time, without seeing \
the whole script. Your brief is the single source of truth that keeps the whole film consistent: the era of every \
paragraph, identical descriptions of recurring characters and places, and in avatar mode the host's clothing and voice.

Do the "read the whole script once and extract" steps the template asks for (eras, sub periods, figures, face policy, \
sensitive paragraphs, clothing line, character cues and so on) and write the results into the brief. Ignore the \
template's instructions about output layout, batching, confirmations and reminders: you output only the JSON below.
PRODUCER NOTES, if present, have the highest priority and override the template and the passport where they conflict.
All brief text in English."""

GLOBAL_SPEC = """Return ONLY this JSON object:
{
  "title": "<short title of the script>",
  "era": "<broad era of the whole script>",
  "synopsis": "<3 to 5 sentences: the story the whole script tells>",
  "sub_periods": [{"id": "p1", "name": "<label the prompts open with>", "when": "<years or phase as the script states>",
                   "description": "<one sentence>",
                   "material": "<material culture as short clauses: technology and tools; architecture and interiors; clothing; food; money; institutions>",
                   "palette": "<dominant colours tied to objects>", "lighting": "<light sources>", "textures": "<surfaces>"}],
  "host": HOST_SPEC,
  "characters": [{"name": "<how the script calls them>", "cue": "<fixed visual cue: role, age range, build, hair or beard, clothing, one prop>",
                  "likeness": "<for named real historical figures only: what the documented likeness is based on; else empty>"}],
  "locations": [{"id": "<short_id>", "cue": "<one sentence fixed visual description of the place>"}],
  "restrictions": [{"paragraph": <n>, "rule": "<how to show a graphic or sensitive event: aftermath, distance, atmosphere, implication>"}],
  "paragraphs": [PARAGRAPH_SPEC]
}"""

PARAGRAPH_SPEC = """{"n": <paragraph number>, "period": "<sub period id>", "location": "<location id, or a short new place>",
                  "beat": "<one sentence: what is physically happening and visible in this paragraph; for commentary paragraphs, a plausible small scene in the surrounding setting>",
                  "characters": ["<names from characters present>"], "stock": ["<subject>", "..."]}"""

HOST_REGULAR = "null"
HOST_AVATAR = """{"gender": "female" or "male",
           "clothing": "<the ONE host clothing line, finished text, written in full>",
           "voice": "<the host's accent and register, one short phrase, e.g. soft working class London accent>",
           "changes": [{"from": <n>, "to": <n>, "clothing": "<full clothing line for that stretch>"}]}"""


def _numbered(paragraphs: list[str]) -> str:
    return "\n\n".join(f'PARAGRAPH {i}: "{t}"' for i, t in enumerate(paragraphs, 1))


def build_system(template_text: str, passport: str, notes: str) -> str:
    parts = [SYSTEM, "# MASTER TEMPLATE\n\n" + template_text.strip()]
    if passport.strip():
        parts.append("# NICHE PASSPORT (audience, tone and format of the channel; use it as background)\n\n"
                     + passport.strip())
    if notes.strip():
        parts.append("# PRODUCER NOTES (highest priority)\n\n" + notes.strip())
    return "\n\n---\n\n".join(parts)


def make_brief(*, caller: Caller, template: dict[str, Any], mode: str, paragraphs: list[str], passport: str,
               notes: str, title: str, max_per_call: int, emit: Callable[[str, str], None]) -> dict[str, Any]:
    system = build_system(template["text"], passport, notes)
    total = len(paragraphs)
    chunk = max(10, int(max_per_call or 150))
    host_spec = HOST_AVATAR if mode == "avatar" else HOST_REGULAR
    spec = GLOBAL_SPEC.replace("HOST_SPEC", host_spec).replace("PARAGRAPH_SPEC", PARAGRAPH_SPEC)
    first_to = min(total, chunk)
    head = (f"# SCRIPT ({total} paragraphs){': ' + title if title else ''}\n\n{_numbered(paragraphs)}\n\n---\n\n"
            f"{spec}\n\n{STOCK_RULES}\n\n")
    task = (f"Write the brief. In \"paragraphs\" include exactly one entry for every paragraph from 1 to {first_to}"
            + (f" (the rest, {first_to + 1} to {total}, will be requested separately)" if first_to < total else "")
            + ", in order.")
    if mode == "avatar":
        task += " The host object is required."
    emit("info", f"Бриф: читаю сценарий целиком ({total} абзацев)")
    data = caller.json("brief", system, head + task, "brief",
                       lambda d: _check(d, 1, first_to, mode, global_part=True))
    brief = _normalize_global(data, mode)
    items = {int(p["n"]): p for p in data.get("paragraphs", []) if _num(p) is not None}

    start = first_to + 1
    while start <= total:
        end = min(total, start + chunk - 1)
        emit("info", f"Бриф: абзацы {start}–{end}")
        g = {k: v for k, v in brief.items() if k != "paragraphs"}
        prompt = (f"# SCRIPT ({total} paragraphs)\n\n{_numbered(paragraphs)}\n\n---\n\n# GLOBAL BRIEF (already made: "
                  f"use exactly these sub period ids, character names and location ids)\n\n{_dump(g)}\n\n---\n\n"
                  f'Return ONLY {{"paragraphs": [{PARAGRAPH_SPEC}]}} with exactly one entry for every paragraph '
                  f"from {start} to {end}.\n\n{STOCK_RULES}")
        part = caller.json("brief", system, prompt, f"brief_{start}_{end}",
                           lambda d, a=start, b=end: _check(d, a, b, mode, global_part=False))
        for p in part.get("paragraphs", []):
            if _num(p) is not None:
                items[int(p["n"])] = p
        start = end + 1

    missing = [n for n in range(1, total + 1) if n not in items]
    if missing:
        emit("warn", f"Бриф: модель пропустила абзацы {_ranges(missing)} — дозапрашиваю")
        g = {k: v for k, v in brief.items() if k != "paragraphs"}
        prompt = (f"# SCRIPT\n\n{_numbered(paragraphs)}\n\n---\n\n# GLOBAL BRIEF\n\n{_dump(g)}\n\n---\n\n"
                  f'Return ONLY {{"paragraphs": [{PARAGRAPH_SPEC}]}} for these paragraphs: '
                  f"{', '.join(map(str, missing))}.\n\n{STOCK_RULES}")
        try:
            part = caller.json("brief", system, prompt, "brief_missing", lambda d: _check_list(d))
            for p in part.get("paragraphs", []):
                if _num(p) is not None:
                    items.setdefault(int(p["n"]), p)
        except FormatFailed:
            pass
    brief["paragraphs"] = [_normalize_paragraph(items.get(n), n, paragraphs[n - 1], brief)
                           for n in range(1, total + 1)]
    still = [n for n in range(1, total + 1) if n not in items]
    brief["filled_by_program"] = still
    if still:
        emit("warn", f"Бриф: для абзацев {_ranges(still)} модель так и не ответила — заполнил по тексту абзаца")
    return brief


# ------------------------------------------------------------------ проверка ответа
def _num(p: Any) -> int | None:
    try:
        return int(p.get("n")) if isinstance(p, dict) else None
    except (TypeError, ValueError):
        return None


def _check_list(d: Any) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("paragraphs"), list):
        raise BadAnswer('нужен объект {"paragraphs": [...]}')


def _check(d: Any, a: int, b: int, mode: str, global_part: bool) -> None:
    _check_list(d)
    got = {_num(p) for p in d["paragraphs"]}
    need = set(range(a, b + 1))
    lost = need - got
    if len(lost) > max(2, len(need) // 5):  # мелкие пропуски дозапросим отдельно
        raise BadAnswer(f"в paragraphs нет {len(lost)} абзацев из {len(need)} (например {sorted(lost)[:5]})")
    if global_part:
        if not isinstance(d.get("sub_periods"), list) or not d["sub_periods"]:
            raise BadAnswer("нет sub_periods")
        if mode == "avatar":
            host = d.get("host")
            if not isinstance(host, dict) or not str(host.get("clothing", "")).strip():
                raise BadAnswer("нет host.clothing — линии одежды ведущей")


# ------------------------------------------------------------------ нормализация
def _s(v: Any, limit: int = 2000) -> str:
    return tc.one_line(str(v or ""))[:limit]


def _normalize_global(d: dict[str, Any], mode: str) -> dict[str, Any]:
    periods = []
    seen = set()
    for i, p in enumerate(d.get("sub_periods") or [], 1):
        if not isinstance(p, dict):
            continue
        pid = _s(p.get("id")) or f"p{i}"
        if pid in seen:
            pid = f"{pid}_{i}"
        seen.add(pid)
        periods.append({k: _s(p.get(k)) for k in ("name", "when", "description", "material", "palette",
                                                    "lighting", "textures")} | {"id": pid})
    for p in periods:
        p["name"] = p["name"] or p["id"]
    host = None
    if mode == "avatar" and isinstance(d.get("host"), dict):
        h = d["host"]
        changes = []
        for c in h.get("changes") or []:
            try:
                changes.append({"from": int(c["from"]), "to": int(c["to"]), "clothing": _s(c.get("clothing"))})
            except (KeyError, TypeError, ValueError):
                continue
        host = {"gender": "male" if str(h.get("gender", "")).lower().startswith("m") else "female",
                "clothing": _s(h.get("clothing")), "voice": _s(h.get("voice")),
                "changes": [c for c in changes if c["clothing"]]}
    chars = [{"name": _s(c.get("name"), 200), "cue": _s(c.get("cue"), 600), "likeness": _s(c.get("likeness"), 300)}
             for c in d.get("characters") or [] if isinstance(c, dict) and c.get("name")]
    locs = [{"id": _s(x.get("id"), 80), "cue": _s(x.get("cue"), 600)}
            for x in d.get("locations") or [] if isinstance(x, dict) and x.get("id")]
    restr = []
    for r in d.get("restrictions") or []:
        try:
            restr.append({"paragraph": int(r["paragraph"]), "rule": _s(r.get("rule"), 600)})
        except (KeyError, TypeError, ValueError):
            continue
    return {"title": _s(d.get("title"), 300), "era": _s(d.get("era"), 300), "synopsis": _s(d.get("synopsis"), 3000),
            "sub_periods": periods, "host": host, "characters": chars, "locations": locs, "restrictions": restr}


def _normalize_paragraph(p: dict[str, Any] | None, n: int, text: str, brief: dict[str, Any]) -> dict[str, Any]:
    periods = {x["id"]: x for x in brief["sub_periods"]}
    if not p:
        prev = brief.get("_last_period") or (brief["sub_periods"][0]["id"] if brief["sub_periods"] else "")
        first = tc.sentences(text)[:1]
        return {"n": n, "period": prev, "location": "", "beat": first[0] if first else text[:200],
                "characters": [], "stock": []}
    period = _s(p.get("period"), 80)
    if period not in periods:  # неизвестный id — берём по имени, иначе как у предыдущего абзаца
        by_name = {x["name"].lower(): x["id"] for x in brief["sub_periods"]}
        period = by_name.get(period.lower()) or brief.get("_last_period") or (
            brief["sub_periods"][0]["id"] if brief["sub_periods"] else "")
    brief["_last_period"] = period
    stock = []
    for s in p.get("stock") or []:
        s = _s(s, 160).strip(" .,;")
        if s and s.lower() not in (x.lower() for x in stock):
            stock.append(s)
    return {"n": n, "period": period, "location": _s(p.get("location"), 300), "beat": _s(p.get("beat"), 600),
            "characters": [_s(c, 200) for c in p.get("characters") or [] if str(c).strip()][:8],
            "stock": stock[:3]}


def finalize(brief: dict[str, Any]) -> dict[str, Any]:
    brief.pop("_last_period", None)
    return brief


def _dump(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=1)


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}–{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}–{prev}" if start != prev else str(start))
    return ", ".join(out)


# ------------------------------------------------------------------ помощники для генерации
def period_of(brief: dict[str, Any], n: int) -> dict[str, Any]:
    pid = brief["paragraphs"][n - 1]["period"]
    for p in brief["sub_periods"]:
        if p["id"] == pid:
            return p
    return brief["sub_periods"][0] if brief["sub_periods"] else {"id": "", "name": "", "when": ""}


def clothing_for(brief: dict[str, Any], n: int) -> str:
    host = brief.get("host") or {}
    for c in host.get("changes") or []:
        if c["from"] <= n <= c["to"]:
            return c["clothing"]
    return host.get("clothing", "")


def location_cue(brief: dict[str, Any], loc: str) -> str:
    for x in brief.get("locations") or []:
        if x["id"] == loc:
            return x["cue"]
    return loc


def stock_queries(brief: dict[str, Any], n: int) -> list[str]:
    """Запросы для стоков: предмет + под-период абзаца (эпоху ставит код, а не модель)."""
    era = period_of(brief, n).get("name", "")
    return [f"{s}, {era}" if era else s for s in brief["paragraphs"][n - 1].get("stock", [])]
