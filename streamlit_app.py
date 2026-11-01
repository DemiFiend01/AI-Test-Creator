import streamlit as st

from app.gemini_service import (
    MAX_PDF_BYTES,
    generate_mixed_quiz,
    generate_mixed_quiz_from_pdf,
    generate_open_quiz,
    generate_open_quiz_from_pdf,
    generate_quiz,
    generate_quiz_from_pdf,
)
from app.grading_schemas import OpenGradingResult
from app.grading_service import grade_open_answers
from app.mock_quiz import create_mock_quiz
from app.quiz_scoring import score_quiz
from app.schemas import ClosedQuiz, OpenQuestion


def _clear_answer_widgets():
    for key in list(st.session_state):
        if key.startswith(("quiz_answer_", "open_quiz_answer_", "mixed_quiz_answer_")):
            del st.session_state[key]


def reset_quiz():
    _clear_answer_widgets()
    st.session_state.stage = "setup"
    st.session_state.quiz = None
    st.session_state.answers = {}
    st.session_state.results = None


def _source_text_input(value):
    return st.text_area(
        "Materiał do nauki",
        value=value,
        height=220,
        placeholder="Wklej notatki lub fragment materiału edukacyjnego…",
        help="Gemini AI wymaga materiału źródłowego. W trybie Demo tekst jest opcjonalny.",
        key="setup_source_text",
    )


def show_setup():
    st.subheader("1. Przygotuj quiz")
    modes = ("Gemini AI", "Demo")
    mode = st.radio(
        "Tryb quizu",
        options=modes,
        index=modes.index(st.session_state.get("generation_mode", "Gemini AI")),
        key="setup_mode",
        horizontal=True,
    )
    material_source = "Wklej tekst"
    question_type = "Zamknięte"
    if mode == "Gemini AI":
        question_types = ("Zamknięte", "Otwarte", "Mieszane")
        question_type = st.radio(
            "Rodzaj pytań",
            options=question_types,
            index=question_types.index(st.session_state.get("question_type", "Zamknięte")),
            key="setup_question_type",
            horizontal=True,
        )
        sources = ("Wklej tekst", "Prześlij PDF")
        material_source = st.radio(
            "Źródło materiału",
            options=sources,
            index=sources.index(st.session_state.get("material_source", "Wklej tekst")),
            key="setup_material_source",
            horizontal=True,
        )

    mixed_counts_valid = True
    if question_type == "Mieszane":
        num_closed = st.number_input(
            "Liczba pytań zamkniętych", min_value=1, max_value=10, step=1,
            value=st.session_state.get("num_closed", 3), key="setup_num_closed",
        )
        num_open = st.number_input(
            "Liczba pytań otwartych", min_value=1, max_value=10, step=1,
            value=st.session_state.get("num_open", 2), key="setup_num_open",
        )
        num_questions = num_closed + num_open
        st.caption(f"Łącznie pytań: {num_questions}")
        mixed_counts_valid = 2 <= num_questions <= 10
        if not mixed_counts_valid:
            st.error("Łączna liczba pytań musi wynosić od 2 do 10.")

    source_text = st.session_state.get("source_text", "")
    uploaded_pdf = None
    if mode == "Gemini AI" and material_source == "Prześlij PDF":
        # Uploads rerun immediately so file details appear before generation.
        uploaded_pdf = st.file_uploader(
            "Dokument PDF (maksymalnie 10 MB)",
            type=["pdf"],
            accept_multiple_files=False,
            max_upload_size=10,
            key="setup_pdf",
        )
        if uploaded_pdf is not None:
            st.markdown(f"**Plik:** {uploaded_pdf.name}")
            st.caption(f"Rozmiar: {uploaded_pdf.size / (1024 * 1024):.2f} MB / 10 MB")
    elif question_type == "Mieszane":
        source_text = _source_text_input(source_text)

    with st.form("setup_form"):
        if question_type != "Mieszane" and (mode != "Gemini AI" or material_source != "Prześlij PDF"):
            source_text = _source_text_input(source_text)
        if question_type != "Mieszane":
            num_questions = st.selectbox(
                "Liczba pytań",
                options=range(1, 11),
                index=st.session_state.get("num_questions", 5) - 1,
                key="setup_num_questions",
            )
        submitted = st.form_submit_button("Rozpocznij quiz", type="primary")

    if submitted:
        st.session_state.source_text = source_text
        st.session_state.generation_mode = mode
        st.session_state.question_type = question_type
        st.session_state.material_source = material_source
        if question_type == "Mieszane":
            st.session_state.num_closed = num_closed
            st.session_state.num_open = num_open
            if not mixed_counts_valid:
                return
        else:
            st.session_state.num_questions = num_questions
        if mode == "Gemini AI":
            if material_source == "Prześlij PDF":
                if uploaded_pdf is None:
                    st.error("Wybierz dokument PDF przed rozpoczęciem quizu.")
                    return
                if uploaded_pdf.size > MAX_PDF_BYTES:
                    st.error("Plik PDF nie może przekraczać 10 MB.")
                    return
                pdf_bytes = uploaded_pdf.getvalue()
                if len(pdf_bytes) > MAX_PDF_BYTES:
                    st.error("Plik PDF nie może przekraczać 10 MB.")
                    return
                if (
                    not uploaded_pdf.name.lower().endswith(".pdf")
                    or not pdf_bytes.startswith(b"%PDF-")
                ):
                    st.error("Plik jest pusty lub nie ma prawidłowego nagłówka PDF.")
                    return
            elif not source_text.strip():
                st.error("W trybie Gemini AI wprowadź niepusty materiał źródłowy.")
                return
            try:
                with st.spinner("Gemini AI przygotowuje pytania…"):
                    if question_type == "Mieszane":
                        if material_source == "Prześlij PDF":
                            quiz = generate_mixed_quiz_from_pdf(pdf_bytes, num_closed, num_open)
                        else:
                            quiz = generate_mixed_quiz(source_text.strip(), num_closed, num_open)
                    elif question_type == "Otwarte":
                        if material_source == "Prześlij PDF":
                            quiz = generate_open_quiz_from_pdf(pdf_bytes, num_questions)
                        else:
                            quiz = generate_open_quiz(source_text.strip(), num_questions)
                    elif material_source == "Prześlij PDF":
                        quiz = generate_quiz_from_pdf(pdf_bytes, num_questions)
                    else:
                        quiz = generate_quiz(source_text.strip(), num_questions)
            except Exception:
                st.error(
                    "Nie udało się wygenerować quizu. Spróbuj ponownie lub wybierz Demo. "
                    "Jeśli problem się powtarza, sprawdź połączenie i konfigurację Gemini."
                )
                return
        else:
            quiz = create_mock_quiz(num_questions)

        _clear_answer_widgets()
        st.session_state.quiz = quiz
        st.session_state.answers = {}
        st.session_state.results = None
        st.session_state.stage = "quiz"
        st.rerun()


def show_quiz():
    quiz = st.session_state.quiz
    question_types = {question.type for question in quiz.questions}
    if question_types == {"open"}:
        show_open_quiz()
        return
    if question_types == {"closed", "open"}:
        show_mixed_quiz()
        return
    st.subheader("2. Rozwiąż quiz")
    st.write(quiz.title)
    st.caption("Wybierz jedną odpowiedź na każde pytanie. Brak odpowiedzi oznacza 0 punktów.")

    answers = {}
    with st.form("quiz_form"):
        for position, question in enumerate(quiz.questions, start=1):
            answers[question.id] = st.radio(
                f"{position}. {question.question}",
                options=range(4),
                index=None,
                format_func=lambda index, options=question.options: (
                    f"{'ABCD'[index]}. {options[index]}"
                ),
                key=f"quiz_answer_{question.id}",
            )
        submitted = st.form_submit_button("Zakończ quiz i sprawdź wynik", type="primary")

    if submitted:
        st.session_state.answers = answers
        st.session_state.results = score_quiz(quiz, answers)
        st.session_state.stage = "results"
        st.rerun()


def show_results():
    quiz = st.session_state.quiz
    question_types = {question.type for question in quiz.questions}
    if question_types == {"open"}:
        show_open_results()
        return
    if question_types == {"closed", "open"}:
        show_mixed_results()
        return
    results = st.session_state.results
    st.subheader("3. Twoje wyniki")
    points, percentage, unanswered = st.columns(3)
    points.metric("Punkty", f"{results['points']} / {results['total']}")
    percentage.metric("Wynik", f"{results['percentage']:g}%".replace(".", ","))
    unanswered.metric("Bez odpowiedzi", results["unanswered"])

    for position, question in enumerate(quiz.questions, start=1):
        st.divider()
        st.markdown(f"**{position}. {question.question}**")
        answer = st.session_state.answers.get(question.id)
        if answer is None:
            st.warning("Nie udzielono odpowiedzi — 0 punktów.")
        else:
            st.write("Twoja odpowiedź:", question.options[answer])
            if answer == question.correct_answer:
                st.success("Poprawna odpowiedź — 1 punkt.")
            else:
                st.error("Niepoprawna odpowiedź — 0 punktów.")
        st.write("Poprawna odpowiedź:", question.options[question.correct_answer])
        st.write("Wyjaśnienie:", question.explanation)

    st.button("Rozpocznij nowy quiz", on_click=reset_quiz, type="primary")


def show_open_quiz():
    quiz = st.session_state.quiz
    st.subheader("2. Odpowiedz na pytania otwarte")
    st.write(quiz.title)
    st.caption("Odpowiedz własnymi słowami. Możesz pozostawić pytania bez odpowiedzi.")
    answers = {}
    for position, question in enumerate(quiz.questions, start=1):
        answers[question.id] = st.text_area(
            f"{position}. {question.question}",
            height=140,
            key=f"open_quiz_answer_{question.id}",
        )
    # Keep answers on every widget rerun; grade only after explicit submission.
    st.session_state.answers = answers
    if st.button("Zakończ quiz i oceń odpowiedzi", type="primary"):
        _submit_for_open_grading()


def show_open_results():
    quiz = st.session_state.quiz
    st.subheader("3. Wyniki quizu otwartego")
    _show_ai_grading_summary()
    grades = _open_grades_by_id()
    for position, question in enumerate(quiz.questions, start=1):
        st.divider()
        st.markdown(f"**{position}. {question.question}**")
        _show_open_answer_result(question, grades.get(question.id))
    st.button("Rozpocznij nowy quiz", on_click=reset_quiz, type="primary")


def show_mixed_quiz():
    quiz = st.session_state.quiz
    st.subheader("2. Rozwiąż quiz mieszany")
    st.write(quiz.title)
    st.caption(
        "Możesz pozostawić pytania bez odpowiedzi. "
        "Po oddaniu quizu odpowiedzi otwarte zostaną ocenione z pomocą AI."
    )
    answers = {}
    for position, question in enumerate(quiz.questions, start=1):
        label = f"{position}. {question.question}"
        key = f"mixed_quiz_answer_{question.type}_{question.id}"
        if question.type == "closed":
            answers[question.id] = st.radio(
                label, options=range(4), index=None, key=key,
                format_func=lambda index, options=question.options: (
                    f"{'ABCD'[index]}. {options[index]}"
                ),
            )
        else:
            answers[question.id] = st.text_area(label, height=140, key=key)
    st.session_state.answers = answers
    if st.button("Zakończ quiz i sprawdź odpowiedzi", type="primary"):
        _submit_for_open_grading()


def show_mixed_results():
    quiz = st.session_state.quiz
    st.subheader("3. Wyniki quizu mieszanego")
    _show_ai_grading_summary()
    grades = _open_grades_by_id()
    for position, question in enumerate(quiz.questions, start=1):
        st.divider()
        st.markdown(f"**{position}. {question.question}**")
        answer = st.session_state.answers.get(question.id)
        if question.type == "closed":
            if answer is None:
                st.warning("Nie udzielono odpowiedzi — 0 punktów.")
            else:
                st.write("Twoja odpowiedź:", question.options[answer])
                if answer == question.correct_answer:
                    st.success("Poprawna odpowiedź — 1 punkt.")
                else:
                    st.error("Niepoprawna odpowiedź — 0 punktów.")
            points = 1 if answer == question.correct_answer else 0
            st.write("Punkty:", f"{points} / 1 pkt")
            st.write("Poprawna odpowiedź:", question.options[question.correct_answer])
            st.write("Wyjaśnienie:", question.explanation)
        else:
            _show_open_answer_result(question, grades.get(question.id))
    st.button("Rozpocznij nowy quiz", on_click=reset_quiz, type="primary")


def _format_points(value):
    return f"{value:g}".replace(".", ",")


def _format_percentage(value):
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def _submit_for_open_grading():
    quiz = st.session_state.quiz
    results = {"open_grades": None, "grading_error": None, "summary": None}
    closed_questions = [question for question in quiz.questions if question.type == "closed"]
    if closed_questions:
        closed_quiz = ClosedQuiz(title=quiz.title, questions=closed_questions)
        closed_answers = {
            question.id: st.session_state.answers.get(question.id) for question in closed_questions
        }
        results["closed"] = score_quiz(closed_quiz, closed_answers)
    st.session_state.results = results
    st.session_state.stage = "results"
    _grade_submitted_open_answers()
    st.rerun()


def _grade_submitted_open_answers():
    results = st.session_state.results
    if results["open_grades"] is not None:
        return
    quiz = st.session_state.quiz
    questions = [question for question in quiz.questions if isinstance(question, OpenQuestion)]
    answers = {question.id: st.session_state.answers.get(question.id, "") for question in questions}
    try:
        with st.spinner("AI ocenia odpowiedzi otwarte…"):
            open_grades = grade_open_answers(questions, answers)
        if (
            not isinstance(open_grades, OpenGradingResult)
            or len(open_grades.grades) != len(questions)
            or {grade.question_id for grade in open_grades.grades} != {question.id for question in questions}
        ):
            raise ValueError("Incomplete grading result.")
        closed_points = results.get("closed", {}).get("points", 0)
        total_points = closed_points + sum(grade.points for grade in open_grades.grades)
        summary = {
            "points": total_points,
            "max_points": len(quiz.questions),
            "percentage": total_points / len(quiz.questions) * 100,
        }
    except Exception:
        results["open_grades"] = None
        results["summary"] = None
        results["grading_error"] = (
            "Ocenianie odpowiedzi otwartych jest chwilowo niedostępne. "
            "Twoje odpowiedzi zostały zachowane. Spróbuj ponownie."
        )
    else:
        results["open_grades"] = open_grades
        results["summary"] = summary
        results["grading_error"] = None
    st.session_state.results = results


def _show_ai_grading_summary():
    results = st.session_state.results
    st.info("Odpowiedzi otwarte są oceniane z pomocą AI. Takie oceny mogą czasem być niedokładne.")
    if "closed" in results:
        closed = results["closed"]
        closed_column, open_column = st.columns(2)
        closed_column.metric("Wynik części zamkniętej", f"{closed['points']} / {closed['total']}")
        if results["open_grades"] is not None:
            open_points = sum(grade.points for grade in results["open_grades"].grades)
            open_column.metric(
                "Wynik części otwartej",
                f"{_format_points(open_points)} / {len(results['open_grades'].grades)}",
            )
        else:
            open_column.caption("Część otwarta oczekuje na ocenienie.")
    if results["open_grades"] is not None and results["summary"] is not None:
        summary = results["summary"]
        total, percentage = st.columns(2)
        total.metric("Łączny wynik", f"{_format_points(summary['points'])} / {summary['max_points']}")
        percentage.metric("Wynik procentowy", _format_percentage(summary["percentage"]))
    else:
        st.error(results["grading_error"] or "Ocenianie odpowiedzi otwartych jest chwilowo niedostępne.")
        if st.button("Ponów ocenianie", key="retry_open_grading", type="primary"):
            _grade_submitted_open_answers()
            st.rerun()


def _open_grades_by_id():
    result = st.session_state.results["open_grades"]
    return {grade.question_id: grade for grade in result.grades} if result is not None else {}


def _show_open_answer_result(question, grade):
    answer = st.session_state.answers.get(question.id, "")
    st.write("Twoja odpowiedź:", answer if answer.strip() else "Nie udzielono odpowiedzi.")
    if grade is not None:
        st.write("Punkty (AI):", f"{_format_points(grade.points)} / 1 pkt")
        st.write("Informacja zwrotna:", grade.feedback)
    else:
        st.write("Ocena:", "Oczekuje na ocenienie.")
    st.write("Odpowiedź wzorcowa:", question.reference_answer)
    st.write("Wyjaśnienie:", question.explanation)


def main():
    st.set_page_config(page_title="AI Test Creator", page_icon="📝", layout="centered")
    if "stage" not in st.session_state:
        reset_quiz()

    st.title("AI Test Creator")
    st.info(
        "Gemini AI tworzy pytania na podstawie Twojego materiału. "
        "Tryb Demo korzysta z gotowych pytań niezależnych od wpisanego tekstu."
    )
    if st.session_state.stage == "setup":
        show_setup()
    elif st.session_state.stage == "quiz":
        show_quiz()
    else:
        show_results()


if __name__ == "__main__":
    main()
