import unittest

from pydantic import ValidationError

from app.grading_schemas import OpenAnswerGrade, OpenGradingResult


class OpenAnswerGradeTests(unittest.TestCase):
    def test_accepts_only_the_three_scores_and_strips_feedback(self):
        for score in (0.0, 0.5, 1.0, 0, 1):
            with self.subTest(score=score):
                grade = OpenAnswerGrade(question_id=1, points=score, feedback=" \tFeedback\n")
                self.assertEqual(grade.points, score)
                self.assertEqual(grade.feedback, "Feedback")

    def test_rejects_invalid_scores_and_boolean_or_string_coercion(self):
        for score in (0.25, 0.75, 2, -0.5, True, False, "1", "0.5", None, float("nan"), float("inf")):
            with self.subTest(score=score):
                with self.assertRaises(ValidationError):
                    OpenAnswerGrade(question_id=1, points=score, feedback="Feedback")

    def test_requires_positive_integer_id(self):
        for question_id in (0, -1, "1", 1.0, 1.5, True, None):
            with self.subTest(question_id=question_id):
                with self.assertRaises(ValidationError):
                    OpenAnswerGrade(question_id=question_id, points=0.5, feedback="Feedback")

    def test_requires_nonempty_feedback(self):
        for feedback in ("", " \t\n", None, 123):
            with self.subTest(feedback=feedback):
                with self.assertRaises(ValidationError):
                    OpenAnswerGrade(question_id=1, points=0.5, feedback=feedback)

    def test_requires_all_grade_fields(self):
        data = {"question_id": 1, "points": 1.0, "feedback": "Feedback"}
        for field in data:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    OpenAnswerGrade.model_validate({key: value for key, value in data.items() if key != field})


class OpenGradingResultTests(unittest.TestCase):
    def test_accepts_unique_grades_and_empty_result(self):
        result = OpenGradingResult(grades=[
            OpenAnswerGrade(question_id=1, points=1.0, feedback="Correct"),
            OpenAnswerGrade(question_id=3, points=0.5, feedback="Incomplete"),
        ])
        self.assertEqual([grade.question_id for grade in result.grades], [1, 3])
        self.assertEqual(OpenGradingResult(grades=[]).grades, [])

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValidationError, "unique"):
            OpenGradingResult(grades=[
                OpenAnswerGrade(question_id=1, points=1.0, feedback="Correct"),
                OpenAnswerGrade(question_id=1, points=0.0, feedback="Incorrect"),
            ])

    def test_rejects_missing_grades_and_unexpected_fields(self):
        with self.assertRaises(ValidationError):
            OpenGradingResult.model_validate({})
        with self.assertRaises(ValidationError):
            OpenGradingResult.model_validate({"grades": [], "total": 0})
        with self.assertRaises(ValidationError):
            OpenAnswerGrade(question_id=1, points=1.0, feedback="Correct", extra_score=1)


if __name__ == "__main__":
    unittest.main()
