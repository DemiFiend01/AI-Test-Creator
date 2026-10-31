import importlib
import json
import os
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import gemini_service
from app.schemas import Question, Quiz


def quiz_data(count=1):
    return {
        "title": "Science quiz",
        "questions": [
            {
                "id": index + 1,
                "question": "What does Earth orbit?",
                "options": ["The Sun", "Mars", "Venus", "The Moon"],
                "correct_answer": 0,
                "explanation": "The source states that Earth orbits the Sun.",
            }
            for index in range(count)
        ],
    }


class GenerateQuizTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"API_KEY": "dummy-test-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

        dotenv = patch("app.gemini_service.load_dotenv")
        self.load_dotenv = dotenv.start()
        self.addCleanup(dotenv.stop)

        client = patch("app.gemini_service.genai.Client")
        self.client_factory = client.start()
        self.addCleanup(client.stop)
        self.client = self.client_factory.return_value.__enter__.return_value
        self.generate = self.client.models.generate_content
        self.generate.return_value = SimpleNamespace(text=json.dumps(quiz_data()))

    def test_returns_validated_quiz_and_uses_structured_json(self):
        quiz = gemini_service.generate_quiz(" Earth orbits the Sun. ", 1)
        self.assertIsInstance(quiz, Quiz)
        self.assertIsInstance(quiz.questions[0], Question)
        self.assertEqual(quiz.model_dump(), quiz_data())
        self.load_dotenv.assert_called_once_with()
        self.client_factory.assert_called_once_with(api_key="dummy-test-key")
        self.generate.assert_called_once()
        request = self.generate.call_args.kwargs
        self.assertEqual(request["model"], "gemini-3.5-flash-lite")
        self.assertEqual(request["contents"], "Earth orbits the Sun.")
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        schema = config["response_json_schema"]
        self.assertEqual(schema["$defs"]["Question"], Question.model_json_schema())
        self.assertEqual(schema["properties"]["questions"]["minItems"], 1)
        self.assertEqual(schema["properties"]["questions"]["maxItems"], 1)
        self.assertIn("based only on the provided source text", config["system_instruction"])

    def test_default_question_count_is_five(self):
        self.generate.return_value.text = json.dumps(quiz_data(5))
        quiz = gemini_service.generate_quiz("Earth orbits the Sun.")
        self.assertEqual(len(quiz.questions), 5)

    def test_accepts_ten_questions(self):
        self.generate.return_value.text = json.dumps(quiz_data(10))
        quiz = gemini_service.generate_quiz("Earth orbits the Sun.", 10)
        self.assertEqual(len(quiz.questions), 10)

    def test_uses_configured_model(self):
        os.environ["GEMINI_MODEL"] = "custom-model"
        gemini_service.generate_quiz("Earth orbits the Sun.", 1)
        self.assertEqual(self.generate.call_args.kwargs["model"], "custom-model")

    def test_loads_api_key_from_dotenv_at_call_time(self):
        del os.environ["API_KEY"]
        self.load_dotenv.side_effect = lambda: os.environ.update(API_KEY="dotenv-dummy-key")
        gemini_service.generate_quiz("Earth orbits the Sun.", 1)
        self.client_factory.assert_called_once_with(api_key="dotenv-dummy-key")

    def test_rejects_empty_or_invalid_text_before_client_creation(self):
        for text in ("", " \t\n", None, 123):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "Source text"):
                    gemini_service.generate_quiz(text, 1)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_rejects_invalid_question_counts_before_client_creation(self):
        for count in (0, -1, 11, 1.5, "5", None, True, False):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "integer between 1 and 10"):
                    gemini_service.generate_quiz("Earth orbits the Sun.", count)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_rejects_missing_or_blank_api_key(self):
        for key in (None, "", " \t"):
            with self.subTest(key=key):
                if key is None:
                    os.environ.pop("API_KEY", None)
                else:
                    os.environ["API_KEY"] = key
                with self.assertRaisesRegex(RuntimeError, "Set API_KEY in your .env file"):
                    gemini_service.generate_quiz("Earth orbits the Sun.", 1)
        self.client_factory.assert_not_called()

    def test_api_failure_does_not_expose_sdk_details(self):
        self.generate.side_effect = RuntimeError("Failed request with dummy-test-key")
        try:
            gemini_service.generate_quiz("Earth orbits the Sun.", 1)
        except RuntimeError as error:
            self.assertIn("Gemini API request failed", str(error))
            self.assertNotIn("dummy-test-key", "".join(traceback.format_exception(error)))
        else:
            self.fail("Expected RuntimeError for an API failure")

    def test_handles_client_initialization_failure(self):
        self.client_factory.side_effect = ValueError("Invalid dummy-test-key")
        with self.assertRaisesRegex(RuntimeError, "Gemini API request failed"):
            gemini_service.generate_quiz("Earth orbits the Sun.", 1)
        self.generate.assert_not_called()

    def test_rejects_empty_or_malformed_json_response(self):
        for response in (None, "", " \n", "not JSON", "```json\n{}\n```", "null", "[]"):
            with self.subTest(response=response):
                self.generate.return_value = SimpleNamespace(text=response)
                with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                    gemini_service.generate_quiz("Earth orbits the Sun.", 1)

    def test_rejects_response_without_text(self):
        self.generate.return_value = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, "invalid quiz response"):
            gemini_service.generate_quiz("Earth orbits the Sun.", 1)

    def test_rejects_invalid_quiz_fields(self):
        invalid_responses = [
            {},
            {"title": " ", "questions": quiz_data()["questions"]},
            {"title": "Science quiz", "questions": []},
        ]
        for field, value in (
            ("id", 0),
            ("question", " "),
            ("options", ["A", "B", "C"]),
            ("options", ["A", "B", "C", " "]),
            ("correct_answer", 4),
            ("explanation", ""),
        ):
            data = quiz_data()
            data["questions"][0][field] = value
            invalid_responses.append(data)
        for data in invalid_responses:
            with self.subTest(data=data):
                self.generate.return_value.text = json.dumps(data)
                with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                    gemini_service.generate_quiz("Earth orbits the Sun.", 1)

    def test_rejects_duplicate_question_ids(self):
        data = quiz_data(2)
        data["questions"][1]["id"] = 1
        self.generate.return_value.text = json.dumps(data)
        with self.assertRaisesRegex(ValueError, "invalid quiz response"):
            gemini_service.generate_quiz("Earth orbits the Sun.", 2)

    def test_rejects_wrong_question_count(self):
        for count in (1, 3):
            with self.subTest(count=count):
                self.generate.return_value.text = json.dumps(quiz_data(count))
                with self.assertRaisesRegex(ValueError, f"returned {count} questions; expected 2"):
                    gemini_service.generate_quiz("Earth orbits the Sun.", 2)

    def test_import_has_no_configuration_or_api_side_effects(self):
        os.environ.pop("API_KEY")
        importlib.reload(gemini_service)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
