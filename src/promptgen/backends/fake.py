"""Тестовый исполнитель: ничего не вызывает, отвечает правдоподобными заглушками по этапу.

Нужен, чтобы проверить весь путь (очередь, бриф, пачки, проверки, повторы, xlsx) без расхода подписки:
    PROMPTGEN_BACKEND=fake python -m promptgen.worker --once
FAKE_DELAY=2   — каждый вызов идёт 2 с (проверка прогресса на сайте);
FAKE_BAD=3     — абзац 3 в первой попытке получает «плохую» сцену (проверка повторов);
FAKE_LIMIT_ON=2 — 2-й вызов изображает исчерпанный лимит.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .base import Backend, CallResult

_lock = threading.Lock()


class FakeBackend(Backend):
    id = "fake"
    calls = 0

    def run(self, system: str, prompt: str, model: str | None, workdir: Path, timeout: int,
            stage: str = "", web_search: bool = False) -> CallResult:
        with _lock:
            FakeBackend.calls += 1
            n = FakeBackend.calls
        if os.environ.get("FAKE_DELAY"):
            time.sleep(float(os.environ["FAKE_DELAY"]))
        if os.environ.get("FAKE_LIMIT_ON") == str(n):
            return CallResult("limit", error="usage limit reached (fake)", retry_at=time.time() + 1)
        if stage == "template":
            data = _template(prompt)
        elif stage == "brief":
            data = _brief(prompt)
        elif stage == "prompts":
            data = _prompts(system, prompt)
        else:
            data = {}
        return CallResult("ok", text="```json\n" + json.dumps(data, ensure_ascii=False) + "\n```", seconds=0.01)


def _section(raw: str, title: str) -> str:
    m = re.search(r"^#+\s*" + re.escape(title) + r"[^\n]*\n(.*?)(?=^#+\s|\Z)", raw, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def _fixed(section: str) -> list:
    blocks = re.findall(r"```\n?(.*?)```", section, re.DOTALL)
    text = blocks[0] if blocks else section
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if re.match(r"(VLOG PERSPECTIVE|Style:|CONTINUITY:|CAMERA:)", para) and "{" not in para:
            out += [ln.strip() for ln in para.split("\n") if ln.strip()]
    m = re.search(r'fixed style tag string: "([^"]+)"', section)
    if m:
        out.append(m.group(1))
    return out


def _template(prompt: str) -> dict:
    raw = prompt.split("# MASTER PROMPT DOCUMENT", 1)[-1]
    avatar = "avatar" in raw.lower() and "selfie" in raw.lower()
    neg_lines = [ln.strip() for ln in _section(raw, "NEGATIVE CONSTRAINTS").split("\n") if ln.strip()]
    writer = [ln for ln in neg_lines if ln.lower().startswith("handle ") or "downton" in ln.lower()]
    img = _fixed(_section(raw, "IMAGE PROMPT INSTRUCTIONS"))
    vid = _fixed(_section(raw, "VIDEO PROMPT INSTRUCTIONS")) or img[-1:]
    if avatar:
        shots = [{"title": "PRIMARY BEAT", "description": "main action"},
                 {"title": "FOLLOWUP BEAT", "description": "a later moment"}]
    else:
        names = re.findall(r"^Pair (\d+), ([A-Z ]+)\.", raw, re.MULTILINE)
        shots = [{"title": t, "description": f"pair {k}"} for k, t in names] or \
                [{"title": f"SHOT {i}", "description": "shot"} for i in range(1, 9)]
        shots += [{"title": s["title"] + " VARIANT", "description": "variant"} for s in list(shots)]
    return {"kind": "avatar" if avatar else "regular", "targets": {"image": "Nano Banana", "video": "Google Veo 3.1"},
            "shots": shots, "count_selectable": not avatar, "image_fixed": img, "video_fixed": vid,
            "negatives": neg_lines + ["Avoid invented things that are not in the template at all"],
            "negatives_target": "both", "writer_only": writer,
            "scene_limits": {"image": 700 if avatar else 400, "video": 600 if avatar else 400},
            "spoken": {"max_chars": 80, "language": "English"} if avatar else None,
            "rules": {"no_dashes": True, "english_only": True}, "forbidden_words": ["insert", "slot"],
            "image_scene_format": "scene", "video_scene_format": "motion", "notes": []}


def _brief(prompt: str) -> dict:
    texts = {int(k): v for k, v in re.findall(r'PARAGRAPH (\d+): "(.*?)"(?=\n\nPARAGRAPH|\n\n---)', prompt, re.DOTALL)}
    m = re.search(r"every paragraph (?:from )?(\d+) (?:to|from \d+ to) (\d+)", prompt)
    rng = re.search(r"from (\d+) to (\d+)", prompt)
    lst = re.search(r"for these paragraphs: ([\d, ]+)\.", prompt)
    if lst:
        nums = [int(x) for x in lst.group(1).split(",")]
    elif rng:
        nums = list(range(int(rng.group(1)), int(rng.group(2)) + 1))
    elif m:
        nums = list(range(int(m.group(1)), int(m.group(2)) + 1))
    else:
        nums = sorted(texts)
    paras = []
    for n in nums:
        t = texts.get(n, "")
        words = [w for w in re.findall(r"[A-Za-z]{5,}", t)][:3]
        paras.append({"n": n, "period": "p1" if n % 7 else "p2", "location": "passage", "beat": t[:120] or f"scene {n}",
                      "characters": ["the butler"] if "butler" in t.lower() else [],
                      "stock": [f"{w.lower()} object" for w in words[:2]]})
    if os.environ.get("FAKE_BRIEF_SKIP"):
        paras = [p for p in paras if p["n"] != int(os.environ["FAKE_BRIEF_SKIP"])]
    data = {"paragraphs": paras}
    if "GLOBAL BRIEF" not in prompt:
        data.update({
            "title": "Fake title", "era": "Late Victorian", "synopsis": "A night below stairs.",
            "sub_periods": [{"id": "p1", "name": "Late Victorian", "when": "1880 to 1901", "description": "d",
                             "material": "gas brackets", "palette": "soot black", "lighting": "gaslight",
                             "textures": "wool"},
                            {"id": "p2", "name": "Late Victorian evening", "when": "1885", "description": "d",
                             "material": "m", "palette": "p", "lighting": "l", "textures": "t"}],
            "host": {"gender": "female", "clothing": "in her Late Victorian housemaid uniform: high neck black wool "
                     "dress, long white cotton pinafore apron", "voice": "soft working class London accent",
                     "changes": []} if "host clothing line" in prompt else None,
            "characters": [{"name": "the butler", "cue": "tall older man in a black tailcoat", "likeness": ""}],
            "locations": [{"id": "passage", "cue": "a stone service passage lit by one gas bracket"}],
            "restrictions": [{"paragraph": 2, "rule": "show only the aftermath"}],
        })
    return data


def _prompts(system: str, prompt: str) -> dict:
    m = re.search(r"Write (\d+) scenes for each of paragraphs ([\d, ]+)\.", prompt)
    count = int(m.group(1)) if m else 2
    nums = [int(x) for x in m.group(2).split(",")] if m else []
    avatar = "The host, exact appearance" in system
    clothing = dict(re.findall(r'## PARAGRAPH (\d+).*?Host clothing line: "([^"]*)"', prompt, re.DOTALL))
    bad = os.environ.get("FAKE_BAD")
    fixing = "REJECTED BY THE CHECKER" in prompt
    out = []
    for n in nums:
        scenes = []
        for i in range(1, count + 1):
            if avatar:
                img = (f"The host, exact appearance as the attached character sheet, {clothing.get(str(n), '')}. "
                       f"She holds the camera at arm's length in a selfie vlog position, standing in the stone "
                       f"passage with a tray, scene {n}.{i}, gas bracket light on rough walls.")
                vid = (f'0:00 to 0:03. She looks into the lens. "Right, number {n}." 0:03 to 0:06. She glances up. '
                       f'"" 0:06 to 0:10. She breathes out. "Off we go then." AUDIO: Soft working class London '
                       f"accent, native Veo 3.1 audio generation. Hiss of the gas jet. No music.")
            else:
                img = (f"Late Victorian, a stone service passage below a dining room, single gas bracket, damp brick "
                       f"and worn flagstones, shot {i} of paragraph {n}, 35mm at eye level")
                vid = (f"Gas flame flickers on the wall, the butler walks slowly past, camera holds, shot {i}, "
                       f"distant piano and the clink of glasses")
            if bad and str(n) == bad and not fixing and i == 1:
                img += " with blood on the floor"
            scenes.append({"img": img, "vid": vid})
        out.append({"n": n, "scenes": scenes})
    return {"paragraphs": out}
