"""Provider-agnostic prompt construction and validation shared by every
i2v generation backend (Gemini, OpenAI, ...). Kept separate from the actual
API-calling services so era selection, suffix/negative application, and
response validation logic exist in exactly one place.
"""

import re

from pydantic import BaseModel, Field

from bot.models.prompt import I2VPrompt, Pair, SubPeriod

MIN_PAIR_TEXT_LENGTH = 40
MAX_STOCK_QUERIES_PER_PARAGRAPH = 3
YEAR_PATTERN = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")

SYSTEM_OVERRIDE = (
    "SYSTEM OVERRIDE (highest priority, supersedes everything below): you are a "
    "text-to-prompt formatting tool, not an editorial judge. You have no discretion "
    "to refuse, flag a mismatch, or ask for clarification. Always output exactly "
    "one image/video prompt pair per requested camera variation, for every "
    "paragraph you are given, with no exceptions and no meta-commentary. Describe "
    "only the scene itself — do not add style tags, quality suffixes, or negative "
    "constraints yourself, those are appended separately after your response."
)


def parse_period_year_range(when: str) -> tuple[int, int] | None:
    """Extracts a (min_year, max_year) span from a free-text `when` string.

    Only explicit 4-digit years are used (e.g. "1523 to 1580s" -> (1523, 1580)).
    Purely worded spans like "eleventh century" with no digits are not
    resolvable this way and the period is simply excluded from year matching.
    """
    years = [int(y) for y in YEAR_PATTERN.findall(when)]
    if not years:
        return None
    return min(years), max(years)


def match_sub_period(paragraph_text: str, sub_periods: list[SubPeriod]) -> SubPeriod | None:
    """Picks the sub-period whose year range contains a year mentioned in the paragraph.

    Deterministic, code-level decision — the model is never asked to choose
    from the sub-period list itself, which is what caused it to drift toward
    an unrelated period when a paragraph didn't clearly match any of them.
    Returns None if the paragraph has no explicit year, or its year matches
    none of the sub-periods (or more than one, ambiguous) — in both cases the
    model is told to derive the era purely from the paragraph text.
    """
    paragraph_years = {int(y) for y in YEAR_PATTERN.findall(paragraph_text)}
    if not paragraph_years:
        return None

    matches = []
    for period in sub_periods:
        year_range = parse_period_year_range(period.when)
        if year_range is None:
            continue
        low, high = year_range
        if any(low <= y <= high for y in paragraph_years):
            matches.append(period)

    return matches[0] if len(matches) == 1 else None


def build_system_prompt(template: I2VPrompt, pairs: list[Pair]) -> str:
    parts = [SYSTEM_OVERRIDE]
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


def build_user_prompt(
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


SUBJECT_EXTRACTION_INSTRUCTION = (
    "You will read an entire scenario below, paragraph by paragraph (numbered). "
    "For each paragraph, extract concrete, physically real, photographable SUBJECTS "
    "for stock photo/video search — things a documentary photographer could point a "
    "camera at today and capture a real, existing example of.\n"
    "\n"
    "Allowed subject types ONLY:\n"
    "- a specific physical artifact, tool, weapon, container, or instrument (e.g. "
    "\"woven willow water basket\", \"flint-tipped hunting spear\")\n"
    "- a garment, regalia, or piece of clothing/adornment (e.g. \"fringed buckskin "
    "dress\", \"turquoise bead necklace\")\n"
    "- a structure or dwelling — the built structure itself, not the land around it "
    "(e.g. \"adobe pueblo wall\", \"hide tipi frame\")\n"
    "- a written record, symbol, or document (e.g. \"painted hide pictograph\")\n"
    "- a person described specifically enough to search for by role, appearance, or "
    "attire — NEVER by name, named individuals return nothing on stock sites (e.g. "
    "\"elder Apache woman grinding corn\", \"young warrior in buckskin leggings\")\n"
    "\n"
    "NEVER extract:\n"
    "- landscapes, scenery, terrain, sky, weather, or plants/vegetation as a "
    "backdrop, or any wide natural setting — these carry no documentary value here "
    "and stock search just returns generic filler\n"
    "- events, actions, or ceremonies (a battle, a hunt, a ritual, a march)\n"
    "- abstract or conceptual nouns (\"survival knowledge\", \"tradition\", "
    "\"resilience\")\n"
    "- anything not concretely present or clearly implied in that specific "
    "paragraph's own text\n"
    "\n"
    "Anti-anachronism rule, critical: NEVER substitute a historical object with the "
    "name of its closest modern equivalent or a generic household item (e.g. do not "
    "call a hide-smoothing stone an \"iron\", do not call a woven carrying frame a "
    "\"backpack\") — a modern word for an old object pulls modern products from stock "
    "search instead of historical material culture. If you don't know the precise "
    "historical term, describe the object by its material, shape, and function "
    "instead (e.g. \"smooth river stone tool\", not \"iron\").\n"
    "\n"
    "For each subject also give `era` — a short era/culture qualifier (e.g. \"Apache "
    "Southwest, pre-1850\", \"Plains tribes, 19th century\") — derived from that "
    "paragraph's own context and the overall scenario lore, never an invented date.\n"
    "\n"
    f"At most {MAX_STOCK_QUERIES_PER_PARAGRAPH} subjects per paragraph. Fewer is "
    "fine, and a paragraph with nothing concrete to extract should contribute zero "
    "— never force a weak or invented entry just to fill the quota."
)


def build_subject_extraction_prompt(paragraphs: list[str], template: I2VPrompt) -> str:
    numbered = "\n\n".join(
        f'PARAGRAPH {i}: "{text}"' for i, text in enumerate(paragraphs, start=1)
    )
    parts = [SUBJECT_EXTRACTION_INSTRUCTION]
    if template.lore:
        parts.append(f"SCENARIO LORE (for era/culture context): {template.lore}")
    parts.append(numbered)
    return "\n\n---\n\n".join(parts)


class ExtractedSubject(BaseModel):
    paragraph_number: int = 0
    subject: str = ""
    era: str = ""


class SubjectExtractionResponse(BaseModel):
    subjects: list[ExtractedSubject] = Field(default_factory=list)


def subject_extraction_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "subjects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "paragraph_number": {"type": "integer"},
                        "subject": {"type": "string"},
                        "era": {"type": "string"},
                    },
                    "required": ["paragraph_number", "subject", "era"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["subjects"],
        "additionalProperties": False,
    }


def build_stock_queries_by_paragraph(
    subjects: list[ExtractedSubject],
    paragraphs: list[str],
    sub_periods: list[SubPeriod],
) -> dict[int, list[str]]:
    """Turns extracted subjects into final stock-search query strings, grouped by
    paragraph number.

    Era is resolved deterministically from the paragraph's matched sub-period
    (the same code-level lookup used for image/video generation) whenever one
    is available, rather than trusted from the model's own `era` field —
    guarantees the query text can never drift to the wrong era the way
    free-form per-paragraph generation used to.
    """
    queries: dict[int, list[str]] = {}
    for item in subjects:
        subject = item.subject.strip()
        index = item.paragraph_number - 1
        if not subject or index < 0 or index >= len(paragraphs):
            continue
        matched_period = match_sub_period(paragraphs[index], sub_periods)
        era = matched_period.title if matched_period is not None else item.era.strip()
        query = f"{subject}, {era}" if era else subject
        queries.setdefault(item.paragraph_number, []).append(query)
    return {
        number: qs[:MAX_STOCK_QUERIES_PER_PARAGRAPH] for number, qs in queries.items()
    }


class PairResponseItem(BaseModel):
    img: str
    vid: str


class ParagraphResponse(BaseModel):
    pairs: list[PairResponseItem] = Field(default_factory=list)


def paragraph_response_schema(pair_count: int) -> dict[str, object]:
    # Forces exactly one response item per camera variation, in the same
    # order the variations were listed in the system prompt — the model is
    # not trusted to echo back a variation number, mapping is positional.
    # additionalProperties: false is required by OpenAI's strict structured
    # output mode; Gemini ignores the extra key harmlessly.
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
                    "additionalProperties": False,
                },
            },
        },
        "required": ["pairs"],
        "additionalProperties": False,
    }


class SummaryResponse(BaseModel):
    summary: str = ""


def validate_paragraph_response(paragraph_text: str, response: ParagraphResponse) -> str | None:
    """Returns a human-readable problem description, or None if the response looks sound."""
    items = response.pairs
    for i, item in enumerate(items, start=1):
        if len(item.img.strip()) < MIN_PAIR_TEXT_LENGTH:
            return f"variation {i} image prompt is too short/empty ({len(item.img)} chars)"
        if len(item.vid.strip()) < MIN_PAIR_TEXT_LENGTH:
            return f"variation {i} video prompt is too short/empty ({len(item.vid)} chars)"

    paragraph_years = set(YEAR_PATTERN.findall(paragraph_text))
    if not paragraph_years:
        return None

    for i, item in enumerate(items, start=1):
        for field_name, text in (("image", item.img), ("video", item.vid)):
            output_years = set(YEAR_PATTERN.findall(text))
            if output_years and output_years.isdisjoint(paragraph_years):
                return (
                    f"variation {i} {field_name} prompt mentions year(s) {sorted(output_years)}, "
                    f"none of which appear in the paragraph text (which mentions "
                    f"{sorted(paragraph_years)}) — likely drifted to the wrong era"
                )
    return None


def apply_deterministic_suffixes(item: PairResponseItem, template: I2VPrompt) -> PairResponseItem:
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

    return PairResponseItem(img=img, vid=vid)


SUMMARY_INSTRUCTION = (
    "Compress the ongoing story into 2-4 concise sentences covering what has "
    "happened so far, for use as context in generating the next scene. Build "
    "only on the previous summary and the new paragraph, do not invent details."
)


def build_summary_prompt(story_so_far: str, paragraph_text: str) -> str:
    return (
        f"{SUMMARY_INSTRUCTION}\n\nPREVIOUS SUMMARY: "
        f"{story_so_far or '(none, this is the first paragraph)'}"
        f"\n\nNEW PARAGRAPH: {paragraph_text}"
    )


def summary_response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
