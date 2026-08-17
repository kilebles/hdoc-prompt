import re
from io import BytesIO
from typing import Protocol

from docx import Document

_BLANK_LINE = re.compile(r"\n\s*\n")


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
                if line:
                    paragraphs.append(line)
        return paragraphs

    def parse_txt(self, content: bytes) -> list[str]:
        text = content.decode("utf-8")
        return [p.strip() for p in _BLANK_LINE.split(text) if p.strip()]
