import unittest

from pydantic import ValidationError

from app.schemas import OpenQuestion, OpenQuiz


def question_data(**overrides):
    return {
        "id": 1,
        "question": "Dlaczego rośliny potrzebują światła?",
        "reference_answer": "Światło dostarcza energii do fotosyntezy.",
        "explanation": "Podczas fotosyntezy rośliny wytwarzają cukry.",
        **overrides,
    }


class OpenQuestionTests(unittest.TestCase):
    def test_valid_question(self):
        question = OpenQuestion.model_validate(question_data())
        self.assertEqual(question.model_dump(), {**question_data(), "type": "open"})
        self.assertNotIn("options", question.model_dump())
        self.assertNotIn("correct_answer", question.model_dump())

    def test_strips_whitespace_from_all_text_fields(self):
        data = question_data()
        for field in ("question", "reference_answer", "explanation"):
            data[field] = f" \t{data[field]}\n"
        self.assertEqual(OpenQuestion.model_validate(data).model_dump(), {**question_data(), "type": "open"})

    def test_rejects_empty_or_whitespace_only_text(self):
        for field in ("question", "reference_answer", "explanation"):
            for value in ("", " \t\n"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        OpenQuestion.model_validate(question_data(**{field: value}))

    def test_rejects_invalid_ids(self):
        for value in (0, -1, 1.5, "invalid"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    OpenQuestion.model_validate(question_data(id=value))

    def test_requires_all_fields(self):
        for field in question_data():
            data = question_data()
            del data[field]
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    OpenQuestion.model_validate(data)


class OpenQuizTests(unittest.TestCase):
    def test_valid_quiz_with_unique_ids(self):
        quiz = OpenQuiz(title="Biologia", questions=[question_data(), question_data(id=3)])
        self.assertEqual([question.id for question in quiz.questions], [1, 3])
        self.assertTrue(all(isinstance(question, OpenQuestion) for question in quiz.questions))

    def test_accepts_question_objects_and_strips_title(self):
        question = OpenQuestion.model_validate(question_data())
        quiz = OpenQuiz(title=" \tBiologia\n", questions=[question])
        self.assertEqual(quiz.title, "Biologia")
        self.assertEqual(quiz.questions, [question])

    def test_rejects_empty_title_and_question_list(self):
        for title in ("", " \t\n"):
            with self.subTest(title=title):
                with self.assertRaises(ValidationError):
                    OpenQuiz(title=title, questions=[question_data()])
        with self.assertRaises(ValidationError):
            OpenQuiz(title="Biologia", questions=[])

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValidationError, "Question IDs must be unique"):
            OpenQuiz(title="Biologia", questions=[question_data(), question_data()])

    def test_rejects_invalid_nested_question(self):
        with self.assertRaises(ValidationError):
            OpenQuiz(title="Biologia", questions=[question_data(reference_answer=" ")])

    def test_requires_title_and_questions(self):
        for data in ({}, {"title": "Biologia"}, {"questions": [question_data()]}):
            with self.subTest(data=data):
                with self.assertRaises(ValidationError):
                    OpenQuiz.model_validate(data)


if __name__ == "__main__":
    unittest.main()
