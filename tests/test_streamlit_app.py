from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.mock_quiz import create_mock_quiz


APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"
PDF_BYTES = b"%PDF-1.7\nMock in-memory document\n%%EOF\n"


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

        pdf_generator = patch("app.gemini_service.generate_quiz_from_pdf", return_value=create_mock_quiz(2))
        self.generate_pdf = pdf_generator.start()
        self.addCleanup(pdf_generator.stop)

        demo = patch("app.mock_quiz.create_mock_quiz", wraps=create_mock_quiz)
        self.demo = demo.start()
        self.addCleanup(demo.stop)

        self.app = AppTest.from_file(str(APP_PATH)).run()
        self.assertFalse(self.app.exception)

    def submit_setup(self, text, count=5, mode="Gemini AI"):
        self.app.radio(key="setup_mode").set_value(mode).run()
        self.app.text_area(key="setup_source_text").input(text)
        self.app.selectbox(key="setup_num_questions").select(count)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)

    def select_pdf_source(self):
        self.app.radio(key="setup_material_source").set_value("Prześlij PDF").run()
        self.assertFalse(self.app.exception)

    def tearDown(self):
        self.client.assert_not_called()

    def test_initial_screen_defaults_to_gemini_without_generating(self):
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.radio(key="setup_mode").value, "Gemini AI")
        self.assertEqual(self.app.selectbox(key="setup_num_questions").options,
                         [str(count) for count in range(1, 11)])
        self.generate.assert_not_called()
        self.generate_pdf.assert_not_called()
        self.demo.assert_not_called()
        self.client.assert_not_called()

    def test_import_does_not_generate_or_create_client(self):
        runpy.run_path(str(APP_PATH), run_name="streamlit_import_test")
        self.generate.assert_not_called()
        self.generate_pdf.assert_not_called()
        self.demo.assert_not_called()
        self.client.assert_not_called()

    def test_widget_updates_and_reruns_do_not_generate(self):
        self.app.text_area(key="setup_source_text").input("Educational material")
        self.app.selectbox(key="setup_num_questions").select(3)
        self.app.radio(key="setup_mode").set_value("Demo")
        self.app.run()
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.generate.assert_not_called()
        self.generate_pdf.assert_not_called()
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
        self.generate_pdf.assert_not_called()
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

    def test_pdf_uploader_configuration_and_upload_do_not_generate(self):
        self.select_pdf_source()
        uploader = self.app.file_uploader(key="setup_pdf")
        self.assertFalse(uploader.accept_multiple_files)
        self.assertEqual(uploader.allowed_type, [".pdf"])
        self.assertEqual(uploader.proto.max_upload_size_mb, 10)
        self.assertEqual(uploader.proto.form_id, "")
        # AppTest commits the staged tuple on run(), without submitting the form.
        uploader.set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        uploaded = self.app.file_uploader(key="setup_pdf").value
        self.assertIsNotNone(uploaded)
        self.assertEqual(uploaded.name, "lesson.pdf")
        self.assertEqual(uploaded.size, len(PDF_BYTES))
        self.assertEqual(uploaded.getvalue(), PDF_BYTES)
        self.assertIn("**Plik:** lesson.pdf", [item.value for item in self.app.markdown])
        expected_size = f"Rozmiar: {len(PDF_BYTES) / (1024 * 1024):.2f} MB / 10 MB"
        self.assertIn(expected_size, [item.value for item in self.app.caption])
        self.generate_pdf.assert_not_called()
        self.generate.assert_not_called()
        self.demo.assert_not_called()

    def test_pdf_submission_uses_bytes_and_selected_count(self):
        self.select_pdf_source()
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.run()
        # A later rerun and form submission must reuse the committed upload.
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.file_uploader(key="setup_pdf").value.getvalue(), PDF_BYTES)
        self.generate_pdf.assert_not_called()
        self.generate.assert_not_called()
        self.app.selectbox(key="setup_num_questions").select(2)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.generate_pdf.assert_called_once_with(PDF_BYTES, 2)
        self.generate.assert_not_called()
        self.assertEqual(self.app.session_state["stage"], "quiz")
        self.assertEqual(self.app.session_state["quiz"], self.generate_pdf.return_value)
        self.assertNotIn("pdf_bytes", self.app.session_state)

    def test_pdf_is_required_before_generation(self):
        self.select_pdf_source()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertIn("Wybierz dokument PDF", self.app.error[0].value)
        self.generate_pdf.assert_not_called()

    def test_pdf_upload_validation_prevents_generation(self):
        self.select_pdf_source()
        for data in (b"", b"Not a PDF"):
            with self.subTest(data=data):
                self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", data, "application/pdf"))
                self.app.button[0].click().run()
                self.assertFalse(self.app.exception)
                self.assertEqual(self.app.session_state["stage"], "setup")
                self.assertTrue(self.app.error)
        self.generate_pdf.assert_not_called()
        self.generate.assert_not_called()

    def test_oversized_upload_prevents_generation(self):
        self.select_pdf_source()
        data = PDF_BYTES + b" " * (10 * 1024 * 1024 + 1 - len(PDF_BYTES))
        self.app.file_uploader(key="setup_pdf").set_value(("large.pdf", data, "application/pdf"))
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertIn("10 MB", self.app.error[0].value)
        self.generate_pdf.assert_not_called()

    def test_pdf_failure_preserves_upload_without_automatic_retry(self):
        self.select_pdf_source()
        self.generate_pdf.side_effect = RuntimeError("private document and API key")
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.selectbox(key="setup_num_questions").select(3)
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.assertEqual(self.app.selectbox(key="setup_num_questions").value, 3)
        self.assertEqual(self.app.radio(key="setup_material_source").value, "Prześlij PDF")
        self.assertEqual(self.app.file_uploader(key="setup_pdf").value.getvalue(), PDF_BYTES)
        self.assertNotIn("private document", self.app.error[0].value)
        self.assertNotIn("API key", self.app.error[0].value)
        self.app.run()
        self.generate_pdf.assert_called_once_with(PDF_BYTES, 3)
        self.generate.assert_not_called()

    def test_pdf_answers_results_and_restart_do_not_regenerate(self):
        self.select_pdf_source()
        self.app.file_uploader(key="setup_pdf").set_value(("lesson.pdf", PDF_BYTES, "application/pdf"))
        self.app.selectbox(key="setup_num_questions").select(2)
        self.app.button[0].click().run()
        correct_answer = self.generate_pdf.return_value.questions[0].correct_answer
        self.app.radio(key="quiz_answer_1").set_value(correct_answer)
        self.generate_pdf.assert_called_once()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "results")
        self.assertEqual(self.app.session_state["results"]["points"], 1)
        self.assertEqual(self.app.session_state["results"]["unanswered"], 1)
        self.app.run()
        self.app.button[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state["stage"], "setup")
        self.generate_pdf.assert_called_once_with(PDF_BYTES, 2)
        self.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
