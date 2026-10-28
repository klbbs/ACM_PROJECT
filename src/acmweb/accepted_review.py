"""Static improvement feedback for an immutable, officially accepted source."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Improvement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Literal['strategy', 'complexity', 'code_quality', 'robustness']
    finding: Text
    evidence: Text
    suggestion: Text
    confidence: Literal['high', 'medium', 'low', 'uncertain']


class AcceptedCodeReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: Text
    dimensions: list[Improvement] = Field(min_length=1, max_length=8)
    strengths: list[Text] = Field(max_length=6)
    alternative_approaches: list[Text] = Field(max_length=4)
    limitations: list[Text] = Field(min_length=1, max_length=6)
