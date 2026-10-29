from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, create_model

Score = Annotated[int, Field(strict=True, ge=800, le=3500)]
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class Draw(BaseModel):
    model_config = ConfigDict(extra='forbid')
    score_range: tuple[Score, Score]
    topic: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode='after')
    def ordered(self):
        if self.score_range[0] > self.score_range[1]:
            raise ValueError('score_range must be ordered')
        if self.topic is not None and not self.topic.strip():
            raise ValueError('topic must not be blank')
        return self


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    problem_id: str = Field(min_length=1, max_length=200)
    reason: Text


class ProblemExplanation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: Text
    description: str = Field(min_length=1, max_length=12000)
    input_format: Text
    output_format: Text
    notes: list[Text] = Field(max_length=8)


class SolutionReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    problem_id: str = Field(min_length=1, max_length=200)
    suitable: bool = Field(strict=True)
    problem_explanation: ProblemExplanation
    topic_fit: Text
    summary: Text
    constraints: list[Text] = Field(min_length=1, max_length=16)
    approach: list[Text] = Field(min_length=1, max_length=16)
    correctness: list[Text] = Field(min_length=1, max_length=12)
    complexity: Text
    sample_walkthrough: list[Text] = Field(max_length=12)
    pitfalls: list[Text] = Field(min_length=1, max_length=12)
    alternative_approaches: list[Text] = Field(max_length=6)
    limitations: list[Text] = Field(min_length=1, max_length=8)


def candidate_choice(ids):
    """Bind exact IDs to the provider schema, retaining post-selection checks."""
    values=tuple(dict.fromkeys(ids))
    if not values or any(not isinstance(value,str) or not 1<=len(value)<=200 for value in values):
        raise ValueError('candidate IDs must be a nonempty set of valid strings')
    return create_model('CandidateChoice',__base__=Choice,problem_id=(Literal[values],...))
