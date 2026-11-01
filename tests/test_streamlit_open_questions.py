from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.grading_schemas import OpenAnswerGrade, OpenGradingResult
from app.mock_quiz import create_mock_quiz
from app.quiz_scoring import score_quiz
from app.schemas import OpenQuestion, OpenQuiz, Quiz


APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"
PDF_BYTES = b"%PDF-1.7\nMock in-memory source\n%%EOF\n"


def example_open_quiz():
    return OpenQuiz(
        title="Quiz otwarty",
        questions=[
            OpenQuestion(
                id=index,
                question=f"Wyjaśnij pojęcie {index}.",
                reference_answer=f"REFERENCE_ONLY_{index}",
                explanation=f"EXPLANATION_ONLY_{index}",
            )
            for index in (1, 2)
        ],
    )


class OpenQuizStreamlitTests(unittest.TestCase):
    def setUp(self):
        client = patch(
            "app.gemini_service.genai.Client",
            side_effect=AssertionError("Real Gemini requests are disabled in tests"),
        )
        self.client = client.start()
        self.addCleanup(client.stop)
        self.open_text = self.patch_generator("generate_open_quiz", example_open_quiz())
        self.open_pdf = self.patch_generator("generate_open_quiz_from_pdf", example_open_quiz())
        self.closed_text = self.patch_generator("generate_quiz", create_mock_quiz(1))
        self.closed_pdf = self.patch_generator("generate_quiz_from_pdf", create_mock_quiz(1))
        # Fixed UI test responses, not an assessment of answer correctness.
        grading = patch("app.grading_service.grade_open_answers", side_effect=lambda questions, answers: (
            OpenGradingResult(grades=[
                OpenAnswerGrade(
                    question_id=question.id,
                    points=1.0 if answers.get(question.id, "").strip() else 0.0,
                    feedback=f"MOCK_FEEDBACK_{question.id}",
                ) for question in questions
            ])
        ))
        self.grading = grading.start()
        self.addCleanup(grading.stop)
        scoring = patch("app.quiz_scoring.score_quiz", wraps=score_quiz)
        self.scoring = scoring.start()
        self.addCleanup(scoring.stop)
        self.app = AppTest.from_file(str(APP_PATH)).run()
        self.assertFalse(self.app.exception)

    def patch_generator(self, name, quiz):
        generator = patch(f"app.gemini_service.{name}", return_value=quiz)
        mock = generator.start()
        self.addCleanup(generator.stop)
        return mock

    def tearDown(self):
        self.client.assert_not_called()

    def select_open(self):
        self.app.radio(key="setup_question_type").set_value("Otwarte").run()
        self.assertFalse(self.app.exception)

    def submit_open_text(self, text="Materiał źródłowy", count=2):
        self.select_open()
        self.app.text_area(key="setup_source_text").input(text)
        self.app.selectbox(key="setup_num_questions").select(count)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)

    def rendered_text(self):
        elements = list(self.app.markdown) + list(self.app.caption) + list(self.app.info)
        return "\n".join(str(element.value) for element in elements)

    def test_defaults_to_closed_and_selection_does_not_generate(self):
        self.assertEqual(self.app.radio(key="setup_question_type").value, "Zamknięte")
        self.select_open()
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.open_text.assert_not_called()
        self.open_pdf.assert_not_called()
        self.closed_text.assert_not_called()
        self.closed_pdf.assert_not_called()

    def test_import_does_not_generate_open_questions(self):
        runpy.run_path(str(APP_PATH), run_name="streamlit_import_test")
        self.open_text.assert_not_called()
        self.open_pdf.assert_not_called()

    def test_open_text_quiz_hides_reference_answers_before_submission(self):
        self.submit_open_text()
        self.open_text.assert_called_once_with("Materiał źródłowy", 2)
        self.closed_text.assert_not_called()
        self.assertIsInstance(self.app.session_state["quiz"], OpenQuiz)
        self.assertEqual(self.app.session_state["stage"], "quiz")
        self.assertEqual(len(self.app.text_area), 2)
        self.assertEqual(len(self.app.radio), 0)
        self.assertEqual(len(self.app.metric), 0)
        self.assertEqual(len(self.app.json), 0)
        for question in self.open_text.return_value.questions:
            self.assertNotIn(question.reference_answer, self.rendered_text())
            self.assertNotIn(question.explanation, self.rendered_text())
        self.scoring.assert_not_called()
        self.grading.assert_not_called()

    def test_answers_survive_widget_and_manual_reruns_without_generating(self):
        self.submit_open_text()
        self.app.text_area(key="open_quiz_answer_1").input("Moja odpowiedź").run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["answers"], {1: "Moja odpowiedź", 2: ""})
        self.app.run()
        self.assertEqual(self.app.text_area(key="open_quiz_answer_1").value, "Moja odpowiedź")
        self.assertNotIn("REFERENCE_ONLY_1", self.rendered_text())
        self.open_text.assert_called_once_with("Materiał źródłowy", 2)
        self.open_pdf.assert_not_called()
        self.scoring.assert_not_called()
        self.grading.assert_not_called()

    def test_submission_displays_responses_grades_and_reference_answers(self):
        self.submit_open_text()
        self.app.text_area(key="open_quiz_answer_1").input("Moja odpowiedź").run()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "results")
        rendered = self.rendered_text()
        self.assertIn("Moja odpowiedź", rendered)
        self.assertIn("Nie udzielono odpowiedzi.", rendered)
        for question in self.open_text.return_value.questions:
            self.assertIn(question.reference_answer, rendered)
            self.assertIn(question.explanation, rendered)
        self.assertIn("oceniane z pomocą AI", rendered)
        self.assertIn("MOCK_FEEDBACK_1", rendered)
        self.assertEqual([metric.value for metric in self.app.metric], ["1 / 2", "50%"])
        self.assertIsInstance(self.app.session_state["results"]["open_grades"], OpenGradingResult)
        self.scoring.assert_not_called()
        self.app.run()
        self.grading.assert_called_once()
        self.open_text.assert_called_once_with("Materiał źródłowy", 2)
        self.open_pdf.assert_not_called()

    def test_all_open_questions_may_be_unanswered(self):
        self.submit_open_text()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "results")
        self.assertEqual(self.app.session_state["answers"], {1: "", 2: ""})
        self.assertEqual([metric.value for metric in self.app.metric], ["0 / 2", "0%"])
        self.scoring.assert_not_called()
        self.open_text.assert_called_once()

    def test_open_pdf_uses_only_the_open_pdf_generator_after_submission(self):
        self.select_open()
        self.app.radio(key="setup_material_source").set_value("Prześlij PDF").run()
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.run()
        self.open_pdf.assert_not_called()
        self.app.selectbox(key="setup_num_questions").select(2)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.open_pdf.assert_called_once_with(PDF_BYTES, 2)
        self.open_text.assert_not_called()
        self.closed_pdf.assert_not_called()
        self.assertIsInstance(self.app.session_state["quiz"], OpenQuiz)
        self.assertEqual(len(self.app.text_area), 2)

    def test_empty_open_source_is_rejected_without_generating(self):
        self.submit_open_text(" \t\n")
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertTrue(self.app.error)
        self.open_text.assert_not_called()

    def test_open_generation_error_preserves_settings_and_hides_details(self):
        self.open_text.side_effect = RuntimeError("private-key-and-source")
        self.submit_open_text("Materiał do zachowania", count=3)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.radio(key="setup_question_type").value, "Otwarte")
        self.assertEqual(self.app.text_area(key="setup_source_text").value, "Materiał do zachowania")
        self.assertEqual(self.app.selectbox(key="setup_num_questions").value, 3)
        self.assertIsNone(self.app.session_state["quiz"])
        self.assertNotIn("private-key-and-source", self.app.error[0].value)
        self.app.run()
        self.open_text.assert_called_once()

    def test_open_pdf_error_preserves_upload(self):
        self.select_open()
        self.app.radio(key="setup_material_source").set_value("Prześlij PDF").run()
        self.open_pdf.side_effect = ValueError("Invalid response")
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.selectbox(key="setup_num_questions").select(2)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.file_uploader(key="setup_pdf").value.getvalue(), PDF_BYTES)
        self.assertEqual(self.app.radio(key="setup_question_type").value, "Otwarte")
        self.app.run()
        self.open_pdf.assert_called_once_with(PDF_BYTES, 2)

    def test_demo_remains_closed_after_selecting_open_questions(self):
        self.select_open()
        self.app.radio(key="setup_mode").set_value("Demo").run()
        self.app.selectbox(key="setup_num_questions").select(2)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertIsInstance(self.app.session_state["quiz"], Quiz)
        self.assertEqual(len(self.app.radio), 2)
        self.open_text.assert_not_called()
        self.open_pdf.assert_not_called()
        self.closed_text.assert_not_called()

    def test_restart_clears_widget_values_between_open_and_closed_quizzes(self):
        self.submit_open_text()
        self.app.text_area(key="open_quiz_answer_1").input("Stara odpowiedź").run()
        self.app.button[0].click().run()
        self.app.button[0].click().run()
        self.assertEqual(self.app.session_state["answers"], {})
        self.assertNotIn("open_quiz_answer_1", self.app.session_state)

        self.app.radio(key="setup_question_type").set_value("Zamknięte").run()
        self.app.text_area(key="setup_source_text").input("Materiał źródłowy")
        self.app.selectbox(key="setup_num_questions").select(1)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertIsInstance(self.app.session_state["quiz"], Quiz)
        self.assertIsNone(self.app.radio(key="quiz_answer_1").value)
        self.app.button[0].click().run()
        self.scoring.assert_called_once()
        self.assertEqual(len(self.app.metric), 3)
        self.app.button[0].click().run()

        self.submit_open_text()
        self.assertFalse(self.app.exception)
        self.assertTrue(all(area.value == "" for area in self.app.text_area))
        self.assertIsNone(self.app.session_state["results"])
        self.assertEqual(self.open_text.call_count, 2)
        self.closed_text.assert_called_once()
        self.scoring.assert_called_once()

    def test_unified_quiz_with_only_open_questions_uses_open_workflow(self):
        quiz = Quiz.model_validate(self.open_text.return_value.model_dump())
        self.assertIs(type(quiz), Quiz)
        self.app.session_state["quiz"] = quiz
        self.app.session_state["stage"] = "quiz"
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.text_area), 2)
        self.assertEqual(len(self.app.radio), 0)
        self.assertNotIn("REFERENCE_ONLY_1", self.rendered_text())
        self.app.text_area(key="open_quiz_answer_1").input("Moja odpowiedź").run()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertIn("REFERENCE_ONLY_1", self.rendered_text())
        self.assertEqual([metric.value for metric in self.app.metric], ["1 / 2", "50%"])
        self.grading.assert_called_once()
        self.open_text.assert_not_called()
        self.open_pdf.assert_not_called()
        self.scoring.assert_not_called()

    def test_unified_mixed_data_uses_both_widgets_and_only_closed_scoring(self):
        quiz = Quiz(title="Quiz", questions=[
            create_mock_quiz(1).questions[0],
            self.open_text.return_value.questions[1],
        ])
        self.app.session_state["quiz"] = quiz
        self.app.session_state["stage"] = "quiz"
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.radio), 1)
        self.assertEqual(len(self.app.text_area), 1)
        self.assertEqual(len(self.app.metric), 0)
        self.assertNotIn("REFERENCE_ONLY_2", self.rendered_text())
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["results"]["closed"]["total"], 1)
        self.assertEqual(len(self.app.metric), 4)
        self.assertEqual(self.app.metric[0].label, "Wynik części zamkniętej")
        self.assertEqual(self.app.metric[2].value, "0 / 2")
        self.grading.assert_called_once()
        self.assertIn("REFERENCE_ONLY_2", self.rendered_text())
        self.open_text.assert_not_called()
        self.open_pdf.assert_not_called()
        self.scoring.assert_called_once()


if __name__ == "__main__":
    unittest.main()
