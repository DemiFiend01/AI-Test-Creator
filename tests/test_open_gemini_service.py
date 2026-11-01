import json
import os
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import gemini_service
from app.mock_quiz import create_mock_quiz
from app.schemas import OpenQuestion, OpenQuiz, Quiz


PDF_BYTES = b"%PDF-1.7\nMock in-memory source\n%%EOF\n"


def open_quiz_data(count=2):
    return {
        "title": "Biologia",
        "questions": [
            {
                "id": index,
                "question": f"Pytanie otwarte {index}",
                "reference_answer": f"Odpowiedź wzorcowa {index}",
                "explanation": f"Wyjaśnienie {index}",
            }
            for index in range(1, count + 1)
        ],
    }


class OpenQuizGenerationTests(unittest.TestCase):
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
        self.generate.return_value = SimpleNamespace(text=json.dumps(open_quiz_data()))
        self.sources = (
            (gemini_service.generate_open_quiz, "Source material"),
            (gemini_service.generate_open_quiz_from_pdf, PDF_BYTES),
        )

    def test_generates_validated_open_quiz_from_text(self):
        os.environ["GEMINI_MODEL"] = "configured-model"
        quiz = gemini_service.generate_open_quiz("  Source material  ", 2)
        self.assertIsInstance(quiz, OpenQuiz)
        self.assertIsInstance(quiz.questions[0], OpenQuestion)
        expected = open_quiz_data()
        for question in expected["questions"]:
            question["type"] = "open"
        self.assertEqual(quiz.model_dump(), expected)
        self.generate.assert_called_once()
        self.client_factory.assert_called_once_with(api_key="dummy-test-key")
        self.load_dotenv.assert_called_once_with()
        request = self.generate.call_args.kwargs
        self.assertEqual(request["model"], "configured-model")
        self.assertEqual(request["contents"], "Source material")
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        schema = config["response_json_schema"]
        self.assertEqual(schema["$defs"]["OpenQuestion"], OpenQuestion.model_json_schema())
        self.assertEqual(schema["properties"]["questions"]["minItems"], 2)
        self.assertEqual(schema["properties"]["questions"]["maxItems"], 2)
        instructions = config["system_instruction"]
        self.assertIn("open-ended quiz", instructions)
        self.assertIn("reference_answer", instructions)
        self.assertIn("based only on the provided source text", instructions)
        self.assertIn("Use the same language as the source text", instructions)
        self.assertNotIn("correct answer index", instructions)

    def test_generates_from_inline_pdf_with_correct_mime_and_bytes(self):
        quiz = gemini_service.generate_open_quiz_from_pdf(PDF_BYTES, 2)
        self.assertIsInstance(quiz, OpenQuiz)
        self.generate.assert_called_once()
        request = self.generate.call_args.kwargs
        part = request["contents"][0]
        self.assertEqual(part.inline_data.data, PDF_BYTES)
        self.assertEqual(part.inline_data.mime_type, "application/pdf")
        self.assertEqual(request["model"], "gemini-3.5-flash-lite")
        self.assertIn("PDF document", request["config"]["system_instruction"])
        self.assertIn("tables, charts and diagrams", request["config"]["system_instruction"])
        self.client.files.upload.assert_not_called()

    def test_default_count_and_count_boundaries(self):
        for generator, source in self.sources:
            for count in (1, 5, 10):
                with self.subTest(generator=generator.__name__, count=count):
                    self.generate.return_value.text = json.dumps(open_quiz_data(count))
                    quiz = generator(source) if count == 5 else generator(source, count)
                    self.assertEqual(len(quiz.questions), count)

    def test_rejects_invalid_text_before_client_creation(self):
        for text in ("", " \t\n", None, 123):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "Source text"):
                    gemini_service.generate_open_quiz(text, 2)
        self.client_factory.assert_not_called()

    def test_rejects_invalid_pdf_before_client_creation(self):
        for data in (b"", b"invalid", None, "not bytes", bytearray(PDF_BYTES)):
            with self.subTest(input_type=type(data).__name__):
                with self.assertRaises(ValueError):
                    gemini_service.generate_open_quiz_from_pdf(data, 2)
        oversized = PDF_BYTES.ljust(gemini_service.MAX_PDF_BYTES + 1, b" ")
        with self.assertRaisesRegex(ValueError, "10 MB"):
            gemini_service.generate_open_quiz_from_pdf(oversized, 2)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_accepts_pdf_exactly_at_10_mib_limit(self):
        data = PDF_BYTES.ljust(10 * 1024 * 1024, b" ")
        quiz = gemini_service.generate_open_quiz_from_pdf(data, 2)
        self.assertEqual(len(quiz.questions), 2)
        self.assertEqual(self.generate.call_args.kwargs["contents"][0].inline_data.data, data)
        self.client.files.upload.assert_not_called()

    def test_rejects_invalid_counts_before_client_creation(self):
        for generator, source in self.sources:
            for count in (0, -1, 11, 1.5, "5", None, True, False):
                with self.subTest(generator=generator.__name__, count=count):
                    with self.assertRaisesRegex(ValueError, "integer between 1 and 10"):
                        generator(source, count)
        self.client_factory.assert_not_called()

    def test_requires_api_key(self):
        del os.environ["API_KEY"]
        for generator, source in self.sources:
            with self.subTest(generator=generator.__name__):
                with self.assertRaisesRegex(RuntimeError, "Set API_KEY"):
                    generator(source, 2)
        self.client_factory.assert_not_called()

    def test_rejects_wrong_question_count(self):
        for generator, source in self.sources:
            for count in (1, 3):
                with self.subTest(generator=generator.__name__, count=count):
                    self.generate.return_value.text = json.dumps(open_quiz_data(count))
                    with self.assertRaisesRegex(ValueError, f"returned {count} questions; expected 2"):
                        generator(source, 2)

    def test_rejects_invalid_json_and_open_quiz_data(self):
        invalid_reference = open_quiz_data()
        invalid_reference["questions"][0]["reference_answer"] = " "
        duplicate_ids = open_quiz_data()
        duplicate_ids["questions"][1]["id"] = 1
        responses = (
            None, "", "not JSON", "{}",
            json.dumps(invalid_reference), json.dumps(duplicate_ids),
            create_mock_quiz(2).model_dump_json(),
        )
        for generator, source in self.sources:
            for response in responses:
                with self.subTest(generator=generator.__name__, response=response):
                    self.generate.return_value.text = response
                    with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                        generator(source, 2)

    def test_api_errors_do_not_expose_details(self):
        self.generate.side_effect = RuntimeError("sensitive-source-and-dummy-test-key")
        for generator, source in self.sources:
            with self.subTest(generator=generator.__name__):
                try:
                    generator(source, 2)
                except RuntimeError as error:
                    rendered = "".join(traceback.format_exception(error))
                    self.assertIn("Gemini API request failed", rendered)
                    self.assertNotIn("sensitive-source-and-dummy-test-key", rendered)
                else:
                    self.fail("Expected a safe API error")

    def test_closed_generation_still_returns_quiz(self):
        self.generate.return_value.text = create_mock_quiz(2).model_dump_json()
        for generator, source in (
            (gemini_service.generate_quiz, "Source material"),
            (gemini_service.generate_quiz_from_pdf, PDF_BYTES),
        ):
            with self.subTest(generator=generator.__name__):
                self.assertIsInstance(generator(source, 2), Quiz)
                config = self.generate.call_args.kwargs["config"]
                self.assertIn("multiple-choice quiz", config["system_instruction"])
                self.assertIn("MultipleChoiceQuestion", config["response_json_schema"]["$defs"])


if __name__ == "__main__":
    unittest.main()
