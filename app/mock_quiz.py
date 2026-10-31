from app.schemas import Question, Quiz


def create_mock_quiz(num_questions: int) -> Quiz:
    """Return example questions independent of the user's source material."""
    if (
        isinstance(num_questions, bool)
        or not isinstance(num_questions, int)
        or not 1 <= num_questions <= 10
    ):
        raise ValueError("Liczba pytań musi być liczbą całkowitą od 1 do 10.")

    examples = [
        (
            "Która planeta krąży najbliżej Słońca?",
            ["Wenus", "Merkury", "Ziemia", "Mars"],
            1,
            "Merkury jest pierwszą planetą Układu Słonecznego, licząc od Słońca.",
        ),
        (
            "Ile wynosi 7 × 8?",
            ["48", "54", "56", "64"],
            2,
            "Iloczyn liczb 7 i 8 wynosi 56.",
        ),
        (
            "Jak nazywa się stolica Polski?",
            ["Warszawa", "Kraków", "Gdańsk", "Poznań"],
            0,
            "Stolicą Polski jest Warszawa.",
        ),
        (
            "Który gaz rośliny pobierają podczas fotosyntezy?",
            ["Hel", "Wodór", "Azot", "Dwutlenek węgla"],
            3,
            "Rośliny wykorzystują dwutlenek węgla i wodę do wytwarzania cukrów.",
        ),
        (
            "Ile boków ma trójkąt?",
            ["Dwa", "Trzy", "Cztery", "Pięć"],
            1,
            "Trójkąt jest wielokątem o trzech bokach.",
        ),
        (
            "Który ocean jest największy?",
            ["Atlantycki", "Indyjski", "Spokojny", "Arktyczny"],
            2,
            "Ocean Spokojny, czyli Pacyfik, ma największą powierzchnię.",
        ),
        (
            "Co oznacza wzór chemiczny H₂O?",
            ["Wodę", "Tlen", "Sól kuchenną", "Dwutlenek węgla"],
            0,
            "Cząsteczka wody składa się z dwóch atomów wodoru i jednego atomu tlenu.",
        ),
        (
            "Ile minut ma godzina?",
            ["30", "45", "50", "60"],
            3,
            "Jedna godzina dzieli się na 60 minut.",
        ),
        (
            "Który narząd pompuje krew w organizmie człowieka?",
            ["Płuca", "Serce", "Żołądek", "Wątroba"],
            1,
            "Serce, kurcząc się, tłoczy krew do naczyń krwionośnych.",
        ),
        (
            "Która z podanych liczb jest parzysta?",
            ["7", "9", "12", "15"],
            2,
            "Liczba 12 dzieli się przez 2 bez reszty.",
        ),
    ]
    return Quiz(
        title="Quiz przykładowy — wiedza ogólna",
        questions=[
            Question(
                id=index,
                question=question,
                options=options,
                correct_answer=correct_answer,
                explanation=explanation,
            )
            for index, (question, options, correct_answer, explanation)
            in enumerate(examples[:num_questions], start=1)
        ],
    )
