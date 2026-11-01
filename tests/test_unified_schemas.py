from copy import deepcopy
import json
import unittest

from pydantic import TypeAdapter, ValidationError

from app.schemas import (
    BaseQuestion,
    ClosedQuiz,
    MultipleChoiceQuestion,
    OpenQuestion,
    OpenQuiz,
    Question,
    Quiz,
    QuizQuestion,
)


def closed_data(**overrides):
    return {
        "id": 1,
        "question": "Ile wynosi 2 + 2?",
        "explanation": "Suma wynosi 4.",
        "options": ["3", "4", "5", "6"],
        "correct_answer": 1,
        **overrides,
    }


def open_data(**overrides):
    return {
        "id": 2,
        "question": "Wyjaśnij dodawanie.",
        "explanation": "Dodawanie łączy wartości.",
        "reference_answer": "Dodawanie wyznacza sumę liczb.",
        **overrides,
    }


class UnifiedSchemaTests(unittest.TestCase):
    def test_base_fields_and_inheritance(self):
        question = BaseQuestion(id=1, question=" Q ", explanation=" E ")
        self.assertEqual(question.model_dump(), {"id": 1, "question": "Q", "explanation": "E"})
        self.assertTrue(issubclass(MultipleChoiceQuestion, BaseQuestion))
        self.assertTrue(issubclass(OpenQuestion, BaseQuestion))
        for data in (
            {"id": 0, "question": "Q", "explanation": "E"},
            {"id": 1, "question": " ", "explanation": "E"},
            {"id": 1, "question": "Q", "explanation": " "},
        ):
            with self.subTest(data=data):
                with self.assertRaises(ValidationError):
                    BaseQuestion.model_validate(data)

    def test_question_alias_preserves_legacy_construction(self):
        self.assertIs(Question, MultipleChoiceQuestion)
        question = Question(**closed_data())
        self.assertEqual(question.type, "closed")
        self.assertEqual(question.model_dump(), {**closed_data(), "type": "closed"})

    def test_discriminator_selects_correct_question_class(self):
        adapter = TypeAdapter(QuizQuestion)
        closed = adapter.validate_python(closed_data(type="closed"))
        opened = adapter.validate_python(open_data(type="open"))
        self.assertIsInstance(closed, MultipleChoiceQuestion)
        self.assertIsInstance(opened, OpenQuestion)

    def test_union_alone_does_not_infer_missing_tag(self):
        with self.assertRaises(ValidationError) as caught:
            TypeAdapter(QuizQuestion).validate_python(closed_data())
        self.assertEqual(caught.exception.errors()[0]["type"], "union_tag_not_found")

    def test_legacy_closed_json_is_normalized_before_union_validation(self):
        payload = {"title": "Quiz", "questions": [closed_data()]}
        quiz = Quiz.model_validate_json(json.dumps(payload))
        self.assertIsInstance(quiz.questions[0], MultipleChoiceQuestion)
        self.assertEqual(quiz.questions[0].type, "closed")
        self.assertNotIn("type", payload["questions"][0])

    def test_legacy_open_payload_works_with_unified_and_compatibility_models(self):
        payload = {"title": "Quiz", "questions": [open_data()]}
        for model in (Quiz, OpenQuiz):
            with self.subTest(model=model.__name__):
                quiz = model.model_validate_json(json.dumps(payload))
                self.assertIsInstance(quiz, Quiz)
                self.assertIsInstance(quiz.questions[0], OpenQuestion)
                self.assertEqual(quiz.questions[0].type, "open")

    def test_unified_quiz_accepts_mixed_questions_and_round_trips(self):
        quiz = Quiz(title="Quiz", questions=[closed_data(type="closed"), open_data(type="open")])
        self.assertIsInstance(quiz.questions[0], MultipleChoiceQuestion)
        self.assertIsInstance(quiz.questions[1], OpenQuestion)
        restored = Quiz.model_validate_json(quiz.model_dump_json())
        self.assertEqual(restored, quiz)
        self.assertEqual([question.type for question in restored.questions], ["closed", "open"])

    def test_question_objects_and_legacy_tuples_are_supported(self):
        for questions in (
            [Question(**closed_data()), OpenQuestion(**open_data())],
            (closed_data(), open_data()),
        ):
            with self.subTest(questions=questions):
                quiz = Quiz(title="Quiz", questions=questions)
                self.assertEqual([question.type for question in quiz.questions], ["closed", "open"])

    def test_duplicate_ids_across_types_are_rejected(self):
        for tagged in (False, True):
            closed = closed_data()
            opened = open_data(id=1)
            if tagged:
                closed["type"] = "closed"
                opened["type"] = "open"
            with self.subTest(tagged=tagged):
                with self.assertRaisesRegex(ValidationError, "Question IDs must be unique"):
                    Quiz(title="Quiz", questions=[closed, opened])

    def test_invalid_tags_are_rejected_without_guessing_from_fields(self):
        for tag in ("essay", "", "OPEN", None, 1):
            with self.subTest(tag=tag):
                with self.assertRaises(ValidationError):
                    Quiz(title="Quiz", questions=[closed_data(type=tag)])

    def test_explicit_tag_does_not_fall_back_to_another_shape(self):
        for data in (closed_data(type="open"), open_data(type="closed")):
            with self.subTest(data=data):
                with self.assertRaises(ValidationError):
                    Quiz(title="Quiz", questions=[data])

    def test_direct_question_constructors_validate_literal_type(self):
        with self.assertRaises(ValidationError):
            Question(**closed_data(type="open"))
        with self.assertRaises(ValidationError):
            OpenQuestion(**open_data(type="closed"))

    def test_ambiguous_legacy_data_requires_explicit_type(self):
        ambiguous = closed_data(reference_answer="Legacy additional answer")
        with self.assertRaisesRegex(ValidationError, "explicit type"):
            Quiz(title="Quiz", questions=[ambiguous])
        quiz = Quiz(title="Quiz", questions=[{**ambiguous, "type": "closed"}])
        self.assertIsInstance(quiz.questions[0], MultipleChoiceQuestion)

    def test_normalization_does_not_mutate_input(self):
        payload = {"title": "Quiz", "questions": [closed_data(), open_data()]}
        original = deepcopy(payload)
        Quiz.model_validate(payload)
        self.assertEqual(payload, original)

    def test_unified_json_schema_has_a_type_discriminator(self):
        schema = Quiz.model_json_schema()
        items = schema["properties"]["questions"]["items"]
        self.assertEqual(items["discriminator"]["propertyName"], "type")
        self.assertEqual(set(items["discriminator"]["mapping"]), {"closed", "open"})
        self.assertEqual(len(items["oneOf"]), 2)

    def test_single_type_adapters_reject_opposite_and_mixed_inputs(self):
        for model, wrong, own in (
            (ClosedQuiz, open_data(), closed_data()),
            (OpenQuiz, closed_data(), open_data()),
        ):
            with self.subTest(model=model.__name__):
                self.assertTrue(issubclass(model, Quiz))
                self.assertIsInstance(model(title="Quiz", questions=[own]), Quiz)
                with self.assertRaises(ValidationError):
                    model(title="Quiz", questions=[wrong])
                with self.assertRaises(ValidationError):
                    model(title="Quiz", questions=[own, wrong])


if __name__ == "__main__":
    unittest.main()
