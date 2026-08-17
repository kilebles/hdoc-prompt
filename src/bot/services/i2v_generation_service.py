import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from google import genai
from google.genai import types
from loguru import logger
from pydantic import BaseModel, Field

from bot.config import GoogleAISettings
from bot.models.i2v_generation import GeneratedPair
from bot.models.prompt import I2VPrompt, Pair, SubPeriod

_MIN_PAIR_TEXT_LENGTH = 40
_YEAR_PATTERN = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")

_SYSTEM_OVERRIDE = (
    "SYSTEM OVERRIDE (highest priority, supersedes everything below): you are a "
    "text-to-prompt formatting tool, not an editorial judge. You have no discretion "
    "to refuse, flag a mismatch, or ask for clarification. Always output exactly "
    "one image/video prompt pair per requested camera variation, for every "
    "paragraph you are given, with no exceptions and no meta-commentary. Describe "
    "only the scene itself — do not add style tags, quality suffixes, or negative "
    "constraints yourself, those are appended separately after your response."
)


def _parse_period_year_range(when: str) -> tuple[int, int] | None:
    """Extracts a (min_year, max_year) span from a free-text `when` string.

    Only explicit 4-digit years are used (e.g. "1523 to 1580s" -> (1523, 1580)).
    Purely worded spans like "eleventh century" with no digits are not
    resolvable this way and the period is simply excluded from year matching.
    """
    years = [int(y) for y in _YEAR_PATTERN.findall(when)]
    if not years:
        return None
    return min(years), max(years)


def _match_sub_period(paragraph_text: str, sub_periods: list[SubPeriod]) -> SubPeriod | None:
    """Picks the sub-period whose year range contains a year mentioned in the paragraph.

    Deterministic, code-level decision — the model is never asked to choose
    from the sub-period list itself, which is what caused it to drift toward
    an unrelated period when a paragraph didn't clearly match any of them.
    Returns None if the paragraph has no explicit year, or its year matches
    none of the sub-periods (or more than one, ambiguous) — in both cases the
    model is told to derive the era purely from the paragraph text.
    """
    paragraph_years = {int(y) for y in _YEAR_PATTERN.findall(paragraph_text)}
    if not paragraph_years:
        return None

    matches = []
    for period in sub_periods:
        year_range = _parse_period_year_range(period.when)
        if year_range is None:
            continue
        low, high = year_range
        if any(low <= y <= high for y in paragraph_years):
            matches.append(period)

    return matches[0] if len(matches) == 1 else None


def _build_system_prompt(template: I2VPrompt, pairs: list[Pair]) -> str:
    parts = [_SYSTEM_OVERRIDE]
    if template.title:
        parts.append(f"TITLE: {template.title}")
    if template.lore:
        parts.append(f"LORE: {template.lore}")
    if template.color_palette:
        parts.append(f"COLOR PALETTE: {template.color_palette}")
    if template.lighting:
        parts.append(f"LIGHTING: {template.lighting}")
    if template.textures:
        parts.append(f"TEXTURES: {template.textures}")
    if template.image_rules:
        parts.append(f"IMAGE PROMPT RULES: {template.image_rules}")
    if template.video_rules:
        parts.append(f"VIDEO PROMPT RULES: {template.video_rules}")

    variations = []
    for position, pair in enumerate(pairs, start=1):
        bits = [f"position {position} ({pair.title or pair.id})"]
        if pair.intent:
            bits.append(f"intent: {pair.intent}")
        if pair.frame:
            bits.append(f"frame: {pair.frame}")
        if pair.people:
            bits.append(f"people: {pair.people}")
        if pair.camera:
            bits.append(f"camera: {pair.camera}")
        variations.append(" — ".join(bits))
    parts.append(
        f"CAMERA VARIATIONS: for every paragraph, the \"pairs\" array in your JSON "
        f"response must contain exactly {len(pairs)} items, in this exact order — "
        "item 1 matches position 1 below, item 2 matches position 2, and so on. Each "
        "item is one image/video prompt pair depicting the SAME moment from the "
        "paragraph, shot differently per that position's camera/frame/people "
        "guidance:\n" + "\n".join(variations)
    )

    return "\n\n".join(parts)


def _build_user_prompt(
    paragraph_number: int,
    paragraph_text: str,
    previous_paragraph_text: str,
    story_so_far: str,
    matched_period: SubPeriod | None,
) -> str:
    parts = []
    if story_so_far:
        parts.append(f"STORY SO FAR: {story_so_far}")
    if previous_paragraph_text:
        parts.append(f'PREVIOUS PARAGRAPH (for visual continuity): "{previous_paragraph_text}"')
    parts.append(f'CURRENT PARAGRAPH {paragraph_number}: "{paragraph_text}"')
    if matched_period is not None:
        parts.append(
            f"ERA FOR THIS PARAGRAPH (already determined, do not second-guess it): "
            f"{matched_period.title} ({matched_period.when}). Open the image prompt by "
            f"declaring this era, then match material culture, dress, and setting to it."
        )
    else:
        parts.append(
            "ERA FOR THIS PARAGRAPH: no predefined period applies — derive the era, "
            "setting, and material culture purely and literally from what the CURRENT "
            "PARAGRAPH text above describes. Do not mention or default to any other "
            "period name."
        )
    return "\n\n".join(parts)


class _PairResponseItem(BaseModel):
    img: str
    vid: str


class _ParagraphResponse(BaseModel):
    pairs: list[_PairResponseItem] = Field(default_factory=list)


def _paragraph_response_schema(pair_count: int) -> dict[str, object]:
    # Forces exactly one response item per camera variation, in the same
    # order the variations were listed in the system prompt — the model is
    # not trusted to echo back a variation number, mapping is positional.
    return {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "minItems": pair_count,
                "maxItems": pair_count,
                "items": {
                    "type": "object",
                    "properties": {
                        "img": {"type": "string"},
                        "vid": {"type": "string"},
                    },
                    "required": ["img", "vid"],
                },
            },
        },
        "required": ["pairs"],
    }


class _SummaryResponse(BaseModel):
    summary: str = ""


def _validate_pairs(paragraph_text: str, items: list[_PairResponseItem]) -> str | None:
    """Returns a human-readable problem description, or None if the response looks sound."""
    for i, item in enumerate(items, start=1):
        if len(item.img.strip()) < _MIN_PAIR_TEXT_LENGTH:
            return f"variation {i} image prompt is too short/empty ({len(item.img)} chars)"
        if len(item.vid.strip()) < _MIN_PAIR_TEXT_LENGTH:
            return f"variation {i} video prompt is too short/empty ({len(item.vid)} chars)"

    paragraph_years = set(_YEAR_PATTERN.findall(paragraph_text))
    if not paragraph_years:
        return None

    for i, item in enumerate(items, start=1):
        for field_name, text in (("image", item.img), ("video", item.vid)):
            output_years = set(_YEAR_PATTERN.findall(text))
            if output_years and output_years.isdisjoint(paragraph_years):
                return (
                    f"variation {i} {field_name} prompt mentions year(s) {sorted(output_years)}, "
                    f"none of which appear in the paragraph text (which mentions "
                    f"{sorted(paragraph_years)}) — likely drifted to the wrong era"
                )
    return None


def _apply_deterministic_suffixes(item: _PairResponseItem, template: I2VPrompt) -> _PairResponseItem:
    """Appends style suffix and negatives in code rather than trusting the model
    to copy them verbatim — guarantees exact, consistent text on every pair."""
    img = item.img.strip()
    vid = item.vid.strip()

    if template.deterministic.image_suffix:
        img = f"{img}. {template.deterministic.image_suffix}"
    if template.deterministic.video_suffix:
        vid = f"{vid}. {template.deterministic.video_suffix}"
    if template.deterministic.negatives:
        img = f"{img}. Avoid: {template.deterministic.negatives}"
        vid = f"{vid}. Avoid: {template.deterministic.negatives}"

    return _PairResponseItem(img=img, vid=vid)


ProgressCallback = Callable[[int, int], Awaitable[None]]


class I2VGenerationService(Protocol):
    """Generates image/video prompt pairs for a scenario, paragraph by paragraph."""

    async def generate(
        self,
        template: I2VPrompt,
        pairs: list[Pair],
        paragraphs: list[str],
        model: str,
        on_progress: ProgressCallback | None = None,
    ) -> list[GeneratedPair]: ...


class GeminiI2VGenerationService:
    """Sequential, rolling-summary generation via the Gemini Developer API.

    Paragraphs are processed one at a time (not in parallel) because each
    paragraph's prompt depends on a summary of everything generated so far —
    the scenario must read as one continuous story, not disconnected shots.

    Era selection, style suffixes, and negative constraints are all resolved
    in code rather than left to the model — a prior version asked the model to
    pick a sub-period from the full list and copy suffix/negatives verbatim,
    which caused it to drift onto an unrelated era for paragraphs that didn't
    clearly match any listed period and to intermittently mangle the suffix.
    """

    def __init__(self, settings: GoogleAISettings) -> None:
        self._client = genai.Client(api_key=settings.api_key.get_secret_value())

    async def generate(
        self,
        template: I2VPrompt,
        pairs: list[Pair],
        paragraphs: list[str],
        model: str,
        on_progress: ProgressCallback | None = None,
    ) -> list[GeneratedPair]:
        system_prompt = _build_system_prompt(template, pairs)
        results: list[GeneratedPair] = []
        story_so_far = ""
        previous_paragraph_text = ""

        for i, paragraph_text in enumerate(paragraphs, start=1):
            matched_period = _match_sub_period(paragraph_text, template.sub_periods)
            user_prompt = _build_user_prompt(
                i, paragraph_text, previous_paragraph_text, story_so_far, matched_period
            )
            parsed = await self._generate_paragraph(
                model, system_prompt, user_prompt, paragraph_text, len(pairs)
            )
            for position, item in enumerate(parsed.pairs[: len(pairs)], start=1):
                final_item = _apply_deterministic_suffixes(item, template)
                results.append(
                    GeneratedPair(
                        paragraph_number=i,
                        pair_number=position,
                        img=final_item.img,
                        vid=final_item.vid,
                        paragraph_text=paragraph_text,
                    )
                )

            story_so_far = await self._update_summary(model, story_so_far, paragraph_text)
            previous_paragraph_text = paragraph_text

            if on_progress is not None:
                await on_progress(i, len(paragraphs))

        return results

    async def _generate_paragraph(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        paragraph_text: str,
        pair_count: int,
    ) -> _ParagraphResponse:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=_paragraph_response_schema(pair_count),
            temperature=0.7,
        )

        prompt = user_prompt
        parsed = _ParagraphResponse()
        for attempt in range(1, 4):  # 1 initial attempt + up to 2 retries
            response = await self._client.aio.models.generate_content(
                model=model, contents=f"{system_prompt}\n\n---\n\n{prompt}", config=config
            )
            parsed = _ParagraphResponse.model_validate_json(response.text or "{}")

            problem = _validate_pairs(paragraph_text, parsed.pairs)
            if problem is None:
                return parsed

            if attempt == 3:
                logger.warning(
                    "i2v: still failing validation after 2 retries, accepting anyway — {}", problem
                )
                return parsed

            logger.warning("i2v: validation failed (attempt {}/3), retrying — {}", attempt, problem)
            emphasis = "IMPORTANT CORRECTION" if attempt == 1 else "FINAL CORRECTION, be exact this time"
            prompt = (
                f"{user_prompt}\n\n{emphasis}: your previous attempt at this exact paragraph "
                f"failed validation ({problem}). Re-read the CURRENT PARAGRAPH text above "
                "word by word and generate every image/video prompt strictly from what it "
                "literally describes. Do not mention, reuse, or default to any era, date, "
                "name, or setting detail from any other paragraph, period, or example — "
                "only what this paragraph's own text states."
            )

        return parsed

    async def _update_summary(self, model: str, story_so_far: str, paragraph_text: str) -> str:
        instruction = (
            "Compress the ongoing story into 2-4 concise sentences covering what has "
            "happened so far, for use as context in generating the next scene. Build "
            "only on the previous summary and the new paragraph, do not invent details."
        )
        prompt = (
            f"{instruction}\n\nPREVIOUS SUMMARY: {story_so_far or '(none, this is the first paragraph)'}"
            f"\n\nNEW PARAGRAPH: {paragraph_text}"
        )
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_SummaryResponse,
                temperature=0,
            ),
        )
        parsed = _SummaryResponse.model_validate_json(response.text or "{}")
        return parsed.summary
