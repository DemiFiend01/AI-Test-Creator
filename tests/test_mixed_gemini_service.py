from copy import deepcopy
import json
import os
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import gemini_service
from app.schemas import MultipleChoiceQuestion, OpenQuestion, Quiz


PDF_BYTES = b"%PDF-1.7\nMock in-memory source\n%%EOF\n"


def mixed_data(num_closed=3, num_open=2):
    questions = []
    for index in range(max(num_closed, num_open)):
        if index < num_open:
            questions.append({
                "id": len(questions) + 1, "type": "open",
                "question": f"Open question {index}",
                "reference_answer": f"Reference {index}", "explanation": "Open explanation",
            })
        if index < num_closed:
            questions.append({
                "id": len(questions) + 1, "type": "closed",
                "question": f"Closed question {index}",
                "options": ["A", "B", "C", "D"],
                "correct_answer": 1, "explanation": "Closed explanation",
            })
    return {"title": "Mixed quiz", "questions": questions}


class MixedQuizGenerationTests(unittest.TestCase):
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
        self.generate.return_value = SimpleNamespace(text=json.dumps(mixed_data()))
        self.sources = (
            (gemini_service.generate_mixed_quiz, "Source material"),
            (gemini_service.generate_mixed_quiz_from_pdf, PDF_BYTES),
        )

    def test_text_generation_uses_exact_counts_schema_model_and_one_request(self):
        os.environ["GEMINI_MODEL"] = "configured-model"
        quiz = gemini_service.generate_mixed_quiz("  Source material  ", 3, 2)
        self.assertIs(type(quiz), Quiz)
        self.assertEqual(quiz.model_dump(), mixed_data())
        self.assertEqual(sum(isinstance(q, MultipleChoiceQuestion) for q in quiz.questions), 3)
        self.assertEqual(sum(isinstance(q, OpenQuestion) for q in quiz.questions), 2)
        self.generate.assert_called_once()
        self.client_factory.assert_called_once_with(api_key="dummy-test-key")
        self.load_dotenv.assert_called_once_with()
        request = self.generate.call_args.kwargs
        self.assertEqual(request["contents"], "Source material")
        self.assertEqual(request["model"], "configured-model")
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        schema = config["response_json_schema"]
        self.assertEqual(schema["properties"]["questions"]["minItems"], 5)
        self.assertEqual(schema["properties"]["questions"]["maxItems"], 5)
        items = schema["properties"]["questions"]["items"]
        self.assertEqual(len(items["anyOf"]), 2)
        self.assertEqual(set(schema["$defs"]), {"MultipleChoiceQuestion", "OpenQuestion"})
        for name in schema["$defs"]:
            self.assertIn("type", schema["$defs"][name]["required"])
        instructions = config["system_instruction"]
        self.assertIn("exactly 3 closed questions and 2 open questions", instructions)
        self.assertIn("based only on the provided source text", instructions)
        self.assertIn("Use the same language as the source text", instructions)
        self.assertIn("interleaved", instructions)
        self.assertIn("nontrivial", instructions)

    def test_pdf_generation_uses_inline_bytes_and_preserves_interleaving(self):
        quiz = gemini_service.generate_mixed_quiz_from_pdf(PDF_BYTES, 3, 2)
        self.assertEqual([q.type for q in quiz.questions], ["open", "closed", "open", "closed", "closed"])
        self.assertEqual([q.id for q in quiz.questions], [1, 2, 3, 4, 5])
        self.generate.assert_called_once()
        request = self.generate.call_args.kwargs
        self.assertEqual(request["contents"][0].inline_data.data, PDF_BYTES)
        self.assertEqual(request["contents"][0].inline_data.mime_type, "application/pdf")
        self.assertIn("PDF document", request["config"]["system_instruction"])
        self.client.files.upload.assert_not_called()

    def test_count_validation_rejects_invalid_values_before_client_creation(self):
        for generator, source in self.sources:
            for closed, opened in (
                (0, 2), (2, 0), (-1, 1), (1.5, 1), (1, 2.0),
                (True, 1), (1, False), ("3", 2), (1, None), (10, 1), (6, 5),
            ):
                with self.subTest(generator=generator.__name__, closed=closed, opened=opened):
                    with self.assertRaises(ValueError):
                        generator(source, closed, opened)
        self.client_factory.assert_not_called()
        self.load_dotenv.assert_not_called()

    def test_minimum_and_maximum_totals(self):
        for generator, source in self.sources:
            for closed, opened in ((1, 1), (1, 9), (9, 1), (5, 5)):
                with self.subTest(generator=generator.__name__, closed=closed, opened=opened):
                    self.generate.return_value.text = json.dumps(mixed_data(closed, opened))
                    quiz = generator(source, closed, opened)
                    self.assertEqual(len(quiz.questions), closed + opened)
                    self.assertEqual(sum(q.type == "closed" for q in quiz.questions), closed)
                    self.assertEqual(sum(q.type == "open" for q in quiz.questions), opened)

    def test_invalid_source_text_and_pdf_are_rejected_before_api(self):
        for text in ("", " \t\n", None, 123):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    gemini_service.generate_mixed_quiz(text, 3, 2)
        for data in (b"", b"invalid PDF", None, "text", PDF_BYTES.ljust(10 * 1024 * 1024 + 1, b" ")):
            with self.subTest(input_type=type(data).__name__):
                with self.assertRaises(ValueError):
                    gemini_service.generate_mixed_quiz_from_pdf(data, 3, 2)
        self.client_factory.assert_not_called()

    def test_incorrect_proportions_are_rejected_without_retry(self):
        self.generate.return_value.text = json.dumps(mixed_data(2, 3))
        for generator, source in self.sources:
            self.generate.reset_mock()
            with self.subTest(generator=generator.__name__):
                with self.assertRaisesRegex(ValueError, "expected 3 closed and 2 open"):
                    generator(source, 3, 2)
                self.generate.assert_called_once()

    def test_wrong_total_count_is_rejected(self):
        self.generate.return_value.text = json.dumps(mixed_data(2, 2))
        for generator, source in self.sources:
            with self.subTest(generator=generator.__name__):
                with self.assertRaisesRegex(ValueError, "expected 5"):
                    generator(source, 3, 2)

    def test_explicit_type_is_required_despite_legacy_model_compatibility(self):
        data = mixed_data()
        del data["questions"][0]["type"]
        self.assertIsInstance(Quiz.model_validate(data), Quiz)
        self.generate.return_value.text = json.dumps(data)
        for generator, source in self.sources:
            with self.subTest(generator=generator.__name__):
                with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                    generator(source, 3, 2)

    def test_invalid_tags_duplicate_ids_and_malformed_fields_are_rejected(self):
        invalid = []
        for tag in ("essay", None, 1):
            data = mixed_data()
            data["questions"][0]["type"] = tag
            invalid.append(data)
        duplicate = mixed_data()
        duplicate["questions"][1]["id"] = duplicate["questions"][0]["id"]
        invalid.append(duplicate)
        for index, field, value in (
            (0, "reference_answer", " "), (0, "explanation", ""),
            (1, "options", ["A", "B", "C"]), (1, "options", ["A", "B", "C", " "]),
            (1, "correct_answer", 4), (1, "question", " "),
        ):
            data = mixed_data()
            data["questions"][index][field] = value
            invalid.append(data)
        for index, field in ((0, "reference_answer"), (1, "correct_answer"), (1, "options")):
            data = mixed_data()
            del data["questions"][index][field]
            invalid.append(data)
        for generator, source in self.sources:
            for data in invalid:
                with self.subTest(generator=generator.__name__, data=data):
                    self.generate.return_value.text = json.dumps(data)
                    with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                        generator(source, 3, 2)

    def test_invalid_json_is_rejected(self):
        for response in (None, "", "not JSON", "[]", "{}"):
            with self.subTest(response=response):
                self.generate.return_value.text = response
                with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                    gemini_service.generate_mixed_quiz("Source material", 3, 2)

    def test_api_failure_is_sanitized_and_not_retried(self):
        self.generate.side_effect = RuntimeError("private-source-and-key")
        try:
            gemini_service.generate_mixed_quiz("Source material", 3, 2)
        except RuntimeError as error:
            rendered = "".join(traceback.format_exception(error))
            self.assertIn("Gemini API request failed", rendered)
            self.assertNotIn("private-source-and-key", rendered)
        else:
            self.fail("Expected a safe API error")
        self.generate.assert_called_once()

    def test_request_schema_does_not_change_unified_model_legacy_behavior(self):
        before = deepcopy(Quiz.model_json_schema())
        gemini_service.generate_mixed_quiz("Source material", 3, 2)
        self.assertEqual(Quiz.model_json_schema(), before)


if __name__ == "__main__":
    unittest.main()
