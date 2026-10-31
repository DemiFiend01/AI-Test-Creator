from app.schemas import Quiz


def score_quiz(quiz: Quiz, answers: dict[int, int | None]) -> dict[str, int | float]:
    """Award one point per correct answer; missing answers receive zero."""
    total = len(quiz.questions)
    points = sum(
        answers.get(question.id) == question.correct_answer
        for question in quiz.questions
    )
    unanswered = sum(answers.get(question.id) is None for question in quiz.questions)
    return {
        "points": points,
        "total": total,
        "percentage": round(points / total * 100, 2),
        "unanswered": unanswered,
    }
