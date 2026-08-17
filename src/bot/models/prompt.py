from enum import StrEnum

from pydantic import BaseModel, Field


class PromptModelType(StrEnum):
    """Target generation model a saved prompt is written for."""

    I2V = "i2v"
    F2V = "f2v"
    T2V = "t2v"


class PromptTargets(BaseModel):
    image: str = ""
    video: str = ""


class SubPeriod(BaseModel):
    id: str = ""
    title: str = ""
    when: str = ""


class Pair(BaseModel):
    """One named shot pair (image + video) from the brief's shot structure."""

    number: int = 0
    id: str = ""
    title: str = ""
    intent: str = ""
    frame: str = ""
    people: str = ""
    camera: str = ""
    repeatable: bool = False


class Deterministic(BaseModel):
    image_suffix: str = ""
    video_suffix: str = ""
    negatives: str = ""


class PairOverride(BaseModel):
    pair_id: str = ""
    value: str = ""


class Limits(BaseModel):
    image_max_chars: int = 400
    video_max_chars: int = 600
    pair_overrides: list[PairOverride] = Field(default_factory=list)


class I2VPrompt(BaseModel):
    """Structured prompt template for the image-to-video (i2v) model."""

    title: str = ""
    targets: PromptTargets = PromptTargets()
    sub_periods: list[SubPeriod] = Field(default_factory=list)
    lore: str = ""
    pairs: list[Pair] = Field(default_factory=list)
    color_palette: str = ""
    lighting: str = ""
    textures: str = ""
    image_rules: str = ""
    video_rules: str = ""
    deterministic: Deterministic = Deterministic()
    limits: Limits = Limits()
