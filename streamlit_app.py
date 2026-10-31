import streamlit as st

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
        source_text = st.text_area(
            "Materiał do nauki",
            value=st.session_state.get("source_text", ""),
            height=220,
            placeholder="Wklej notatki lub fragment materiału edukacyjnego…",
            help="W wersji demonstracyjnej materiał jest opcjonalny i nie wpływa na pytania.",
        )
        num_questions = st.selectbox("Liczba pytań", options=range(1, 11), index=4)
        submitted = st.form_submit_button("Rozpocznij quiz", type="primary")

    if submitted:
        st.session_state.source_text = source_text.strip()
        # Future integration: generate_quiz(source_text, num_questions).
        st.session_state.quiz = create_mock_quiz(num_questions)
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
        "Wersja demonstracyjna korzysta z przykładowych pytań, a nie z Gemini API. "
        "Wpisany materiał nie wpływa jeszcze na treść quizu."
    )
    if st.session_state.stage == "setup":
        show_setup()
    elif st.session_state.stage == "quiz":
        show_quiz()
    else:
        show_results()


if __name__ == "__main__":
    main()
