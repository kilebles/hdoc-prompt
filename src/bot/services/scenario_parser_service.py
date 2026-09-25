import re
from io import BytesIO
from typing import Protocol

from docx import Document
from docx.text.paragraph import Paragraph

_BLANK_LINE = re.compile(r"\n\s*\n")

# Section-heading lines that scripts include verbatim as their own paragraph
# ("Introduction", "CHAPTER 2. A Kingdom at the Crossroads", "Часть 3",
# "Act II", "Оглавление"). These must not be sent to the model as scenario
# content, or it burns a full set of image/video pairs on a title.
#
# Optional leading list numbering ("1.", "3)", "IV.") is allowed before the
# keyword, and any trailing title text after it.
_NUMBER = r"(?:\d+|[ivxlcdm]+)"
_HEADING_KEYWORD = re.compile(
    r"^(?:\(?\s*" + _NUMBER + r"\s*[.)]\s*)?"
    r"(?:"
    r"(?:introduction|intro|conclusion|outro|prologue|epilogue|preface|foreword|"
    r"afterword|contents|table\s+of\s+contents|summary|outline|synopsis|"
    r"введение|вступление|заключение|пролог|эпилог|предисловие|послесловие|"
    r"оглавление|содержание|итоги?|вывод(?:ы)?)\b"
    r"|"
    r"(?:chapter|part|section|act|scene|episode|book|segment|block|"
    r"глава|часть|раздел|акт|сцена|эпизод|серия|блок|сегмент)"
    r"\s*(?:№|#)?\s*" + _NUMBER + r"\b"
    r")",
    re.IGNORECASE,
)

# Table-of-contents entries: "The Fall of Rome ........ 12", "Chapter 3\t7".
_TOC_ENTRY = re.compile(r"(?:\.{3,}|…+|\t+|\s{3,})\s*\d{1,4}\s*$")

# Narration always ends with sentence punctuation; a short line that does not
# ("The Fall of Rome", "Kingdom at the Crossroads") is a title.
_SENTENCE_END = re.compile(r"[.!?…:;]['\"”»’)\]]*$")
_SHORT_TITLE_MAX_WORDS = 8
_HEADING_MAX_WORDS = 16

_HEADING_STYLE_PREFIXES = ("heading", "title", "subtitle", "toc", "caption")


def _word_count(line: str) -> int:
    return len(line.split())


def _is_all_caps(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    return len(letters) >= 3 and all(ch.isupper() for ch in letters)


def _is_heading(line: str, *, standalone: bool = True) -> bool:
    """Text-only heading detection, shared by docx and txt input.

    `standalone` means the line is a paragraph of its own. Only then may a
    short line without sentence punctuation count as a title; inside a
    hard-wrapped paragraph such a line is just a wrapped fragment.
    """
    words = _word_count(line)
    if words > _HEADING_MAX_WORDS:
        return False
    if _HEADING_KEYWORD.match(line):
        return True
    if _TOC_ENTRY.search(line):
        return True
    if _is_all_caps(line):
        return True
    return standalone and words <= _SHORT_TITLE_MAX_WORDS and not _SENTENCE_END.search(line)


def _content_lines(text: str) -> list[str]:
    """Non-empty lines of one paragraph block with heading lines removed."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return [line for line in lines if not _is_heading(line, standalone=len(lines) == 1)]


def _is_heading_paragraph(para: Paragraph) -> bool:
    """docx-only signals: paragraph style and run formatting."""
    style_name = (para.style.name if para.style is not None else "").lower()
    if style_name.startswith(_HEADING_STYLE_PREFIXES):
        return True
    if _word_count(para.text) > _HEADING_MAX_WORDS:
        return False
    runs = [run for run in para.runs if run.text.strip()]
    return bool(runs) and all(run.bold for run in runs)


class ScenarioParserService(Protocol):
    """Splits a scenario document into an ordered list of paragraph texts."""

    def parse_docx(self, content: bytes) -> list[str]: ...

    def parse_txt(self, content: bytes) -> list[str]: ...


class ScenarioParserServiceImpl:
    def parse_docx(self, content: bytes) -> list[str]:
        document = Document(BytesIO(content))
        paragraphs = []
        for para in document.paragraphs:
            if not para.text.strip() or _is_heading_paragraph(para):
                continue
            paragraphs.extend(_content_lines(para.text))
        return paragraphs

    def parse_txt(self, content: bytes) -> list[str]:
        text = content.decode("utf-8")
        # Paragraphs are normally separated by blank lines; a file with none
        # at all is one-paragraph-per-line.
        blocks = _BLANK_LINE.split(text) if _BLANK_LINE.search(text) else text.split("\n")
        paragraphs = []
        for block in blocks:
            # A block may be a hard-wrapped paragraph, or a heading glued to
            # the paragraph that follows it; drop heading lines, keep the rest.
            kept = _content_lines(block)
            if kept:
                paragraphs.append(" ".join(kept))
        return paragraphs
