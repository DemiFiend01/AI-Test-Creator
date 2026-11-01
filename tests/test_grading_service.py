from copy import deepcopy
import importlib
import json
import os
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import grading_service
from app.grading_schemas import OpenGradingResult
from app.mock_quiz import create_mock_quiz
from app.quiz_scoring import score_quiz
from app.schemas import OpenQuestion


def example_questions():
    return [
        OpenQuestion(
            id=question_id,
            question="What does Earth orbit?",
            reference_answer="Earth orbits the Sun.",
            explanation="The Sun is the star at the centre of our Solar System.",
        )
        for question_id in (2, 7, 11)
    ]


def grade_data(question_id, points=1.0, feedback="Constructive feedback"):
    return {"question_id": question_id, "points": points, "feedback": feedback}


class OpenAnswerGradingTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"API_KEY": "dummy-grading-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("app.grading_service.load_dotenv")
        self.load_dotenv = dotenv.start()
        self.addCleanup(dotenv.stop)
        client = patch("app.grading_service.genai.Client")
        self.client_factory = client.start()
        self.addCleanup(client.stop)
        self.client = self.client_factory.return_value.__enter__.return_value
        self.generate = self.client.models.generate_content
        self.generate.return_value = SimpleNamespace(text=json.dumps({
            "grades": [grade_data(2, 1.0), grade_data(7, 0.5), grade_data(11, 0.0)],
        }))
        self.questions = example_questions()
        self.answers = {2: "The Sun", 7: "A star", 11: "Mars"}

    def set_response(self, grades):
        self.generate.return_value.text = json.dumps({"grades": grades})

    def test_full_credit_response(self):
        self.set_response([grade_data(2, 1.0)])
        result = grading_service.grade_open_answers(self.questions[:1], {2: "The Sun"})
        self.assertEqual(result.grades[0].points, 1.0)

    def test_partial_credit_response(self):
        self.set_response([grade_data(2, 0.5, "Identify which star.")])
        result = grading_service.grade_open_answers(self.questions[:1], {2: "A star"})
        self.assertEqual(result.grades[0].points, 0.5)

    def test_incorrect_response_returns_zero_not_a_service_failure(self):
        self.set_response([grade_data(2, 0.0)])
        result = grading_service.grade_open_answers(self.questions[:1], {2: "Mars"})
        self.assertIsInstance(result, OpenGradingResult)
        self.assertEqual(result.grades[0].points, 0.0)

    def test_mocked_paraphrase_grade_is_accepted_not_semantically_proven(self):
        """This verifies the contract for a stubbed grade, not Gemini's accuracy."""
        self.set_response([grade_data(2, 1.0, "Equivalent explanation.")])
        answer = "Our planet moves around our star."
        result = grading_service.grade_open_answers(self.questions[:1], {2: answer})
        self.assertEqual(result.grades[0].points, 1.0)
        payload = json.loads(self.generate.call_args.kwargs["contents"])
        self.assertEqual(payload["items"][0]["student_answer"], answer)

    def test_several_answers_use_one_structured_request_with_existing_configuration(self):
        os.environ["GEMINI_MODEL"] = "selected-grading-model"
        result = grading_service.grade_open_answers(self.questions, self.answers)
        self.assertEqual([grade.points for grade in result.grades], [1.0, 0.5, 0.0])
        self.generate.assert_called_once()
        self.client_factory.assert_called_once()
        client_options = self.client_factory.call_args.kwargs
        self.assertEqual(client_options["api_key"], "dummy-grading-key")
        self.assertEqual(client_options["http_options"].retry_options.attempts, 1)
        self.load_dotenv.assert_called_once_with()
        request = self.generate.call_args.kwargs
        self.assertEqual(request["model"], "selected-grading-model")
        payload = json.loads(request["contents"])
        self.assertEqual(payload, {"items": [
            {
                "question_id": question.id, "question": question.question,
                "reference_answer": question.reference_answer,
                "explanation": question.explanation,
                "student_answer": self.answers[question.id],
            }
            for question in self.questions
        ]})
        self.assertNotIn("dummy-grading-key", request["contents"])
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertNotIn("http_options", config)
        schema = config["response_json_schema"]
        self.assertEqual(schema["properties"]["grades"]["minItems"], 3)
        self.assertEqual(schema["properties"]["grades"]["maxItems"], 3)
        fields = schema["$defs"]["OpenAnswerGrade"]["properties"]
        self.assertEqual(fields["question_id"]["enum"], [2, 7, 11])
        self.assertEqual(fields["points"]["enum"], [0.0, 0.5, 1.0])
        instructions = config["system_instruction"]
        for phrase in ("semantic", "paraphrases", "0.0", "0.5", "1.0", "same language", "untrusted data"):
            self.assertIn(phrase, instructions)
        self.client.files.upload.assert_not_called()

    def test_default_model(self):
        grading_service.grade_open_answers(self.questions, self.answers)
        self.assertEqual(self.generate.call_args.kwargs["model"], "gemini-3.5-flash-lite")

    def test_all_blank_answers_are_local_without_api_key(self):
        del os.environ["API_KEY"]
        result = grading_service.grade_open_answers(self.questions, {2: "", 7: " \t\n", 11: "   "})
        self.assertEqual([grade.question_id for grade in result.grades], [2, 7, 11])
        self.assertTrue(all(grade.points == 0.0 for grade in result.grades))
        self.assertTrue(all(grade.feedback == "Nie udzielono odpowiedzi." for grade in result.grades))
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_missing_answers_are_locally_unanswered(self):
        result = grading_service.grade_open_answers(self.questions, {})
        self.assertEqual(len(result.grades), 3)
        self.assertTrue(all(grade.points == 0.0 for grade in result.grades))
        self.client_factory.assert_not_called()

    def test_empty_question_list_returns_empty_result(self):
        self.assertEqual(grading_service.grade_open_answers([], {}).grades, [])
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_mixed_blank_and_nonblank_answers_send_only_nonblank(self):
        self.set_response([grade_data(2, 0.5)])
        result = grading_service.grade_open_answers(self.questions, {2: "A star", 7: " \n"})
        self.assertEqual([grade.points for grade in result.grades], [0.5, 0.0, 0.0])
        self.generate.assert_called_once()
        items = json.loads(self.generate.call_args.kwargs["contents"])["items"]
        self.assertEqual([item["question_id"] for item in items], [2])

    def test_response_cannot_grade_a_locally_unanswered_question_instead(self):
        self.set_response([grade_data(7, 1.0)])
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            grading_service.grade_open_answers(self.questions, {2: "Answer", 7: " "})

    def test_invalid_scores_and_feedback_in_response_are_rejected(self):
        for points in (0.25, 0.75, 2, -1, True, "1"):
            with self.subTest(points=points):
                self.set_response([grade_data(2, points)])
                with self.assertRaisesRegex(ValueError, "invalid open-answer grading data"):
                    grading_service.grade_open_answers(self.questions[:1], {2: "Answer"})
        for feedback in ("", " \t\n"):
            with self.subTest(feedback=feedback):
                self.set_response([grade_data(2, 1.0, feedback)])
                with self.assertRaises(ValueError):
                    grading_service.grade_open_answers(self.questions[:1], {2: "Answer"})

    def test_missing_duplicate_unknown_and_extra_grades_are_rejected_without_retry(self):
        for grades in (
            [],
            [grade_data(2), grade_data(7)],
            [grade_data(2), grade_data(2), grade_data(11)],
            [grade_data(2), grade_data(7), grade_data(99)],
            [grade_data(2), grade_data(7), grade_data(11), grade_data(99)],
            [{"points": 1.0, "feedback": "Missing ID"}, grade_data(7), grade_data(11)],
        ):
            with self.subTest(grades=grades):
                self.generate.reset_mock()
                self.set_response(grades)
                with self.assertRaises(ValueError):
                    grading_service.grade_open_answers(self.questions, self.answers)
                self.generate.assert_called_once()

    def test_invalid_json_and_missing_response_fields_are_rejected(self):
        for text in (None, "", "not JSON", "[]", "{}", "```json\n{}\n```"):
            with self.subTest(text=text):
                self.generate.return_value = SimpleNamespace(text=text)
                with self.assertRaisesRegex(ValueError, "invalid open-answer grading data"):
                    grading_service.grade_open_answers(self.questions, self.answers)
        self.generate.return_value = SimpleNamespace()
        with self.assertRaises(ValueError):
            grading_service.grade_open_answers(self.questions, self.answers)

    def test_missing_or_blank_api_key(self):
        for key in (None, "", " \t"):
            with self.subTest(key=key):
                if key is None:
                    os.environ.pop("API_KEY", None)
                else:
                    os.environ["API_KEY"] = key
                with self.assertRaisesRegex(RuntimeError, "Set API_KEY in your .env file"):
                    grading_service.grade_open_answers(self.questions, self.answers)
        self.client_factory.assert_not_called()

    def test_api_failure_is_safe_and_does_not_return_local_partial_results(self):
        self.generate.side_effect = RuntimeError("private document and dummy-grading-key")
        try:
            grading_service.grade_open_answers(self.questions, {2: "Answer", 7: " "})
        except RuntimeError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertIn("Gemini grading request failed", rendered)
            self.assertNotIn("dummy-grading-key", rendered)
            self.assertNotIn("private document", rendered)
        else:
            self.fail("A grading failure must raise, not fabricate zero grades")
        self.generate.assert_called_once()

    def test_client_initialization_failure_is_safe(self):
        self.client_factory.side_effect = ValueError("private dummy-grading-key")
        with self.assertRaisesRegex(RuntimeError, "Gemini grading request failed") as caught:
            grading_service.grade_open_answers(self.questions, self.answers)
        self.assertNotIn("dummy-grading-key", "".join(traceback.format_exception(caught.exception)))
        self.generate.assert_not_called()

    def test_invalid_input_is_rejected_before_api(self):
        for questions in (None, {}, tuple(self.questions), [{"id": 2}], create_mock_quiz(1).questions):
            with self.subTest(questions=questions):
                with self.assertRaises(ValueError):
                    grading_service.grade_open_answers(questions, {})
        for answers in (None, [], {"2": "Answer"}, {0: "Answer"}, {True: "Answer"}, {2: None}, {2: 1}, {99: ""}):
            with self.subTest(answers=answers):
                with self.assertRaises(ValueError):
                    grading_service.grade_open_answers(self.questions, answers)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_duplicate_question_ids_and_modified_invalid_models_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            grading_service.grade_open_answers([self.questions[0], self.questions[0]], {})
        self.questions[0].id = 0
        with self.assertRaisesRegex(ValueError, "Invalid open question data"):
            grading_service.grade_open_answers(self.questions, {})
        self.client_factory.assert_not_called()

    def test_response_order_is_normalized_without_changing_grades_or_inputs(self):
        self.set_response([grade_data(11, 0.0, "F11"), grade_data(2, 1.0, "F2"), grade_data(7, 0.5, "F7")])
        original_questions = [question.model_dump() for question in self.questions]
        original_answers = deepcopy(self.answers)
        result = grading_service.grade_open_answers(self.questions, self.answers)
        self.assertEqual([grade.model_dump() for grade in result.grades], [
            grade_data(2, 1.0, "F2"), grade_data(7, 0.5, "F7"), grade_data(11, 0.0, "F11"),
        ])
        self.assertEqual([question.model_dump() for question in self.questions], original_questions)
        self.assertEqual(self.answers, original_answers)

    def test_api_failure_does_not_mutate_input_data(self):
        original_questions = [question.model_dump() for question in self.questions]
        original_answers = deepcopy(self.answers)
        self.generate.side_effect = RuntimeError("Failed request")
        with self.assertRaises(RuntimeError):
            grading_service.grade_open_answers(self.questions, self.answers)
        self.assertEqual([question.model_dump() for question in self.questions], original_questions)
        self.assertEqual(self.answers, original_answers)

    def test_import_has_no_api_or_configuration_side_effects(self):
        os.environ.pop("API_KEY")
        importlib.reload(grading_service)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_existing_quiz_generation_and_closed_scoring_are_unchanged(self):
        from app.gemini_service import generate_quiz

        quiz = create_mock_quiz(2)
        self.generate.return_value.text = quiz.model_dump_json()
        with patch("app.gemini_service.load_dotenv"):
            generated = generate_quiz("Source material", 2)
        answers = {question.id: question.correct_answer for question in generated.questions}
        result = score_quiz(generated, answers)
        self.assertEqual(result["points"], 2)
        self.assertEqual(result["percentage"], 100.0)
        self.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
