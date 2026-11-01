"""UI contract tests with fixed grades; these do not measure Gemini's accuracy."""

from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.grading_schemas import OpenAnswerGrade, OpenGradingResult
from app.grading_service import grade_open_answers as real_grade_open_answers
from app.mock_quiz import create_mock_quiz
from app.quiz_scoring import score_quiz
from app.schemas import MultipleChoiceQuestion, OpenQuestion, Quiz


APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"
PDF_BYTES = b"%PDF-1.7\nMock source\n%%EOF\n"


def open_question(question_id):
    return OpenQuestion(
        id=question_id,
        question=f"QUESTION_{question_id}",
        reference_answer=f"REFERENCE_{question_id}",
        explanation=f"EXPLANATION_{question_id}",
    )


def open_quiz():
    return Quiz(title="Quiz otwarty", questions=[open_question(7), open_question(23)])


def mixed_quiz():
    closed = [
        MultipleChoiceQuestion(
            id=question_id, question=f"QUESTION_{question_id}",
            options=["A", "B", "C", "D"], correct_answer=answer,
            explanation=f"EXPLANATION_{question_id}",
        ) for question_id, answer in ((12, 0), (34, 3), (56, 1))
    ]
    return Quiz(title="Quiz mieszany", questions=[
        open_question(7), closed[0], open_question(23), closed[1], closed[2],
    ])


def grading_result(*scores):
    return OpenGradingResult(grades=[
        OpenAnswerGrade(question_id=question_id, points=points, feedback=f"FEEDBACK_{question_id}")
        for question_id, points in scores
    ])


class StreamlitGradingTests(unittest.TestCase):
    def setUp(self):
        self.client = self.start_patch(
            "app.grading_service.genai.Client",
            side_effect=AssertionError("Real Gemini requests are disabled in tests"),
        )
        self.grading = self.start_patch(
            "app.grading_service.grade_open_answers",
            return_value=grading_result((7, 1.0), (23, 1.0)),
        )
        self.generators = {}
        for name in (
            "generate_quiz", "generate_quiz_from_pdf",
            "generate_open_quiz", "generate_open_quiz_from_pdf",
            "generate_mixed_quiz", "generate_mixed_quiz_from_pdf",
        ):
            self.generators[name] = self.start_patch(
                f"app.gemini_service.{name}",
                return_value=mixed_quiz() if "mixed" in name else open_quiz(),
            )
        self.scoring = self.start_patch("app.quiz_scoring.score_quiz", wraps=score_quiz)
        self.app = AppTest.from_file(str(APP_PATH)).run()
        self.assertFalse(self.app.exception)

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def tearDown(self):
        self.client.assert_not_called()

    def load_quiz(self, quiz):
        self.app.session_state["quiz"] = quiz
        self.app.session_state["answers"] = {}
        self.app.session_state["results"] = None
        self.app.session_state["stage"] = "quiz"
        self.app.run()
        self.assertFalse(self.app.exception)

    def click(self, label):
        buttons = [button for button in self.app.button if button.label == label]
        self.assertEqual(len(buttons), 1)
        buttons[0].click().run()
        self.assertFalse(self.app.exception)

    def submit(self):
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "results")

    def text(self):
        elements = (
            list(self.app.markdown) + list(self.app.caption) + list(self.app.info)
            + list(self.app.error) + list(self.app.warning) + list(self.app.success)
        )
        return "\n".join(str(element.value) for element in elements)

    def metrics(self):
        return {metric.label: metric.value for metric in self.app.metric}

    def test_no_grading_or_reference_answers_before_submission(self):
        self.load_quiz(open_quiz())
        self.app.text_area(key="open_quiz_answer_7").input("Odpowiedź").run()
        self.app.run()
        self.assertEqual(self.app.session_state["answers"], {7: "Odpowiedź", 23: ""})
        for marker in ("REFERENCE_7", "EXPLANATION_7", "FEEDBACK_7"):
            self.assertNotIn(marker, self.text())
        self.assertFalse(self.app.metric)
        self.grading.assert_not_called()
        for generator in self.generators.values():
            generator.assert_not_called()

    def test_open_all_correct_and_results_reruns_use_saved_grades(self):
        quiz = open_quiz()
        self.load_quiz(quiz)
        self.app.text_area(key="open_quiz_answer_7").input("Pierwsza").run()
        self.app.text_area(key="open_quiz_answer_23").input("Druga").run()
        self.submit()
        self.grading.assert_called_once_with(quiz.questions, {7: "Pierwsza", 23: "Druga"})
        self.assertEqual(self.metrics(), {"Łączny wynik": "2 / 2", "Wynik procentowy": "100%"})
        self.assertIn("Punkty (AI): 1 / 1 pkt", self.text())
        self.assertIn("FEEDBACK_7", self.text())
        self.assertIn("REFERENCE_7", self.text())
        self.assertIn("EXPLANATION_7", self.text())
        self.assertIn("niedokładne", self.text())
        self.assertIsNone(self.app.session_state["results"]["grading_error"])
        self.app.run()
        self.app.run()
        self.grading.assert_called_once()
        self.scoring.assert_not_called()
        self.assertNotIn("Ponów ocenianie", [button.label for button in self.app.button])

    def test_open_partial_grades_and_percentage_with_decimal_comma(self):
        quiz = Quiz(title="Trzy pytania", questions=[open_question(7), open_question(23), open_question(90)])
        self.grading.return_value = grading_result((7, 1.0), (23, 0.5), (90, 0.5))
        self.load_quiz(quiz)
        for area in self.app.text_area:
            area.input("Przykładowa odpowiedź")
        self.submit()
        self.assertEqual(self.metrics(), {"Łączny wynik": "2 / 3", "Wynik procentowy": "66,7%"})
        self.assertIn("Punkty (AI): 0,5 / 1 pkt", self.text())
        self.grading.assert_called_once()

    def test_wrong_and_blank_answers_display_zero_and_feedback(self):
        self.grading.return_value = grading_result((7, 0.0), (23, 0.0))
        self.load_quiz(open_quiz())
        self.app.text_area(key="open_quiz_answer_7").input("Błędna odpowiedź").run()
        self.submit()
        self.assertEqual(self.metrics(), {"Łączny wynik": "0 / 2", "Wynik procentowy": "0%"})
        self.assertIn("Nie udzielono odpowiedzi.", self.text())
        self.assertIn("Punkty (AI): 0 / 1 pkt", self.text())
        self.assertIn("FEEDBACK_23", self.text())
        self.grading.assert_called_once()

    def test_mixed_fractional_score_order_and_grade_ids(self):
        quiz = mixed_quiz()
        # Deliberately reversed: display must associate grades by ID, not list position.
        self.grading.return_value = grading_result((23, 0.5), (7, 1.0))
        self.load_quiz(quiz)
        self.app.text_area(key="mixed_quiz_answer_open_7").input("Pełna").run()
        self.app.text_area(key="mixed_quiz_answer_open_23").input("Częściowa").run()
        self.app.radio(key="mixed_quiz_answer_closed_12").set_value(0).run()
        self.app.radio(key="mixed_quiz_answer_closed_34").set_value(3).run()
        self.submit()
        self.assertEqual(self.metrics(), {
            "Wynik części zamkniętej": "2 / 3", "Wynik części otwartej": "1,5 / 2",
            "Łączny wynik": "3,5 / 5", "Wynik procentowy": "70%",
        })
        self.assertEqual(self.app.session_state["results"]["summary"], {
            "points": 3.5, "max_points": 5, "percentage": 70.0,
        })
        self.grading.assert_called_once_with(
            [quiz.questions[0], quiz.questions[2]], {7: "Pełna", 23: "Częściowa"},
        )
        self.scoring.assert_called_once()
        scored_quiz, answers = self.scoring.call_args.args
        self.assertEqual([question.id for question in scored_quiz.questions], [12, 34, 56])
        self.assertEqual(answers, {12: 0, 34: 3, 56: None})
        markdown = [element.value for element in self.app.markdown]
        headings = [value for value in markdown if value.startswith("**")]
        self.assertEqual(headings, [
            f"**{index}. {question.question}**" for index, question in enumerate(quiz.questions, 1)
        ])
        rendered = "\n".join(markdown)
        first = rendered.split(headings[0], 1)[1].split(headings[1], 1)[0]
        second = rendered.split(headings[2], 1)[1].split(headings[3], 1)[0]
        self.assertIn("FEEDBACK_7", first)
        self.assertIn("1 / 1 pkt", first)
        self.assertNotIn("FEEDBACK_23", first)
        self.assertIn("FEEDBACK_23", second)
        self.assertIn("0,5 / 1 pkt", second)
        self.assertIn("Punkty: 0 / 1 pkt", rendered)
        self.app.run()
        self.grading.assert_called_once()
        self.scoring.assert_called_once()

    def test_open_failure_preserves_answers_without_zero_or_total_score(self):
        self.grading.side_effect = RuntimeError("SECRET_INTERNAL_ERROR")
        quiz = open_quiz()
        self.load_quiz(quiz)
        self.app.text_area(key="open_quiz_answer_7").input("Zachowaj odpowiedź").run()
        self.app.text_area(key="open_quiz_answer_23").input("Drugą też").run()
        self.submit()
        self.assertEqual(self.app.session_state["quiz"], quiz)
        self.assertEqual(self.app.session_state["answers"], {7: "Zachowaj odpowiedź", 23: "Drugą też"})
        self.assertIsNone(self.app.session_state["results"]["open_grades"])
        self.assertIsNone(self.app.session_state["results"]["summary"])
        self.assertTrue(self.app.session_state["results"]["grading_error"])
        self.assertFalse(self.app.metric)
        self.assertIn("chwilowo niedostępne", self.app.error[0].value)
        self.assertNotIn("SECRET_INTERNAL_ERROR", self.text())
        self.assertNotIn("Punkty (AI):", self.text())
        self.assertIn("Oczekuje na ocenienie.", self.text())
        self.app.run()
        self.grading.assert_called_once()
        self.grading.side_effect = None
        self.click("Ponów ocenianie")
        self.assertEqual(self.grading.call_count, 2)
        self.assertEqual(self.metrics()["Łączny wynik"], "2 / 2")
        for generator in self.generators.values():
            generator.assert_not_called()

    def test_mixed_failure_and_retry_do_not_regenerate_or_rescore_closed(self):
        self.app.radio(key="setup_question_type").set_value("Mieszane").run()
        self.app.text_area(key="setup_source_text").input("Materiał do quizu")
        self.click("Rozpocznij quiz")
        self.grading.assert_not_called()
        self.app.text_area(key="mixed_quiz_answer_open_7").input("Odpowiedź pierwsza").run()
        self.app.text_area(key="mixed_quiz_answer_open_23").input("Odpowiedź druga").run()
        self.app.radio(key="mixed_quiz_answer_closed_12").set_value(0).run()
        self.grading.side_effect = ValueError("PRIVATE_INVALID_RESPONSE")
        self.submit()
        answers = dict(self.app.session_state["answers"])
        closed = dict(self.app.session_state["results"]["closed"])
        self.assertEqual(closed["points"], 1)
        self.assertEqual(self.metrics(), {"Wynik części zamkniętej": "1 / 3"})
        self.assertIsNone(self.app.session_state["results"]["summary"])
        self.assertNotIn("PRIVATE_INVALID_RESPONSE", self.text())
        self.app.run()
        self.grading.assert_called_once()
        self.grading.side_effect = None
        self.grading.return_value = grading_result((7, 0.5), (23, 1.0))
        self.click("Ponów ocenianie")
        self.assertEqual(self.app.session_state["answers"], answers)
        self.assertEqual(self.app.session_state["results"]["closed"], closed)
        self.assertIsNone(self.app.session_state["results"]["grading_error"])
        self.assertEqual(self.metrics(), {
            "Wynik części zamkniętej": "1 / 3", "Wynik części otwartej": "1,5 / 2",
            "Łączny wynik": "2,5 / 5", "Wynik procentowy": "50%",
        })
        self.assertEqual(self.grading.call_count, 2)
        self.assertEqual(self.grading.call_args_list[0], self.grading.call_args_list[1])
        self.scoring.assert_called_once()
        self.generators["generate_mixed_quiz"].assert_called_once_with("Materiał do quizu", 3, 2)
        self.app.run()
        self.assertEqual(self.grading.call_count, 2)
        self.generators["generate_mixed_quiz"].assert_called_once()

    def test_repeated_retry_failure_stays_safe_and_requires_another_click(self):
        self.grading.side_effect = RuntimeError("API failure")
        self.load_quiz(open_quiz())
        self.app.text_area(key="open_quiz_answer_7").input("Odpowiedź").run()
        self.submit()
        self.click("Ponów ocenianie")
        self.assertEqual(self.grading.call_count, 2)
        self.assertEqual(self.app.session_state["answers"][7], "Odpowiedź")
        self.assertIsNone(self.app.session_state["results"]["summary"])
        self.assertFalse(self.app.metric)
        self.app.run()
        self.assertEqual(self.grading.call_count, 2)

    def test_incomplete_or_wrong_grading_ids_do_not_produce_a_total(self):
        for grades in (grading_result((7, 1.0)), grading_result((7, 1.0), (999, 0.5))):
            with self.subTest(grades=grades):
                self.app = AppTest.from_file(str(APP_PATH)).run()
                self.grading.return_value = grades
                self.load_quiz(open_quiz())
                self.submit()
                self.assertIsNone(self.app.session_state["results"]["open_grades"])
                self.assertIsNone(self.app.session_state["results"]["summary"])
                self.assertFalse(self.app.metric)
                self.assertTrue(self.app.error)

    def test_reset_clears_successful_grades_and_old_answer_widgets(self):
        self.load_quiz(open_quiz())
        self.app.text_area(key="open_quiz_answer_7").input("Stara odpowiedź").run()
        self.submit()
        self.assertIsNotNone(self.app.session_state["results"]["open_grades"])
        self.click("Rozpocznij nowy quiz")
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertIsNone(self.app.session_state["quiz"])
        self.assertIsNone(self.app.session_state["results"])
        self.assertEqual(self.app.session_state["answers"], {})
        self.assertNotIn("open_quiz_answer_7", self.app.session_state)
        self.load_quiz(open_quiz())
        self.assertTrue(all(area.value == "" for area in self.app.text_area))
        self.assertIsNone(self.app.session_state["results"])
        self.assertNotIn("FEEDBACK_7", self.text())
        self.grading.assert_called_once()

    def test_reset_after_grading_failure_clears_error_without_retrying(self):
        self.grading.side_effect = RuntimeError("API failure")
        self.load_quiz(open_quiz())
        self.submit()
        self.assertTrue(self.app.session_state["results"]["grading_error"])
        self.click("Rozpocznij nowy quiz")
        self.assertIsNone(self.app.session_state["results"])
        self.assertEqual(self.app.session_state["answers"], {})
        self.assertFalse(self.app.error)
        self.grading.assert_called_once()

    def test_all_blank_open_answers_use_real_local_service_without_gemini(self):
        self.grading.side_effect = real_grade_open_answers
        dotenv = self.start_patch("app.grading_service.load_dotenv")
        self.load_quiz(open_quiz())
        self.app.text_area(key="open_quiz_answer_23").input(" \t\n").run()
        self.submit()
        self.grading.assert_called_once()
        self.client.assert_not_called()
        dotenv.assert_not_called()
        self.assertEqual(self.metrics(), {"Łączny wynik": "0 / 2", "Wynik procentowy": "0%"})
        self.assertEqual(self.app.session_state["answers"], {7: "", 23: " \t\n"})
        grades = self.app.session_state["results"]["open_grades"].grades
        self.assertEqual([(grade.question_id, grade.points) for grade in grades], [(7, 0.0), (23, 0.0)])
        self.assertTrue(all(grade.feedback == "Nie udzielono odpowiedzi." for grade in grades))

    def test_closed_only_results_keep_existing_score_without_grading(self):
        self.load_quiz(create_mock_quiz(2))
        question = self.app.session_state["quiz"].questions[0]
        self.app.radio(key=f"quiz_answer_{question.id}").set_value(question.correct_answer)
        self.submit()
        self.assertEqual(self.metrics(), {"Punkty": "1 / 2", "Wynik": "50%", "Bez odpowiedzi": "1"})
        self.assertEqual(self.app.session_state["results"], {
            "points": 1, "total": 2, "percentage": 50.0, "unanswered": 1,
        })
        self.scoring.assert_called_once()
        self.grading.assert_not_called()

    def test_mixed_blank_open_answers_use_local_grades_and_keep_closed_points(self):
        self.grading.side_effect = real_grade_open_answers
        self.load_quiz(mixed_quiz())
        self.app.radio(key="mixed_quiz_answer_closed_12").set_value(0).run()
        self.submit()
        self.assertEqual(self.metrics(), {
            "Wynik części zamkniętej": "1 / 3", "Wynik części otwartej": "0 / 2",
            "Łączny wynik": "1 / 5", "Wynik procentowy": "20%",
        })
        self.grading.assert_called_once()
        self.client.assert_not_called()
        self.app.run()
        self.grading.assert_called_once()
        self.scoring.assert_called_once()

    def test_open_pdf_generation_and_grading_are_separate_explicit_actions(self):
        self.app.radio(key="setup_question_type").set_value("Otwarte").run()
        self.app.radio(key="setup_material_source").set_value("Prześlij PDF").run()
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.run()
        self.generators["generate_open_quiz_from_pdf"].assert_not_called()
        self.grading.assert_not_called()
        self.app.selectbox(key="setup_num_questions").select(2)
        self.click("Rozpocznij quiz")
        self.grading.assert_not_called()
        self.app.text_area(key="open_quiz_answer_7").input("Odpowiedź 1").run()
        self.app.text_area(key="open_quiz_answer_23").input("Odpowiedź 2").run()
        self.submit()
        self.app.run()
        self.generators["generate_open_quiz_from_pdf"].assert_called_once_with(PDF_BYTES, 2)
        self.grading.assert_called_once_with(open_quiz().questions, {7: "Odpowiedź 1", 23: "Odpowiedź 2"})
        self.assertEqual(self.metrics()["Łączny wynik"], "2 / 2")


if __name__ == "__main__":
    unittest.main()
