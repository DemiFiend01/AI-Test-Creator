import unittest

from app.quiz_scoring import score_quiz
from app.schemas import Question, Quiz


class QuizScoringTests(unittest.TestCase):
    def setUp(self):
        self.quiz = Quiz(
            title="Quiz testowy",
            questions=[
                Question(
                    id=question_id,
                    question=f"Pytanie {question_id}",
                    options=["A", "B", "C", "D"],
                    correct_answer=correct_answer,
                    explanation="Przykładowe wyjaśnienie.",
                )
                for question_id, correct_answer in ((2, 0), (5, 3), (9, 1))
            ],
        )

    def test_all_answers_correct(self):
        result = score_quiz(self.quiz, {2: 0, 5: 3, 9: 1})
        self.assertEqual(result, {
            "points": 3, "total": 3, "percentage": 100.0, "unanswered": 0,
        })

    def test_all_answers_wrong(self):
        result = score_quiz(self.quiz, {2: 1, 5: 0, 9: 2})
        self.assertEqual(result, {
            "points": 0, "total": 3, "percentage": 0.0, "unanswered": 0,
        })

    def test_mixed_answers_and_rounded_percentage(self):
        result = score_quiz(self.quiz, {2: 0, 5: 1, 9: 2})
        self.assertEqual(result["points"], 1)
        self.assertEqual(result["percentage"], 33.33)

    def test_no_answers(self):
        result = score_quiz(self.quiz, {})
        self.assertEqual(result, {
            "points": 0, "total": 3, "percentage": 0.0, "unanswered": 3,
        })

    def test_none_answers_receive_zero_points(self):
        result = score_quiz(self.quiz, {2: None, 5: None, 9: None})
        self.assertEqual(result["points"], 0)
        self.assertEqual(result["unanswered"], 3)

    def test_partial_answers_use_question_ids(self):
        result = score_quiz(self.quiz, {5: 3, 9: None})
        self.assertEqual(result["points"], 1)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["unanswered"], 2)

    def test_unknown_question_ids_do_not_affect_score(self):
        result = score_quiz(self.quiz, {2: 0, 5: 3, 9: 1, 100: 0})
        self.assertEqual(result["points"], 3)
        self.assertEqual(result["total"], 3)

    def test_scoring_does_not_change_inputs(self):
        answers = {2: 0, 5: None}
        original_quiz = self.quiz.model_dump()
        score_quiz(self.quiz, answers)
        self.assertEqual(answers, {2: 0, 5: None})
        self.assertEqual(self.quiz.model_dump(), original_quiz)


if __name__ == "__main__":
    unittest.main()
