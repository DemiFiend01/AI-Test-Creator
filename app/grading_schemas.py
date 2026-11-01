from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import NonEmptyText


class OpenAnswerGrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: int = Field(ge=1, strict=True)
    points: Literal[0.0, 0.5, 1.0]
    feedback: NonEmptyText

    @field_validator("points", mode="before")
    @classmethod
    def require_numeric_points(cls, value):
        # In Python, True == 1 and False == 0; neither is a numeric grade.
        if type(value) not in (int, float):
            raise ValueError("Points must be a number: 0, 0.5 or 1.")
        return value


class OpenGradingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grades: list[OpenAnswerGrade]

    @model_validator(mode="after")
    def validate_unique_question_ids(self) -> Self:
        ids = [grade.question_id for grade in self.grades]
        if len(ids) != len(set(ids)):
            raise ValueError("Question IDs must be unique in grading results.")
        return self
