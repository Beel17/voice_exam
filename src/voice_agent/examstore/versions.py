"""Each candidate's version of a paper: its own order of questions and of options.

Neighbours then see different questions at the same number, and the same question with
its options in a different order. Questions move only within their section, so subjects
stay together. A version is worked out once, from the exam and candidate IDs (so it is
reproducible), and stored at import, so it never changes even if this code does.

Answers are always stored against the paper's own question number and option letter:
marking, sync and the platform never need to know about versions.
"""

import hashlib
import random
from dataclasses import dataclass

from voice_agent.adapters.base import OPTION_LETTERS, ExamOutline, Option, Question
from voice_agent.contract import OptionLetter


@dataclass(frozen=True, slots=True)
class Version:
    # order[k] is the paper's number of the question this candidate sees as k + 1.
    order: tuple[int, ...]
    # options[paper number][i] is the paper's letter of the option shown as the i-th.
    options: dict[int, tuple[OptionLetter, ...]]

    def paper_number(self, shown: int) -> int:
        if not 1 <= shown <= len(self.order):
            raise ValueError(f"no question {shown}")
        return self.order[shown - 1]

    def paper_choice(self, shown: int, choice: OptionLetter) -> OptionLetter:
        """The paper's letter for the option this candidate sees as ``choice``."""
        letters = self.options[self.paper_number(shown)]
        index = OPTION_LETTERS.index(choice)
        if index >= len(letters):
            raise ValueError(f"no option {choice}")
        return letters[index]

    def shown_choice(self, paper_number: int, letter: OptionLetter) -> OptionLetter:
        """How this candidate sees the paper's option ``letter`` (to show saved answers)."""
        return OPTION_LETTERS[self.options[paper_number].index(letter)]

    def shown_number(self, paper_number: int) -> int:
        return self.order.index(paper_number) + 1

    def question(self, shown: int, paper: Question) -> Question:
        """The paper's question as this candidate sees it: renumbered, options relettered."""
        by_letter = {o.letter: o for o in paper.options}
        options = tuple(
            Option(
                letter=OPTION_LETTERS[i],
                text=by_letter[letter].text,
                spoken_text=by_letter[letter].spoken_text,
            )
            for i, letter in enumerate(self.options[paper.number])
        )
        return Question(
            number=shown, text=paper.text, spoken_text=paper.spoken_text, options=options
        )

    def to_json(self) -> dict[str, object]:
        return {
            "order": list(self.order),
            "options": {str(n): "".join(letters) for n, letters in self.options.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "Version":
        order = data["order"]
        options = data["options"]
        assert isinstance(order, list) and isinstance(options, dict)
        return cls(
            order=tuple(int(n) for n in order),
            options={int(n): tuple(letters) for n, letters in options.items()},
        )


def make_version(
    exam_id: str,
    candidate_id: str,
    outline: ExamOutline,
    questions: tuple[Question, ...],
    *,
    shuffle_questions: bool,
    shuffle_options: bool,
    keep_option_order: frozenset[int] = frozenset(),
) -> Version:
    seed = hashlib.sha256(f"{exam_id}\x00{candidate_id}".encode()).digest()
    rng = random.Random(seed)
    groups = (
        [range(s.first_question, s.last_question + 1) for s in outline.sections]
        if outline.sections
        else [range(1, outline.question_count + 1)]
    )
    order: list[int] = []
    for group in groups:
        numbers = list(group)
        if shuffle_questions:
            rng.shuffle(numbers)
        order.extend(numbers)
    options: dict[int, tuple[OptionLetter, ...]] = {}
    for q in questions:
        letters = [o.letter for o in q.options]
        if shuffle_options and q.number not in keep_option_order:
            rng.shuffle(letters)
        options[q.number] = tuple(letters)
    return Version(order=tuple(order), options=options)
