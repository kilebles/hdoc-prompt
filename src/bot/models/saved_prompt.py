from pydantic import BaseModel

from bot.models.prompt import I2VPrompt, PromptModelType


class SavedPrompt(BaseModel):
    """A parsed prompt persisted to storage, tagged by model type."""

    id: str
    model_type: PromptModelType
    data: I2VPrompt
