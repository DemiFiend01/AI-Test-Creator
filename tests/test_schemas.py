import unittest

from pydantic import ValidationError

from app.schemas import Question, Quiz


def question_data(**overrides):
    return {
        "id": 1,
        "question": "What is 2 + 2?",
        "options": ["3", "4", "5", "6"],
        "correct_answer": 1,
        "explanation": "Adding 2 and 2 gives 4.",
        **overrides,
    }


class QuestionTests(unittest.TestCase):
    def test_valid_question(self):
        question = Question.model_validate(question_data())
        self.assertEqual(question.model_dump(), question_data())

    def test_strips_whitespace_from_all_text(self):
        question = Question.model_validate(question_data(
            question=" \tWhat is 2 + 2?\n",
            options=[" 3 ", "\t4\n", " 5", "6 "],
            explanation="\nAdding 2 and 2 gives 4.\t ",
        ))
        self.assertEqual(question.model_dump(), question_data())

    def test_rejects_empty_question_and_explanation(self):
        for field in ("question", "explanation"):
            for value in ("", " \t\n"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        Question.model_validate(question_data(**{field: value}))

    def test_rejects_empty_options(self):
        for index in range(4):
            for value in ("", " \t\n"):
                options = ["3", "4", "5", "6"]
                options[index] = value
                with self.subTest(index=index, value=value):
                    with self.assertRaises(ValidationError):
                        Question.model_validate(question_data(options=options))

    def test_requires_exactly_four_options(self):
        for count in (0, 1, 3, 5):
            with self.subTest(count=count):
                with self.assertRaises(ValidationError):
                    Question.model_validate(question_data(options=["A"] * count))

    def test_rejects_invalid_ids(self):
        for value in (0, -1, 1.5, "invalid"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    Question.model_validate(question_data(id=value))

    def test_accepts_answer_index_boundaries(self):
        for index in (0, 3):
            with self.subTest(index=index):
                question = Question.model_validate(question_data(correct_answer=index))
                self.assertEqual(question.correct_answer, index)

    def test_rejects_invalid_answer_indices(self):
        for index in (-1, 4, 1.5, "invalid"):
            with self.subTest(index=index):
                with self.assertRaises(ValidationError):
                    Question.model_validate(question_data(correct_answer=index))

    def test_requires_all_fields(self):
        for field in question_data():
            data = question_data()
            del data[field]
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Question.model_validate(data)


class QuizTests(unittest.TestCase):
    def test_valid_quiz_with_unique_ids(self):
        quiz = Quiz.model_validate({
            "title": "Math quiz",
            "questions": [question_data(), question_data(id=3)],
        })
        self.assertEqual(quiz.title, "Math quiz")
        self.assertEqual([question.id for question in quiz.questions], [1, 3])
        self.assertTrue(all(isinstance(question, Question) for question in quiz.questions))

    def test_accepts_question_objects(self):
        question = Question.model_validate(question_data())
        quiz = Quiz(title="Math quiz", questions=[question])
        self.assertEqual(quiz.questions, [question])

    def test_strips_whitespace_from_title(self):
        quiz = Quiz(title=" \tMath quiz\n", questions=[question_data()])
        self.assertEqual(quiz.title, "Math quiz")

    def test_rejects_empty_title(self):
        for title in ("", " \t\n"):
            with self.subTest(title=title):
                with self.assertRaises(ValidationError):
                    Quiz(title=title, questions=[question_data()])

    def test_rejects_empty_questions(self):
        with self.assertRaises(ValidationError):
            Quiz(title="Math quiz", questions=[])

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValidationError, "Question IDs must be unique"):
            Quiz(title="Math quiz", questions=[
                question_data(),
                question_data(question="A different question with the same ID?"),
            ])

    def test_rejects_invalid_nested_question(self):
        with self.assertRaises(ValidationError):
            Quiz(title="Math quiz", questions=[question_data(correct_answer=4)])

    def test_requires_all_fields(self):
        data = {"title": "Math quiz", "questions": [question_data()]}
        for field in data:
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    Quiz.model_validate({key: value for key, value in data.items() if key != field})


if __name__ == "__main__":
    unittest.main()
