from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator


NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class Question(BaseModel):
    id: int = Field(ge=1)
    question: NonEmptyText
    options: list[NonEmptyText] = Field(min_length=4, max_length=4)
    correct_answer: int = Field(ge=0, le=3)
    explanation: NonEmptyText


class Quiz(BaseModel):
    title: NonEmptyText
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_question_ids(self) -> Self:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question IDs must be unique within a quiz.")
        return self
