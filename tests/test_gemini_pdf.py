import json
import os
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import gemini_service
from app.mock_quiz import create_mock_quiz
from app.schemas import Quiz


# A header fixture is sufficient because every SDK request is mocked.
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


class GenerateQuizFromPdfTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"API_KEY": "dummy-pdf-key"}, clear=True)
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
        self.generate.return_value = SimpleNamespace(text=create_mock_quiz(2).model_dump_json())

    def test_pdf_bytes_mime_type_schema_and_model(self):
        os.environ["GEMINI_MODEL"] = "configured-model"
        quiz = gemini_service.generate_quiz_from_pdf(PDF_BYTES, 2)
        self.assertIsInstance(quiz, Quiz)
        self.assertEqual(len(quiz.questions), 2)
        self.load_dotenv.assert_called_once_with()
        self.client_factory.assert_called_once_with(api_key="dummy-pdf-key")
        self.generate.assert_called_once()
        request = self.generate.call_args.kwargs
        self.assertEqual(request["model"], "configured-model")
        self.assertEqual(len(request["contents"]), 1)
        part = request["contents"][0]
        self.assertEqual(part.inline_data.data, PDF_BYTES)
        self.assertEqual(part.inline_data.mime_type, "application/pdf")
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["response_json_schema"]["properties"]["questions"]["minItems"], 2)
        self.assertEqual(config["response_json_schema"]["properties"]["questions"]["maxItems"], 2)
        self.assertIn("based only on the provided PDF document", config["system_instruction"])
        self.assertIn("tables, charts and diagrams", config["system_instruction"])
        self.assertEqual(config["http_options"]["retry_options"]["attempts"], 1)
        self.client.files.upload.assert_not_called()

    def test_default_model_and_question_count(self):
        self.generate.return_value.text = create_mock_quiz(5).model_dump_json()
        quiz = gemini_service.generate_quiz_from_pdf(PDF_BYTES)
        self.assertEqual(len(quiz.questions), 5)
        self.assertEqual(self.generate.call_args.kwargs["model"], "gemini-3.5-flash-lite")

    def test_accepts_question_count_boundaries(self):
        for count in (1, 10):
            with self.subTest(count=count):
                self.generate.return_value.text = create_mock_quiz(count).model_dump_json()
                self.assertEqual(len(gemini_service.generate_quiz_from_pdf(PDF_BYTES, count).questions), count)

    def test_rejects_empty_and_non_bytes_input_before_api(self):
        for data in (b"", None, "not bytes", bytearray(PDF_BYTES)):
            with self.subTest(input_type=type(data).__name__):
                with self.assertRaisesRegex(ValueError, "non-empty bytes"):
                    gemini_service.generate_quiz_from_pdf(data, 2)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_rejects_invalid_signature_before_api(self):
        with self.assertRaisesRegex(ValueError, "Invalid PDF signature"):
            gemini_service.generate_quiz_from_pdf(b"This is not a PDF", 2)
        self.client_factory.assert_not_called()

    def test_rejects_oversized_pdf_before_api(self):
        data = PDF_BYTES + b" " * (gemini_service.MAX_PDF_BYTES + 1 - len(PDF_BYTES))
        with self.assertRaisesRegex(ValueError, "10 MB"):
            gemini_service.generate_quiz_from_pdf(data, 2)
        self.client_factory.assert_not_called()

    def test_accepts_exact_size_limit(self):
        self.assertEqual(gemini_service.MAX_PDF_BYTES, 10 * 1024 * 1024)
        data = PDF_BYTES + b" " * (gemini_service.MAX_PDF_BYTES - len(PDF_BYTES))
        self.assertEqual(len(gemini_service.generate_quiz_from_pdf(data, 2).questions), 2)
        self.generate.assert_called_once()
        self.assertEqual(self.generate.call_args.kwargs["contents"][0].inline_data.data, data)
        self.client.files.upload.assert_not_called()

    def test_rejects_invalid_question_counts_before_api(self):
        for count in (0, -1, 11, 1.5, "5", None, True, False):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "integer between 1 and 10"):
                    gemini_service.generate_quiz_from_pdf(PDF_BYTES, count)
        self.client_factory.assert_not_called()

    def test_missing_key(self):
        del os.environ["API_KEY"]
        with self.assertRaisesRegex(RuntimeError, "Set API_KEY"):
            gemini_service.generate_quiz_from_pdf(PDF_BYTES, 2)
        self.client_factory.assert_not_called()

    def test_api_failure_is_sanitized_and_not_retried(self):
        self.generate.side_effect = RuntimeError("private PDF contents and dummy-pdf-key")
        try:
            gemini_service.generate_quiz_from_pdf(PDF_BYTES, 2)
        except RuntimeError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertIn("Gemini API request failed", rendered)
            self.assertNotIn("dummy-pdf-key", rendered)
            self.assertNotIn("private PDF contents", rendered)
        else:
            self.fail("Expected an API failure")
        self.generate.assert_called_once()

    def test_rejects_invalid_json_and_quiz_data(self):
        invalid_quiz = create_mock_quiz(2).model_dump()
        invalid_quiz["questions"][0]["correct_answer"] = 4
        for response in (None, "", "not JSON", "{}", json.dumps(invalid_quiz)):
            with self.subTest(response=response):
                self.generate.return_value.text = response
                with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                    gemini_service.generate_quiz_from_pdf(PDF_BYTES, 2)

    def test_rejects_wrong_question_count(self):
        for count in (1, 3):
            with self.subTest(count=count):
                self.generate.return_value.text = create_mock_quiz(count).model_dump_json()
                with self.assertRaisesRegex(ValueError, f"returned {count} questions; expected 2"):
                    gemini_service.generate_quiz_from_pdf(PDF_BYTES, 2)

    def test_existing_text_request_is_unchanged(self):
        quiz = gemini_service.generate_quiz("  Source material  ", 2)
        self.assertEqual(len(quiz.questions), 2)
        request = self.generate.call_args.kwargs
        self.assertEqual(request["contents"], "Source material")
        self.assertIn("based only on the provided source text", request["config"]["system_instruction"])
        self.assertNotIn("http_options", request["config"])
        self.client_factory.assert_called_once_with(api_key="dummy-pdf-key")


if __name__ == "__main__":
    unittest.main()
