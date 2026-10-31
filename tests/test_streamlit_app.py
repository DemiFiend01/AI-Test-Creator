from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.mock_quiz import create_mock_quiz


APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class StreamlitIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Guard the SDK as well as mocking generation: no real request is allowed.
        client = patch(
            "app.gemini_service.genai.Client",
            side_effect=AssertionError("Real Gemini requests are disabled in tests"),
        )
        self.client = client.start()
        self.addCleanup(client.stop)

        generator = patch("app.gemini_service.generate_quiz", return_value=create_mock_quiz(5))
        self.generate = generator.start()
        self.addCleanup(generator.stop)

        demo = patch("app.mock_quiz.create_mock_quiz", wraps=create_mock_quiz)
        self.demo = demo.start()
        self.addCleanup(demo.stop)

        self.app = AppTest.from_file(str(APP_PATH)).run()
        self.assertFalse(self.app.exception)

    def submit_setup(self, text, count=5, mode="Gemini AI"):
        self.app.radio(key="setup_mode").set_value(mode)
        self.app.text_area(key="setup_source_text").input(text)
        self.app.selectbox(key="setup_num_questions").select(count)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)

    def test_initial_screen_defaults_to_gemini_without_generating(self):
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.radio(key="setup_mode").value, "Gemini AI")
        self.assertEqual(self.app.selectbox(key="setup_num_questions").options,
                         [str(count) for count in range(1, 11)])
        self.generate.assert_not_called()
        self.demo.assert_not_called()
        self.client.assert_not_called()

    def test_import_does_not_generate_or_create_client(self):
        runpy.run_path(str(APP_PATH), run_name="streamlit_import_test")
        self.generate.assert_not_called()
        self.demo.assert_not_called()
        self.client.assert_not_called()

    def test_widget_updates_and_reruns_do_not_generate(self):
        self.app.text_area(key="setup_source_text").input("Educational material")
        self.app.selectbox(key="setup_num_questions").select(3)
        self.app.radio(key="setup_mode").set_value("Demo")
        self.app.run()
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.generate.assert_not_called()
        self.demo.assert_not_called()
        self.client.assert_not_called()

    def test_gemini_submission_stores_quiz_and_advances(self):
        quiz = create_mock_quiz(3)
        self.generate.return_value = quiz
        self.submit_setup("  Educational material  ", count=3)
        self.generate.assert_called_once_with("Educational material", 3)
        self.demo.assert_not_called()
        self.assertEqual(self.app.session_state["stage"], "quiz")
        self.assertEqual(self.app.session_state["quiz"], quiz)
        self.assertEqual(len(self.app.radio), 3)
        self.assertTrue(all(radio.value is None for radio in self.app.radio))
        self.assertEqual(self.app.session_state["answers"], {})
        self.assertIsNone(self.app.session_state["results"])
        self.client.assert_not_called()

    def test_gemini_rejects_blank_source_without_generating(self):
        for text in ("", " \t\n"):
            with self.subTest(text=text):
                self.submit_setup(text)
                self.assertEqual(self.app.session_state["stage"], "setup")
                self.assertIn("niepusty materiał", self.app.error[0].value)
                self.assertIsNone(self.app.session_state["quiz"])
        self.generate.assert_not_called()
        self.client.assert_not_called()

    def test_demo_accepts_empty_source_without_gemini(self):
        self.submit_setup("", count=2, mode="Demo")
        self.demo.assert_called_once_with(2)
        self.generate.assert_not_called()
        self.client.assert_not_called()
        self.assertEqual(self.app.session_state["stage"], "quiz")
        self.assertEqual(len(self.app.session_state["quiz"].questions), 2)
        self.assertIn("gotowych pytań niezależnych", self.app.info[0].value)

    def test_generation_errors_preserve_form_and_hide_details(self):
        text = "  Source material that should be preserved.  "
        for error_type in (RuntimeError, ValueError):
            with self.subTest(error_type=error_type):
                self.generate.side_effect = error_type("private-api-key-and-configuration")
                self.submit_setup(text, count=3)
                self.assertEqual(self.app.session_state["stage"], "setup")
                self.assertEqual(self.app.session_state["source_text"], text)
                self.assertEqual(self.app.text_area(key="setup_source_text").value, text)
                self.assertEqual(self.app.radio(key="setup_mode").value, "Gemini AI")
                self.assertEqual(self.app.selectbox(key="setup_num_questions").value, 3)
                self.assertIsNone(self.app.session_state["quiz"])
                self.assertEqual(self.app.session_state["answers"], {})
                self.assertIsNone(self.app.session_state["results"])
                self.assertEqual(len(self.app.error), 1)
                self.assertIn("Nie udało się wygenerować quizu", self.app.error[0].value)
                self.assertNotIn("private-api-key", self.app.error[0].value)
        self.demo.assert_not_called()
        self.client.assert_not_called()

    def test_retry_after_error_succeeds(self):
        self.generate.side_effect = RuntimeError("Failed request")
        self.submit_setup("Source material", count=2)
        self.generate.side_effect = None
        self.generate.return_value = create_mock_quiz(2)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "quiz")
        self.assertEqual(self.generate.call_count, 2)
        self.assertFalse(self.app.error)
        self.client.assert_not_called()

    def test_answers_results_and_restart_do_not_regenerate(self):
        quiz = create_mock_quiz(2)
        self.generate.return_value = quiz
        self.submit_setup("Source material", count=2)
        self.app.radio(key="quiz_answer_1").set_value(
            quiz.questions[0].correct_answer
        )
        self.generate.assert_called_once()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "results")
        self.assertEqual(self.app.session_state["results"], {
            "points": 1, "total": 2, "percentage": 50.0, "unanswered": 1,
        })
        self.app.run()
        self.generate.assert_called_once()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertIsNone(self.app.session_state["quiz"])
        self.assertEqual(self.app.session_state["answers"], {})
        self.assertIsNone(self.app.session_state["results"])
        self.assertEqual(self.app.text_area(key="setup_source_text").value, "Source material")
        self.assertEqual(self.app.selectbox(key="setup_num_questions").value, 2)
        self.generate.assert_called_once()
        self.client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
