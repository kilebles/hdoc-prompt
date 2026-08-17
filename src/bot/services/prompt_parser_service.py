import re
from typing import Protocol

from google import genai
from google.genai import types

from bot.config import GoogleAISettings
from bot.models.prompt import I2VPrompt

_SLUG_APOSTROPHES = re.compile(r"['’]")
_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")

_I2V_INSTRUCTION = (
    "Extract a structured video-generation prompt template from the following brief.\n"
    "This is an EXTRACTION task, not a creative writing task.\n"
    "Strict rules:\n"
    "- Use only information explicitly present in the source text below.\n"
    "- Do not invent, infer, guess, or add any fact, name, date, or detail that is not "
    "stated in the source text, even if it seems plausible or typical for the topic.\n"
    "- Prefer copying or lightly compressing the source wording over paraphrasing that "
    "could change meaning.\n"
    "- If a field's value is not explicitly present in the source text, leave it at its "
    "default (empty string / 0 / empty list) instead of filling it with a guess.\n"
    "\n"
    "Field-specific guidance:\n"
    "Leave title empty, it is assigned separately.\n"
    "Fill targets.image and targets.video with the short name of the image/video "
    "generation tool the brief is written for (e.g. 'Nano Banana', 'Google Imagen' "
    "for targets.image; 'Google Veo 3.1' for targets.video) — usually named once near "
    "the very start of the brief, such as 'for AI image and video generation tools, "
    "Google Imagen and Google Veo 3.1'. Extract ONLY the short tool name itself, never "
    "any surrounding sentence or rules text — if no tool name is stated, leave empty.\n"
    "Fill sub_periods from any distinct era/sub-period definitions found "
    "(leave id empty, it is assigned separately; title, when=date or time range).\n"
    "Fill pairs from the described shot structure: look for a section that lists "
    "an ordered sequence of named shot pairs, one entry per pair (often marked as "
    "'Pair 1', 'Shot 1', 'Pair 1, ESTABLISHING SHOT.' or similar numbering followed by a "
    "shot type name). Create exactly one pairs entry per such listed pair, in the "
    "same order they appear, however many there are (may be 6, 12, 16, or any other "
    "count) — do not skip any and do not merge multiple pairs into one entry. Leave "
    "number at 0 and id empty, both are assigned separately. For each: "
    "title=the shot type name as given, "
    "intent=one-line purpose/description of what the pair shows, frame=framing/distance "
    "if described, people=who or what appears if described, camera=camera behavior/lens "
    "if described, repeatable=true (this shot structure repeats per paragraph/unit).\n"
    "Fill lore with a concise summary of the narrative/historical context (what happens, "
    "who, when) — do NOT include color palette, lighting, texture, or camera instructions "
    "in lore, those belong in their own dedicated fields below.\n"
    "Fill color_palette with the content of any section describing color palette/tones "
    "per period or scene (often headed 'COLOR PALETTE'), compressed but not summarized "
    "away — keep the specific colors and which period/scene each applies to.\n"
    "Fill lighting with the content of any section describing lighting conditions per "
    "period or scene (often headed 'LIGHTING'), same treatment as color_palette.\n"
    "Fill textures with the content of any section describing materials/textures per "
    "period or scene (often headed 'TEXTURES AND MATERIALS'), same treatment.\n"
    "Fill image_rules and video_rules with the compact instructions for writing "
    "image vs video prompts respectively (camera/lens guidance, composition instructions, "
    "and the 'IMAGE PROMPT INSTRUCTIONS' / 'VIDEO PROMPT INSTRUCTIONS' sections belong "
    "here) — but EXCLUDE any trailing 'end with style tags ...' / 'style tags:' clause, "
    "that belongs in deterministic.image_suffix / video_suffix instead, see below.\n"
    "Fill deterministic.image_suffix and deterministic.video_suffix with the fixed "
    "trailing style-tag clause each prompt should end with — look for phrasing like "
    "'End with style tags, ...' or 'style tags:' near the end of the image/video prompt "
    "instructions, and copy everything after that phrase verbatim (e.g. 'cinematic "
    "historical realism, muted ... palette, practical lighting only, photorealistic 8K, "
    "deep shadow, extreme surface texture, film grain, documentary stillness, no text, "
    "no watermarks, no logos'). If image and video prompts share one identical style-tag "
    "clause, use the same text for both image_suffix and video_suffix.\n"
    "Fill deterministic.negatives by joining the mandatory negative constraints.\n"
    "Leave limits.image_max_chars and limits.video_max_chars at 0 unless the brief "
    "explicitly states a character limit (e.g. 'must not exceed 400 characters') — "
    "defaults are applied separately when none is stated.\n"
)

_DEFAULT_IMAGE_MAX_CHARS = 400
_DEFAULT_VIDEO_MAX_CHARS = 600


class PromptParserService(Protocol):
    """Turns free-form prompt-brief text into a structured template."""

    async def parse_i2v(self, raw_text: str) -> I2VPrompt: ...


class GeminiPromptParserService:
    """Structured-output parsing via the Gemini Developer API."""

    def __init__(self, settings: GoogleAISettings) -> None:
        self._client = genai.Client(api_key=settings.api_key.get_secret_value())
        self._model = settings.model

    async def parse_i2v(self, raw_text: str) -> I2VPrompt:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=f"{_I2V_INSTRUCTION}\n\n---\n\n{raw_text}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=I2VPrompt,
                temperature=0,
            ),
        )
        parsed = I2VPrompt.model_validate_json(response.text or "{}")
        parsed.title = _first_line(raw_text)

        # Number pairs by their extraction order rather than trusting the model
        # to read numbering out of the source text — the order is already
        # guaranteed correct by the "same order they appear" instruction.
        for i, pair in enumerate(parsed.pairs, start=1):
            pair.number = i

        # id is a mechanical slug, not creative content — deriving it in code
        # avoids inconsistent formatting (the model has drifted between
        # kebab-case and snake_case across otherwise identical briefs).
        for pair in parsed.pairs:
            pair.id = _slugify(pair.title or pair.id)
        for period in parsed.sub_periods:
            period.id = _slugify(period.title or period.id)

        # Apply fixed defaults when the brief didn't state an explicit limit,
        # rather than leaving 0 (which the model returns for "not specified").
        if parsed.limits.image_max_chars <= 0:
            parsed.limits.image_max_chars = _DEFAULT_IMAGE_MAX_CHARS
        if parsed.limits.video_max_chars <= 0:
            parsed.limits.video_max_chars = _DEFAULT_VIDEO_MAX_CHARS

        return parsed


def _first_line(raw_text: str) -> str:
    for line in raw_text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _slugify(text: str) -> str:
    without_apostrophes = _SLUG_APOSTROPHES.sub("", text.strip().lower())
    return _SLUG_INVALID_CHARS.sub("-", without_apostrophes).strip("-")
