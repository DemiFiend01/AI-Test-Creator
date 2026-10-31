import streamlit as st

from app.gemini_service import generate_quiz
from app.mock_quiz import create_mock_quiz
from app.quiz_scoring import score_quiz


def reset_quiz():
    for key in list(st.session_state):
        if key.startswith("quiz_answer_"):
            del st.session_state[key]
    st.session_state.stage = "setup"
    st.session_state.quiz = None
    st.session_state.answers = {}
    st.session_state.results = None


def show_setup():
    st.subheader("1. Przygotuj quiz")
    with st.form("setup_form"):
        modes = ("Gemini AI", "Demo")
        mode = st.radio(
            "Tryb quizu",
            options=modes,
            index=modes.index(st.session_state.get("generation_mode", "Gemini AI")),
            key="setup_mode",
            horizontal=True,
        )
        source_text = st.text_area(
            "Materiał do nauki",
            value=st.session_state.get("source_text", ""),
            height=220,
            placeholder="Wklej notatki lub fragment materiału edukacyjnego…",
            help="Gemini AI wymaga materiału źródłowego. W trybie Demo tekst jest opcjonalny.",
            key="setup_source_text",
        )
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
        st.session_state.num_questions = num_questions
        if mode == "Gemini AI":
            if not source_text.strip():
                st.error("W trybie Gemini AI wprowadź niepusty materiał źródłowy.")
                return
            try:
                with st.spinner("Gemini AI przygotowuje pytania…"):
                    quiz = generate_quiz(source_text.strip(), num_questions)
            except Exception:
                st.error(
                    "Nie udało się wygenerować quizu. Spróbuj ponownie lub wybierz Demo. "
                    "Jeśli problem się powtarza, sprawdź połączenie i konfigurację Gemini."
                )
                return
        else:
            quiz = create_mock_quiz(num_questions)

        st.session_state.quiz = quiz
        st.session_state.answers = {}
        st.session_state.results = None
        st.session_state.stage = "quiz"
        st.rerun()


def show_quiz():
    quiz = st.session_state.quiz
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
