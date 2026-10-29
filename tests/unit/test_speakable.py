import pytest

from voice_agent.speech.speakable import speakable
from voice_agent.speech.tts import fixed_sentences, split_sentences


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Simplify 2³ × 2².", "Simplify 2 cubed times 2 squared."),
        ("x² − 9 = 0", "x squared minus 9 equals 0"),
        ("2⁵", "2 to the power 5"),
        ("Convert 101₂ to base ten.", "Convert 101 base 2 to base ten."),
        ("Find x if 3x + 5 = 20.", "Find x if 3x plus 5 equals 20."),
        ("What is 15% of 240?", "What is 15 percent of 240?"),
        ("₦5,000 at 10% per annum", "5,000 naira at 10 percent per annum"),
        ("x = ±3", "x equals plus or minus 3"),
        ("1/6", "1 over 6"),
        ("90°", "90 degrees"),
        ("3 ≤ x", "3 is less than or equal to x"),
        ("the sequence 3, 6, 12, 24, …?", "the sequence 3, 6, 12, 24, and so on?"),
        # Plain text and things it does not know stay as they are.
        ("Green plants make their food by", "Green plants make their food by"),
        ("and/or a/b", "and/or a/b"),
        ("0.75", "0.75"),
    ],
)
def test_symbols_become_words(text: str, spoken: str) -> None:
    assert speakable(text) == spoken


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Saved.", ["Saved."]),
        (
            "Question 5 of 60. What is 0.75 as a fraction? Option A: 3 over 4.",
            ["Question 5 of 60.", "What is 0.75 as a fraction?", "Option A: 3 over 4."],
        ),
        ("Wow! Next.", ["Wow!", "Next."]),
        ("  ", []),
    ],
)
def test_split_sentences(text: str, sentences: list[str]) -> None:
    assert split_sentences(text) == sentences


def test_fixed_sentences_are_the_common_prompts() -> None:
    fixed = fixed_sentences()
    assert "Saved." in fixed
    assert "Say yes to save it, or no to cancel." in fixed
    assert "Say a letter, A to D, to answer." in fixed
    assert len(fixed) == len(set(fixed)) and 30 < len(fixed) < 80
