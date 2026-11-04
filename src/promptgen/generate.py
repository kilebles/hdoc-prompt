"""Промпты картинка + видео по абзацам: пачки, параллельные вызовы, проверки кодом, сборка.

Как идёт генерация:
1. Абзацы делятся на пачки: при 16 парах на абзац — по одному абзацу за вызов, при аватаре (2 сцены) —
   по несколько (config.json → batch). Пачки уходят в модель параллельно (config.json → parallel).
2. Модель пишет только «сцену» каждого промпта. Фиксированные блоки шаблона (стиль, VLOG PERSPECTIVE,
   негативы) дописывает код дословно — поэтому они никогда не теряются, не пересказываются и не двоятся.
3. Каждый абзац проверяется кодом (textcheck): число сцен, длина, заглушки, годы чужой эпохи, слова,
   на которых режет модерация, у аватара — реплики (длина, повтор, копия текста сценария), описание
   лица, линия одежды. Абзацы с серьёзными замечаниями переписываются отдельно (до checks.fix_rounds
   раз) с перечнем замечаний; что не исправилось — сохраняется с пометкой и попадает в лист «Проверка».
4. Каждый готовый абзац сразу пишется в scenes/<n>.json — после остановки или сбоя продолжаем с места.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import brief as br
from . import textcheck as tc
from .caller import BadAnswer, Caller, FormatFailed, StopRequested
from .config import read_json, write_json
from .templates import negatives_text

Emit = Callable[[str, str], None]


# ======================================================================= подсказки модели
def shots_for(template: dict[str, Any], mode: str, pairs: int) -> list[dict[str, str]]:
    shots = template["analysis"]["shots"]
    if mode == "regular" and template["analysis"].get("count_selectable", True) and pairs:
        return shots[:max(1, min(pairs, len(shots)))]
    return shots


def build_system(template: dict[str, Any], brief: dict[str, Any], mode: str, shots: list[dict[str, str]],
                 config: dict[str, Any]) -> str:
    a = template["analysis"]
    lim = a.get("scene_limits") or {}
    fixed = [b for b in a.get("image_fixed", []) + a.get("video_fixed", [])] + a.get("negatives", [])
    fixed_hint = "\n".join(f'  - "{tc.one_line(b)[:90]}…"' for b in dict.fromkeys(fixed))
    shot_lines = "\n".join(f"  {i}. {s['title']}: {s['description']}" for i, s in enumerate(shots, 1))
    rules = [
        "You are the prompt writer described in the MASTER TEMPLATE below. Follow its writer rules exactly: era and "
        "material culture, light, textures, characters and faces, camera, shot structure, content safety, writing "
        "rules. Use the PRODUCTION BRIEF for everything that must stay consistent across the film.",
        "",
        "OUTPUT CONTRACT (overrides anything in the template about output layout, batching, waiting for "
        "confirmation, reminders or summaries):",
        f"- You get a few paragraphs of the script at a time. For EACH paragraph write exactly {len(shots)} scenes, "
        f"in this order:\n{shot_lines}",
        "- Every scene is one image prompt (\"img\") and one video prompt (\"vid\"); the video is generated from "
        "that image.",
        f"- In \"img\" write only the scene part: {a.get('image_scene_format') or 'the scene description'}",
        f"- In \"vid\" write only the scene part: {a.get('video_scene_format') or 'the motion and sound'}",
        "- Do NOT write the fixed blocks, style tags, \"Avoid:\" lists or negative constraints: the program appends "
        "them verbatim after your text. These are appended for you:\n" + (fixed_hint or "  (none)"),
    ]
    if lim.get("image"):
        rules.append(f"- \"img\" under {lim['image']} characters.")
    if lim.get("video"):
        rules.append(f"- \"vid\" under {lim['video']} characters.")
    if mode == "avatar":
        sp = a.get("spoken") or {}
        rules += [
            "- Start every \"img\" with: \"The host, exact appearance as the attached character sheet,\" followed by "
            "the host clothing line given for that paragraph, word for word.",
            "- Never describe the host's face, skin, eyes, hair, brows or build.",
            f"- Spoken lines: at most {sp.get('max_chars', 80)} characters per scene in total, "
            f"{sp.get('language', 'English')}, retold in the host's own words. Never copy a sentence or a long phrase "
            "from the script. Never repeat the same line in two beats. Silence is \"\".",
            "- No sounds in \"img\": sound belongs only in \"vid\".",
        ]
    else:
        rules.append("- No sounds in \"img\": sound belongs only in \"vid\".")
    if a.get("rules", {}).get("english_only", True):
        rules.append("- All prompt text in English.")
    if a.get("rules", {}).get("no_dashes"):
        rules.append("- No em dashes, en dashes or hyphens used as punctuation; compound words written open.")
    rules += [
        "- No square brackets, curly braces, angle brackets or template words in the prompts.",
        "- Calm, concrete, neutral wording that describes only what is in the frame. Google moderation reads every "
        "word, so never write words about blood, injury, death, violence, cruelty, nudity or intimacy, not even "
        "after \"no\" or \"avoid\"; show hard moments through aftermath, distance or a quiet place.",
        "- Return ONLY JSON: {\"paragraphs\": [{\"n\": <paragraph number>, \"scenes\": [{\"img\": \"...\", "
        "\"vid\": \"...\"}]}]}",
    ]
    return "\n".join(rules) + "\n\n---\n\n# MASTER TEMPLATE\n\n" + template["text"].strip() + \
        "\n\n---\n\n# PRODUCTION BRIEF\n\n" + brief_text(brief, mode)


def brief_text(brief: dict[str, Any], mode: str) -> str:
    lines = [f"Title: {brief.get('title', '')}", f"Era: {brief.get('era', '')}",
             f"Synopsis: {brief.get('synopsis', '')}", "", "Sub periods:"]
    for p in brief.get("sub_periods", []):
        lines.append(f"- [{p['id']}] {p['name']} ({p['when']}): {p['description']} Material culture: {p['material']}. "
                     f"Palette: {p['palette']}. Lighting: {p['lighting']}. Textures: {p['textures']}.")
    if mode == "avatar" and brief.get("host"):
        h = brief["host"]
        lines += ["", f"Host ({h.get('gender', 'female')}): clothing line \"{h.get('clothing', '')}\"; voice: "
                      f"{h.get('voice', '')}."]
    if brief.get("characters"):
        lines += ["", "Recurring characters (use the cue word for word every time they appear):"]
        for c in brief["characters"]:
            extra = f" Likeness: {c['likeness']}." if c.get("likeness") else ""
            lines.append(f"- {c['name']}: {c['cue']}.{extra}")
    if brief.get("locations"):
        lines += ["", "Places (fixed descriptions):"]
        lines += [f"- [{x['id']}] {x['cue']}" for x in brief["locations"]]
    return "\n".join(lines)


def build_prompt(nums: list[int], paragraphs: list[str], brief: dict[str, Any], mode: str, n_shots: int,
                 feedback: dict[int, str] | None = None) -> str:
    first = nums[0]
    ctx = [f"{k}: {brief['paragraphs'][k - 1]['beat']}" for k in range(max(1, first - 3), first)]
    out = []
    if ctx:
        out.append("STORY SO FAR (what the preceding paragraphs show):\n" + "\n".join(ctx))
    restr = {r["paragraph"]: r["rule"] for r in brief.get("restrictions", [])}
    for n in nums:
        b = brief["paragraphs"][n - 1]
        per = br.period_of(brief, n)
        block = [f"## PARAGRAPH {n}",
                 f"Sub period: [{per.get('id', '')}] {per.get('name', '')} ({per.get('when', '')})",
                 f"Place: {br.location_cue(brief, b['location'])}" if b.get("location") else "",
                 f"What happens: {b['beat']}",
                 f"Characters present: {', '.join(b['characters'])}" if b.get("characters") else ""]
        if mode == "avatar":
            block.append(f"Host clothing line: \"{br.clothing_for(brief, n)}\"")
        if n in restr:
            block.append(f"MANDATORY for this paragraph: {restr[n]}")
        if n == first and n > 1:
            block.append(f'Previous paragraph (for visual continuity): "{paragraphs[n - 2]}"')
        block.append(f'Text: "{paragraphs[n - 1]}"')
        if feedback and n in feedback:
            block.append("YOUR PREVIOUS ATTEMPT AT THIS PARAGRAPH WAS REJECTED BY THE CHECKER: " + feedback[n] +
                         ". Rewrite all scenes of this paragraph and fix exactly these problems.")
        out.append("\n".join(x for x in block if x))
    out.append(f"Write {n_shots} scenes for each of paragraphs {', '.join(map(str, nums))}.")
    return "\n\n".join(out)


# ======================================================================= проверка и сборка
def assemble(scene_img: str, scene_vid: str, template: dict[str, Any]) -> tuple[str, str, str, str]:
    """Чистит сцену и дописывает фиксированные блоки. Возвращает (сцена img, сцена vid, img, vid)."""
    a = template["analysis"]
    neg = negatives_text(a)
    fixed = a.get("image_fixed", []) + a.get("video_fixed", []) + a.get("negatives", [])
    si, sv = tc.strip_fixed(scene_img, fixed), tc.strip_fixed(scene_vid, fixed)
    if a.get("rules", {}).get("no_dashes"):
        si, sv = tc.fix_dashes(si), tc.fix_dashes(sv)
    tgt = a.get("negatives_target", "both")
    img = tc.join_blocks(si, *a.get("image_fixed", []), neg if tgt in ("both", "image") else "")
    vid = tc.join_blocks(sv, *a.get("video_fixed", []), neg if tgt in ("both", "video") else "")
    return si, sv, img, vid


def check_paragraph(n: int, text: str, scenes: list[dict[str, str]], n_shots: int, template: dict[str, Any],
                    brief: dict[str, Any], mode: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Собирает сцены абзаца и проверяет. Возвращает (сцены, серьёзные замечания, мягкие пометки)."""
    a = template["analysis"]
    ch = config.get("checks") or {}
    tol, hard_lim = float(ch.get("length_tolerance", 1.25)), float(ch.get("length_hard_limit", 1.6))
    sp_tol = float(ch.get("spoken_tolerance", 1.3))
    hard_words = ch.get("moderation_hard") or tc.MODERATION_HARD
    soft_words = ch.get("moderation_soft") or tc.MODERATION_SOFT
    lim = a.get("scene_limits") or {}
    per = br.period_of(brief, n)
    allowed = tc.years(text) | tc.years(per.get("when", "")) | tc.years(per.get("name", ""))
    clothing = br.clothing_for(brief, n) if mode == "avatar" else ""
    hard: dict[str, str] = {}
    soft: list[str] = []
    out = []
    if len(scenes) < n_shots:
        hard["сцены"] = f"{len(scenes)} из {n_shots}"
    for i, sc in enumerate(scenes[:n_shots], 1):
        si, sv, img, vid = assemble(str(sc.get("img", "")), str(sc.get("vid", "")), template)
        tag = f"{n}.{i}"
        out.append({"scene_img": si, "scene_vid": sv, "img": img, "vid": vid})
        for field, scene, limit in (("img", si, lim.get("image")), ("vid", sv, lim.get("video"))):
            if len(scene) < 40:
                hard[f"{tag} {field}"] = "пусто или слишком коротко"
                continue
            if limit and len(scene) > limit * hard_lim:
                hard[f"{tag} {field}"] = f"{len(scene)} символов при лимите {limit}"
            elif limit and len(scene) > limit * tol:
                soft.append(f"{tag} {field}: длиннее лимита ({len(scene)}/{limit})")
            ph = tc.placeholder_issue(scene, a.get("forbidden_words", []))
            if ph:
                hard[f"{tag} {field}"] = ph
            drift = tc.year_drift(scene, allowed)
            if drift:
                hard[f"{tag} {field}"] = f"годы {', '.join(sorted(drift))}, которых нет в абзаце и его эпохе"
            bad = tc.moderation_hits(scene, hard_words)
            if bad:
                hard[f"{tag} {field}"] = "слова для модерации: " + ", ".join(bad)
            mild = tc.moderation_hits(scene, soft_words)
            if mild:
                soft.append(f"{tag} {field}: {', '.join(mild)} (модерация)")
        if tc.SOUND_IN_IMAGE.search(si):
            soft.append(f"{tag} img: звук в промпте картинки")
        if mode == "avatar":
            if tc.FACE_WORDS.search(si):
                hard[f"{tag} img"] = f"описание внешности ведущей («{tc.FACE_WORDS.search(si).group(0)}»)"  # type: ignore[union-attr]
            if clothing and tc.norm(clothing) not in tc.norm(si):
                hard[f"{tag} img"] = "нет линии одежды ведущей слово в слово"
            lines = [q for q in tc.spoken(sv) if q]
            total = sum(len(q) for q in lines)
            max_sp = int((a.get("spoken") or {}).get("max_chars") or 80)
            if total > max_sp * sp_tol:
                hard[f"{tag} vid"] = f"реплики {total} символов при лимите {max_sp}"
            low = [tc.norm(q) for q in lines]
            if any(x and (x in y or y in x) for k, x in enumerate(low) for y in low[k + 1:]):
                hard[f"{tag} vid"] = "одна и та же реплика повторяется"
            if any(tc.copied_from(q, text) for q in lines):
                hard[f"{tag} vid"] = "реплика переписана из сценария дословно, нужен пересказ своими словами"
    return out, hard, soft


# ======================================================================= запуск
class Generator:
    def __init__(self, *, caller: Caller, template: dict[str, Any], brief: dict[str, Any], paragraphs: list[str],
                 mode: str, pairs: int, scenes_dir: Path, config: dict[str, Any], emit: Emit,
                 on_progress: Callable[[int, int, int], None]):
        self.caller, self.template, self.brief, self.paragraphs = caller, template, brief, paragraphs
        self.mode, self.config, self.emit, self.on_progress = mode, config, emit, on_progress
        self.scenes_dir = scenes_dir
        self.shots = shots_for(template, mode, pairs)
        self.system = build_system(template, brief, mode, self.shots, config)
        self._lock = threading.Lock()

    # --- состояние на диске
    def saved(self, n: int) -> dict[str, Any] | None:
        return read_json(self.scenes_dir / f"{n}.json")

    def _save(self, n: int, scenes: list[dict[str, Any]], hard: dict[str, str], soft: list[str], final: bool,
              attempt: int) -> None:
        write_json(self.scenes_dir / f"{n}.json", {
            "n": n, "shots": [s["title"] for s in self.shots], "scenes": scenes, "attempts": attempt,
            "problems": [f"{k}: {v}" for k, v in hard.items()], "flags": soft, "final": final})

    def _progress(self) -> None:
        done = flagged = 0
        for n in range(1, len(self.paragraphs) + 1):
            s = self.saved(n)
            if s and s.get("final"):
                done += 1
                flagged += 1 if s.get("problems") or s.get("flags") else 0
        self.on_progress(done, len(self.paragraphs), flagged)

    # --- основной цикл
    def run(self) -> None:
        total = len(self.paragraphs)
        pending = [n for n in range(1, total + 1) if not (self.saved(n) or {}).get("final")]
        if not pending:
            self._progress()
            return
        b = self.config.get("batch") or {}
        per_call = max(1, min(int(b.get("max_paragraphs_per_call", 8)),
                              int(b.get("scenes_per_call", 16)) // max(1, len(self.shots))))
        batches = [pending[i:i + per_call] for i in range(0, len(pending), per_call)]
        self.emit("info", f"Промпты: {len(pending)} абзацев × {len(self.shots)} сцен, {len(batches)} вызовов, "
                          f"по {self.caller.parallel('prompts')} одновременно · {self.caller.label('prompts')}")
        rounds = int((self.config.get("checks") or {}).get("fix_rounds", 2))
        feedback: dict[int, str] = {}
        todo = batches
        for attempt in range(rounds + 1):
            last = attempt == rounds
            failed = self._round(todo, feedback, attempt, last)
            if not failed:
                break
            feedback = failed
            todo = [[n] for n in sorted(failed)]  # исправление — по одному абзацу за вызов
            if not last:
                self.emit("info", f"Переписываю {len(todo)} абз. с замечаниями (круг {attempt + 1} из {rounds})")
        self._progress()

    def _round(self, batches: list[list[int]], feedback: dict[int, str], attempt: int, last: bool) -> dict[int, str]:
        failed: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=self.caller.parallel("prompts")) as pool:
            futures = {pool.submit(self._batch, nums, feedback, attempt, last): nums for nums in batches}
            try:
                for fut in as_completed(futures):
                    failed.update(fut.result())
            except BaseException:
                for f in futures:
                    f.cancel()
                raise
        return failed

    def _batch(self, nums: list[int], feedback: dict[int, str], attempt: int, last: bool) -> dict[int, str]:
        prompt = build_prompt(nums, self.paragraphs, self.brief, self.mode, len(self.shots),
                              {n: feedback[n] for n in nums if n in feedback})
        label = f"p{nums[0]}" + (f"-{nums[-1]}" if len(nums) > 1 else "") + (f"_fix{attempt}" if attempt else "")
        try:
            data = self.caller.json("prompts", self.system, prompt, label, _check_shape)
        except FormatFailed as e:
            if last:
                for n in nums:
                    self._save(n, [], {"ответ": str(e)[:200]}, [], True, attempt + 1)
                self._progress()
            return {n: "the answer was not valid JSON in the required shape" for n in nums}
        got = {}
        for p in data.get("paragraphs", []):
            try:
                got[int(p.get("n"))] = p.get("scenes") or []
            except (TypeError, ValueError, AttributeError):
                continue
        failed: dict[int, str] = {}
        for n in nums:
            scenes = [s for s in got.get(n, []) if isinstance(s, dict)]
            built, hard, soft = check_paragraph(n, self.paragraphs[n - 1], scenes, len(self.shots), self.template,
                                                self.brief, self.mode, self.config)
            if hard and not last:
                self._save(n, built, hard, soft, False, attempt + 1)
                failed[n] = tc.describe(hard) if scenes else "no scenes were returned for this paragraph"
            else:
                self._save(n, built, hard, soft, True, attempt + 1)
        self._progress()
        return failed


def _check_shape(d: Any) -> None:
    if not isinstance(d, dict) or not isinstance(d.get("paragraphs"), list):
        raise BadAnswer('нужен объект {"paragraphs": [{"n": ..., "scenes": [...]}]}')


__all__ = ["Generator", "StopRequested", "assemble", "build_prompt", "build_system", "check_paragraph", "shots_for"]
