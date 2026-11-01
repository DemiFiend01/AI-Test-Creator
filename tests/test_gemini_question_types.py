from copy import deepcopy
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import gemini_service
from app.mock_quiz import create_mock_quiz
from app.schemas import OpenQuestion, OpenQuiz, Quiz


PDF_BYTES = b"%PDF-1.7\nMock in-memory source\n%%EOF\n"


def open_quiz_data():
    return OpenQuiz(title="Quiz", questions=[
        OpenQuestion(id=1, question="Q1", reference_answer="A1", explanation="E1"),
        OpenQuestion(id=2, question="Q2", reference_answer="A2", explanation="E2"),
    ]).model_dump()


class GeminiQuestionTypeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"API_KEY": "dummy-key"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        dotenv = patch("app.gemini_service.load_dotenv")
        dotenv.start()
        self.addCleanup(dotenv.stop)
        client = patch("app.gemini_service.genai.Client")
        self.client_factory = client.start()
        self.addCleanup(client.stop)
        self.client = self.client_factory.return_value.__enter__.return_value
        self.generate = self.client.models.generate_content
        self.modes = (
            ("closed", gemini_service.generate_quiz, "Source text"),
            ("closed", gemini_service.generate_quiz_from_pdf, PDF_BYTES),
            ("open", gemini_service.generate_open_quiz, "Source text"),
            ("open", gemini_service.generate_open_quiz_from_pdf, PDF_BYTES),
        )

    def test_all_modes_send_only_their_own_question_schema(self):
        for kind, generator, source in self.modes:
            with self.subTest(generator=generator.__name__):
                data = create_mock_quiz(2).model_dump() if kind == "closed" else open_quiz_data()
                self.generate.return_value = SimpleNamespace(text=json.dumps(data))
                quiz = generator(source, 2)
                self.assertIsInstance(quiz, Quiz)
                self.assertTrue(all(question.type == kind for question in quiz.questions))
                config = self.generate.call_args.kwargs["config"]
                question_name = "MultipleChoiceQuestion" if kind == "closed" else "OpenQuestion"
                schema = config["response_json_schema"]
                self.assertEqual(set(schema["$defs"]), {question_name})
                self.assertEqual(schema["properties"]["questions"]["items"], {
                    "$ref": f"#/$defs/{question_name}",
                })
                self.assertEqual(schema["$defs"][question_name]["properties"]["type"]["const"], kind)
                self.assertIn(f'type to "{kind}"', config["system_instruction"])
        self.client.files.upload.assert_not_called()

    def test_all_modes_accept_their_own_legacy_json_without_tags(self):
        for kind, generator, source in self.modes:
            with self.subTest(generator=generator.__name__):
                data = create_mock_quiz(2).model_dump() if kind == "closed" else open_quiz_data()
                for question in data["questions"]:
                    del question["type"]
                self.generate.return_value = SimpleNamespace(text=json.dumps(data))
                quiz = generator(source, 2)
                self.assertEqual([question.type for question in quiz.questions], [kind, kind])

    def test_all_modes_reject_wrong_question_type_even_without_tags(self):
        for kind, generator, source in self.modes:
            for legacy in (False, True):
                with self.subTest(generator=generator.__name__, legacy=legacy):
                    data = open_quiz_data() if kind == "closed" else create_mock_quiz(2).model_dump()
                    if legacy:
                        for question in data["questions"]:
                            del question["type"]
                    self.generate.return_value = SimpleNamespace(text=json.dumps(data))
                    with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                        generator(source, 2)

    def test_all_modes_reject_mixed_payloads(self):
        mixed = create_mock_quiz(2).model_dump()
        mixed["questions"][1] = open_quiz_data()["questions"][1]
        for _kind, generator, source in self.modes:
            for legacy in (False, True):
                with self.subTest(generator=generator.__name__, legacy=legacy):
                    data = deepcopy(mixed)
                    if legacy:
                        for question in data["questions"]:
                            del question["type"]
                    self.generate.return_value = SimpleNamespace(text=json.dumps(data))
                    with self.assertRaisesRegex(ValueError, "invalid quiz response"):
                        generator(source, 2)


if __name__ == "__main__":
    unittest.main()
