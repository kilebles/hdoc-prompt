import re
from io import BytesIO
from typing import Protocol

from docx import Document

_BLANK_LINE = re.compile(r"\n\s*\n")

# Standalone section-heading lines (not narration) that scripts sometimes
# include verbatim as their own paragraph, e.g. "Introduction" or
# "Chapter 3. The Breaking Point" — these must not be sent to the model as
# scenario content, or it burns a full set of image/video pairs on a title.
_HEADING_LINE = re.compile(
    r"^(introduction|conclusion|prologue|epilogue|"
    r"(chapter|part|section)\s+\d+\b.*)$",
    re.IGNORECASE,
)


def _is_heading(line: str) -> bool:
    return bool(_HEADING_LINE.match(line))


class ScenarioParserService(Protocol):
    """Splits a scenario document into an ordered list of paragraph texts."""

    def parse_docx(self, content: bytes) -> list[str]: ...

    def parse_txt(self, content: bytes) -> list[str]: ...


class ScenarioParserServiceImpl:
    def parse_docx(self, content: bytes) -> list[str]:
        document = Document(BytesIO(content))
        paragraphs = []
        for para in document.paragraphs:
            for line in para.text.split("\n"):
                line = line.strip()
                if line and not _is_heading(line):
                    paragraphs.append(line)
        return paragraphs

    def parse_txt(self, content: bytes) -> list[str]:
        text = content.decode("utf-8")
        return [
            p.strip() for p in _BLANK_LINE.split(text) if p.strip() and not _is_heading(p.strip())
        ]
