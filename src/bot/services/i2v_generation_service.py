from collections.abc import Awaitable, Callable
from typing import Any, Protocol, cast

from google import genai
from google.genai import types as genai_types
from loguru import logger
from openai import AsyncOpenAI, Omit, omit
from openai.types.chat import ChatCompletionMessageParam

from bot.config import GoogleAISettings, OpenAISettings
from bot.models.i2v_generation import GeneratedPair
from bot.models.prompt import I2VPrompt, Pair
from bot.services.i2v_prompt_builder import (
    ParagraphResponse,
    SummaryResponse,
    apply_deterministic_suffixes,
    build_summary_prompt,
    build_system_prompt,
    build_user_prompt,
    match_sub_period,
    paragraph_response_schema,
    summary_response_schema,
    validate_pairs,
)

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


async def _run_generation(
    *,
    template: I2VPrompt,
    pairs: list[Pair],
    paragraphs: list[str],
    on_progress: ProgressCallback | None,
    generate_paragraph: Callable[[str, str, str, int], Awaitable[ParagraphResponse]],
    update_summary: Callable[[str, str], Awaitable[str]],
) -> list[GeneratedPair]:
    """Shared sequential, rolling-summary generation loop.

    Paragraphs are processed one at a time (not in parallel) because each
    paragraph's prompt depends on a summary of everything generated so far —
    the scenario must read as one continuous story, not disconnected shots.
    Era selection, style suffixes, and negative constraints are all resolved
    in code (see i2v_prompt_builder) rather than left to the model.
    """
    system_prompt = build_system_prompt(template, pairs)
    results: list[GeneratedPair] = []
    story_so_far = ""
    previous_paragraph_text = ""

    for i, paragraph_text in enumerate(paragraphs, start=1):
        matched_period = match_sub_period(paragraph_text, template.sub_periods)
        user_prompt = build_user_prompt(
            i, paragraph_text, previous_paragraph_text, story_so_far, matched_period
        )
        parsed = await generate_paragraph(system_prompt, user_prompt, paragraph_text, len(pairs))
        for position, item in enumerate(parsed.pairs[: len(pairs)], start=1):
            final_item = apply_deterministic_suffixes(item, template)
            results.append(
                GeneratedPair(
                    paragraph_number=i,
                    pair_number=position,
                    img=final_item.img,
                    vid=final_item.vid,
                    paragraph_text=paragraph_text,
                )
            )

        story_so_far = await update_summary(story_so_far, paragraph_text)
        previous_paragraph_text = paragraph_text

        if on_progress is not None:
            await on_progress(i, len(paragraphs))

    return results


class GeminiI2VGenerationService:
    """Sequential i2v generation via the Gemini Developer API."""

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
        async def generate_paragraph(
            system_prompt: str, user_prompt: str, paragraph_text: str, pair_count: int
        ) -> ParagraphResponse:
            return await self._generate_paragraph(
                model, system_prompt, user_prompt, paragraph_text, pair_count
            )

        async def update_summary(story_so_far: str, paragraph_text: str) -> str:
            return await self._update_summary(model, story_so_far, paragraph_text)

        return await _run_generation(
            template=template,
            pairs=pairs,
            paragraphs=paragraphs,
            on_progress=on_progress,
            generate_paragraph=generate_paragraph,
            update_summary=update_summary,
        )

    async def _generate_paragraph(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        paragraph_text: str,
        pair_count: int,
    ) -> ParagraphResponse:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=paragraph_response_schema(pair_count),
            temperature=0.7,
        )

        prompt = user_prompt
        parsed = ParagraphResponse()
        for attempt in range(1, 4):  # 1 initial attempt + up to 2 retries
            response = await self._client.aio.models.generate_content(
                model=model, contents=f"{system_prompt}\n\n---\n\n{prompt}", config=config
            )
            parsed = ParagraphResponse.model_validate_json(response.text or "{}")

            problem = validate_pairs(paragraph_text, parsed.pairs)
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
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=build_summary_prompt(story_so_far, paragraph_text),
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SummaryResponse,
                temperature=0,
            ),
        )
        parsed = SummaryResponse.model_validate_json(response.text or "{}")
        return parsed.summary


def _temperature_for(model: str, value: float) -> float | Omit:
    # gpt-5.x models only accept the default temperature (1) and reject any
    # explicit value, including 1 itself — so the param is simply omitted.
    if model.startswith("gpt-5"):
        return omit
    return value


class OpenAII2VGenerationService:
    """Sequential i2v generation via the OpenAI API (chat completions, structured output)."""

    def __init__(self, settings: OpenAISettings) -> None:
        self._client = AsyncOpenAI(api_key=settings.api_key.get_secret_value())

    async def generate(
        self,
        template: I2VPrompt,
        pairs: list[Pair],
        paragraphs: list[str],
        model: str,
        on_progress: ProgressCallback | None = None,
    ) -> list[GeneratedPair]:
        async def generate_paragraph(
            system_prompt: str, user_prompt: str, paragraph_text: str, pair_count: int
        ) -> ParagraphResponse:
            return await self._generate_paragraph(
                model, system_prompt, user_prompt, paragraph_text, pair_count
            )

        async def update_summary(story_so_far: str, paragraph_text: str) -> str:
            return await self._update_summary(model, story_so_far, paragraph_text)

        return await _run_generation(
            template=template,
            pairs=pairs,
            paragraphs=paragraphs,
            on_progress=on_progress,
            generate_paragraph=generate_paragraph,
            update_summary=update_summary,
        )

    async def _generate_paragraph(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        paragraph_text: str,
        pair_count: int,
    ) -> ParagraphResponse:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "paragraph_response",
                "schema": paragraph_response_schema(pair_count),
                "strict": True,
            },
        }

        prompt = user_prompt
        parsed = ParagraphResponse()
        for attempt in range(1, 4):  # 1 initial attempt + up to 2 retries
            messages = cast(
                "list[ChatCompletionMessageParam]",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                response_format=cast("Any", response_format),
                temperature=_temperature_for(model, 0.7),
            )
            content = response.choices[0].message.content or "{}"
            parsed = ParagraphResponse.model_validate_json(content)

            problem = validate_pairs(paragraph_text, parsed.pairs)
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
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "summary_response",
                "schema": summary_response_schema(),
                "strict": True,
            },
        }
        messages = cast(
            "list[ChatCompletionMessageParam]",
            [{"role": "user", "content": build_summary_prompt(story_so_far, paragraph_text)}],
        )
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            response_format=cast("Any", response_format),
            temperature=_temperature_for(model, 0),
        )
        content = response.choices[0].message.content or "{}"
        parsed = SummaryResponse.model_validate_json(content)
        return parsed.summary


class RoutingI2VGenerationService:
    """Dispatches to the Gemini or OpenAI backend based on the chosen model id.

    Keeps the handler/DI layer oblivious to which provider actually serves a
    given model — the keyboard just offers model ids from both providers.
    """

    def __init__(self, gemini: I2VGenerationService, openai: I2VGenerationService) -> None:
        self._gemini = gemini
        self._openai = openai

    async def generate(
        self,
        template: I2VPrompt,
        pairs: list[Pair],
        paragraphs: list[str],
        model: str,
        on_progress: ProgressCallback | None = None,
    ) -> list[GeneratedPair]:
        backend = self._openai if model.startswith("gpt-") else self._gemini
        return await backend.generate(template, pairs, paragraphs, model, on_progress)
