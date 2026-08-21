from pydantic import BaseModel


class GeneratedPair(BaseModel):
    """One generated image/video prompt pair for a paragraph, at a given variation."""

    paragraph_number: int
    pair_number: int
    img: str
    vid: str
    stock_query: str
    paragraph_text: str
