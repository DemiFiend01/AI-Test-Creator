from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.quiz_scoring import score_quiz
from app.schemas import MultipleChoiceQuestion, OpenQuestion, Quiz


APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"
PDF_BYTES = b"%PDF-1.7\nMock in-memory source\n%%EOF\n"


def example_mixed_quiz(num_closed=3, num_open=2):
    questions = []
    for index in range(max(num_closed, num_open)):
        if index < num_open:
            question_id = (len(questions) + 1) * 10
            questions.append(OpenQuestion(
                id=question_id, question=f"OPEN_Q_{question_id}",
                reference_answer=f"REFERENCE_ONLY_{question_id}",
                explanation=f"EXPLANATION_ONLY_{question_id}",
            ))
        if index < num_closed:
            question_id = (len(questions) + 1) * 10
            questions.append(MultipleChoiceQuestion(
                id=question_id, question=f"CLOSED_Q_{question_id}",
                options=["A", "B", "C", "D"], correct_answer=0 if index % 2 == 0 else 3,
                explanation=f"EXPLANATION_ONLY_{question_id}",
            ))
    return Quiz(title="Quiz mieszany", questions=questions)


class MixedQuizStreamlitTests(unittest.TestCase):
    def setUp(self):
        client = patch(
            "app.gemini_service.genai.Client",
            side_effect=AssertionError("Real Gemini requests are disabled in tests"),
        )
        self.client = client.start()
        self.addCleanup(client.stop)
        text = patch("app.gemini_service.generate_mixed_quiz", return_value=example_mixed_quiz())
        self.mixed_text = text.start()
        self.addCleanup(text.stop)
        pdf = patch("app.gemini_service.generate_mixed_quiz_from_pdf", return_value=example_mixed_quiz())
        self.mixed_pdf = pdf.start()
        self.addCleanup(pdf.stop)
        self.single_generators = []
        for name in ("generate_quiz", "generate_quiz_from_pdf", "generate_open_quiz", "generate_open_quiz_from_pdf"):
            generator = patch(
                f"app.gemini_service.{name}",
                side_effect=AssertionError("Unexpected single-mode generation"),
            )
            self.single_generators.append(generator.start())
            self.addCleanup(generator.stop)
        scoring = patch("app.quiz_scoring.score_quiz", wraps=score_quiz)
        self.scoring = scoring.start()
        self.addCleanup(scoring.stop)
        self.app = AppTest.from_file(str(APP_PATH)).run()
        self.assertFalse(self.app.exception)

    def tearDown(self):
        self.client.assert_not_called()
        for generator in self.single_generators:
            generator.assert_not_called()

    def select_mixed(self):
        self.app.radio(key="setup_question_type").set_value("Mieszane").run()
        self.assertFalse(self.app.exception)

    def start_text_quiz(self, num_closed=3, num_open=2):
        self.select_mixed()
        self.app.number_input(key="setup_num_closed").set_value(num_closed).run()
        self.app.number_input(key="setup_num_open").set_value(num_open).run()
        self.app.text_area(key="setup_source_text").input("Materiał źródłowy")
        self.mixed_text.return_value = example_mixed_quiz(num_closed, num_open)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)

    def rendered_text(self):
        elements = list(self.app.markdown) + list(self.app.caption) + list(self.app.info)
        return "\n".join(str(element.value) for element in elements)

    def test_default_counts_and_live_total_do_not_generate(self):
        self.assertEqual(self.app.radio(key="setup_question_type").value, "Zamknięte")
        self.select_mixed()
        self.assertEqual(self.app.number_input(key="setup_num_closed").value, 3)
        self.assertEqual(self.app.number_input(key="setup_num_open").value, 2)
        self.assertIs(type(self.app.number_input(key="setup_num_closed").value), int)
        self.assertIs(type(self.app.number_input(key="setup_num_open").value), int)
        self.assertEqual(len(self.app.selectbox), 0)
        self.assertIn("Łącznie pytań: 5", self.rendered_text())
        self.app.number_input(key="setup_num_open").set_value(3).run()
        self.assertIn("Łącznie pytań: 6", self.rendered_text())
        self.mixed_text.assert_not_called()
        self.mixed_pdf.assert_not_called()

    def test_invalid_total_blocks_submission_and_preserves_single_mode_count(self):
        self.select_mixed()
        self.app.number_input(key="setup_num_closed").set_value(8).run()
        self.app.number_input(key="setup_num_open").set_value(3).run()
        self.assertIn("Łącznie pytań: 11", self.rendered_text())
        self.assertIn("od 2 do 10", self.app.error[0].value)
        self.app.text_area(key="setup_source_text").input("Materiał")
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.mixed_text.assert_not_called()
        self.mixed_pdf.assert_not_called()
        self.app.radio(key="setup_question_type").set_value("Zamknięte").run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox(key="setup_num_questions").value, 5)

    def test_count_reruns_preserve_source_material_without_generating(self):
        self.select_mixed()
        self.app.text_area(key="setup_source_text").input("Materiał do zachowania").run()
        self.app.number_input(key="setup_num_closed").set_value(4).run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.text_area(key="setup_source_text").value, "Materiał do zachowania")
        self.assertIn("Łącznie pytań: 6", self.rendered_text())
        self.mixed_text.assert_not_called()
        self.mixed_pdf.assert_not_called()

    def test_user_selected_counts_are_passed_only_after_submission(self):
        self.start_text_quiz(2, 1)
        self.mixed_text.assert_called_once_with("Materiał źródłowy", 2, 1)
        self.mixed_pdf.assert_not_called()
        self.assertEqual(self.app.session_state["stage"], "quiz")
        self.assertEqual(len(self.app.radio), 2)
        self.assertEqual(len(self.app.text_area), 1)

    def test_pdf_upload_and_count_changes_do_not_generate_until_submitting(self):
        self.select_mixed()
        self.app.radio(key="setup_material_source").set_value("Prześlij PDF").run()
        self.app.number_input(key="setup_num_closed").set_value(2).run()
        self.app.number_input(key="setup_num_open").set_value(1).run()
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.run()
        self.mixed_pdf.assert_not_called()
        self.mixed_text.assert_not_called()
        self.mixed_pdf.return_value = example_mixed_quiz(2, 1)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.mixed_pdf.assert_called_once_with(PDF_BYTES, 2, 1)
        self.assertEqual(self.app.session_state["stage"], "quiz")

    def test_question_order_widget_types_and_hidden_answers(self):
        self.start_text_quiz()
        quiz = self.app.session_state["quiz"]
        widgets = [node for node in self.app.main if node.type in ("radio", "text_area")]
        self.assertEqual([widget.label for widget in widgets], [
            f"{position}. {question.question}" for position, question in enumerate(quiz.questions, 1)
        ])
        self.assertEqual([widget.key for widget in widgets], [
            f"mixed_quiz_answer_{question.type}_{question.id}" for question in quiz.questions
        ])
        self.assertEqual([widget.type for widget in widgets], ["text_area", "radio", "text_area", "radio", "radio"])
        self.assertTrue(all(radio.value is None for radio in self.app.radio))
        self.assertTrue(all(area.value == "" for area in self.app.text_area))
        for question in quiz.questions:
            self.assertNotIn(question.explanation, self.rendered_text())
            if question.type == "open":
                self.assertNotIn(question.reference_answer, self.rendered_text())
        self.assertEqual(len(self.app.success), 0)
        self.assertEqual(len(self.app.metric), 0)

    def test_reruns_preserve_answers_and_only_closed_questions_are_scored(self):
        self.start_text_quiz()
        self.app.text_area(key="mixed_quiz_answer_open_10").input("Moja otwarta odpowiedź").run()
        self.app.radio(key="mixed_quiz_answer_closed_20").set_value(0).run()
        self.app.radio(key="mixed_quiz_answer_closed_40").set_value(0).run()
        self.app.run()
        self.assertEqual(self.app.text_area(key="mixed_quiz_answer_open_10").value, "Moja otwarta odpowiedź")
        self.assertEqual(self.app.radio(key="mixed_quiz_answer_closed_20").value, 0)
        self.assertEqual(self.app.session_state["answers"], {
            10: "Moja otwarta odpowiedź", 20: 0, 30: "", 40: 0, 50: None,
        })
        self.mixed_text.assert_called_once_with("Materiał źródłowy", 3, 2)
        self.scoring.assert_not_called()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "results")
        self.scoring.assert_called_once()
        scored_quiz, scored_answers = self.scoring.call_args.args
        self.assertEqual([question.id for question in scored_quiz.questions], [20, 40, 50])
        self.assertTrue(all(question.type == "closed" for question in scored_quiz.questions))
        self.assertEqual(scored_answers, {20: 0, 40: 0, 50: None})
        self.assertEqual(self.app.session_state["results"], {"closed": {
            "points": 1, "total": 3, "percentage": 33.33, "unanswered": 1,
        }})
        self.assertEqual(len(self.app.metric), 1)
        self.assertEqual(self.app.metric[0].label, "Wynik części zamkniętej")
        self.assertEqual(self.app.metric[0].value, "1 / 3")
        self.assertNotIn("%", self.app.metric[0].value)
        rendered = self.rendered_text()
        self.assertIn("Moja otwarta odpowiedź", rendered)
        self.assertIn("REFERENCE_ONLY_10", rendered)
        self.assertIn("REFERENCE_ONLY_30", rendered)
        self.assertIn("nie są jeszcze automatycznie oceniane", rendered)
        headings = [item.value for item in self.app.markdown if item.value.startswith("**")]
        self.assertEqual(headings, [
            f"**{position}. {question.question}**"
            for position, question in enumerate(self.app.session_state["quiz"].questions, 1)
        ])
        self.app.run()
        self.mixed_text.assert_called_once()
        self.mixed_pdf.assert_not_called()
        self.scoring.assert_called_once()

    def test_all_questions_may_be_unanswered(self):
        self.start_text_quiz()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["results"]["closed"], {
            "points": 0, "total": 3, "percentage": 0.0, "unanswered": 3,
        })
        self.assertEqual(self.app.metric[0].value, "0 / 3")
        self.assertIn("Nie udzielono odpowiedzi.", self.rendered_text())
        self.assertIn("REFERENCE_ONLY_30", self.rendered_text())
        self.mixed_text.assert_called_once()

    def test_restart_clears_answers_and_preserves_selected_counts(self):
        self.start_text_quiz(2, 1)
        self.app.text_area(key="mixed_quiz_answer_open_10").input("Stara odpowiedź").run()
        self.app.radio(key="mixed_quiz_answer_closed_20").set_value(0).run()
        self.app.button[0].click().run()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["answers"], {})
        self.assertIsNone(self.app.session_state["results"])
        self.assertNotIn("mixed_quiz_answer_open_10", self.app.session_state)
        self.assertEqual(self.app.number_input(key="setup_num_closed").value, 2)
        self.assertEqual(self.app.number_input(key="setup_num_open").value, 1)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(all(area.value == "" for area in self.app.text_area))
        self.assertTrue(all(radio.value is None for radio in self.app.radio))
        self.assertEqual(self.mixed_text.call_count, 2)

    def test_recoverable_error_preserves_source_and_counts_without_retry(self):
        self.mixed_text.side_effect = ValueError("private-source-and-key")
        self.start_text_quiz(4, 1)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.number_input(key="setup_num_closed").value, 4)
        self.assertEqual(self.app.number_input(key="setup_num_open").value, 1)
        self.assertEqual(self.app.text_area(key="setup_source_text").value, "Materiał źródłowy")
        self.assertNotIn("private-source-and-key", self.app.error[0].value)
        self.app.run()
        self.mixed_text.assert_called_once_with("Materiał źródłowy", 4, 1)

    def test_pdf_error_preserves_upload_and_counts_without_retry(self):
        self.select_mixed()
        self.app.radio(key="setup_material_source").set_value("Prześlij PDF").run()
        self.app.number_input(key="setup_num_closed").set_value(4).run()
        self.app.number_input(key="setup_num_open").set_value(1).run()
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.mixed_pdf.side_effect = RuntimeError("private failure")
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.file_uploader(key="setup_pdf").value.getvalue(), PDF_BYTES)
        self.assertEqual(self.app.number_input(key="setup_num_closed").value, 4)
        self.app.run()
        self.mixed_pdf.assert_called_once_with(PDF_BYTES, 4, 1)

    def test_demo_remains_closed_only(self):
        self.select_mixed()
        self.app.radio(key="setup_mode").set_value("Demo").run()
        self.assertEqual(len(self.app.number_input), 0)
        self.app.selectbox(key="setup_num_questions").select(2)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.radio), 2)
        self.assertEqual(len(self.app.text_area), 0)
        self.assertTrue(all(question.type == "closed" for question in self.app.session_state["quiz"].questions))
        self.mixed_text.assert_not_called()
        self.mixed_pdf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
