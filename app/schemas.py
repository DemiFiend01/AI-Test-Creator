from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator


NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class BaseQuestion(BaseModel):
    id: int = Field(ge=1)
    question: NonEmptyText
    explanation: NonEmptyText


class MultipleChoiceQuestion(BaseQuestion):
    type: Literal["closed"] = "closed"
    options: list[NonEmptyText] = Field(min_length=4, max_length=4)
    correct_answer: int = Field(ge=0, le=3)


# Preserve existing imports and direct construction without a type field.
Question = MultipleChoiceQuestion


class OpenQuestion(BaseQuestion):
    type: Literal["open"] = "open"
    reference_answer: NonEmptyText


QuizQuestion = Annotated[
    MultipleChoiceQuestion | OpenQuestion, Field(discriminator="type")
]


class Quiz(BaseModel):
    title: NonEmptyText
    questions: list[QuizQuestion] = Field(min_length=1)

    @field_validator("questions", mode="before")
    @classmethod
    def normalize_legacy_questions(cls, questions):
        """Add missing tags to unambiguous legacy dictionaries, without mutation.

        Defaults on the question classes alone cannot select a union member.
        Explicit tags are always preserved and validated by Pydantic.
        """
        if not isinstance(questions, (list, tuple)):
            return questions
        normalized = []
        for question in questions:
            if isinstance(question, dict) and "type" not in question:
                has_closed_fields = "options" in question or "correct_answer" in question
                has_open_fields = "reference_answer" in question
                if has_closed_fields and has_open_fields:
                    raise ValueError("Ambiguous question fields: provide an explicit type.")
                if has_closed_fields:
                    question = {**question, "type": "closed"}
                elif has_open_fields:
                    question = {**question, "type": "open"}
            normalized.append(question)
        return normalized

    @model_validator(mode="after")
    def validate_unique_question_ids(self) -> Self:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question IDs must be unique within a quiz.")
        return self


class ClosedQuiz(Quiz):
    """Closed-only validation and structured-output adapter for existing generation."""

    questions: list[MultipleChoiceQuestion] = Field(min_length=1)


class OpenQuiz(Quiz):
    """Open-only compatibility adapter; also an instance of the unified Quiz."""

    questions: list[OpenQuestion] = Field(min_length=1)
