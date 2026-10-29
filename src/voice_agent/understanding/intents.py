"""The closed action list: everything a candidate can ask the exam script to do.

The rules fast path, the language model and the keyboard all produce one ``Intent``. The
model's output is forced through ``llm_output_grammar()`` and then re-validated by
``parse_llm_output``, so it can only ever name an action the code implements, with
arguments of the right type. Whether an action is allowed *now* (question exists,
something is pending confirmation, exam not submitted) is the state machine's job.

Each action's docstring is its description for the model's prompt; keep it short.
"""

import json
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from voice_agent.contract import Key, OptionLetter

# Generous ceilings; the real limits come from the exam outline and are checked by policy.
MAX_QUESTIONS: Final = 999
MAX_SECTIONS: Final = 50
# A valid action serialises to well under this; anything longer is not worth parsing.
MAX_LLM_OUTPUT_CHARS: Final = 256

QuestionNumber = Annotated[int, Field(ge=1, le=MAX_QUESTIONS)]
SectionNumber = Annotated[int, Field(ge=1, le=MAX_SECTIONS)]

_FROZEN = ConfigDict(extra="forbid", frozen=True)


# --- Arguments --------------------------------------------------------------------------


class NoArgs(BaseModel):
    model_config = _FROZEN


class ChoiceArgs(BaseModel):
    model_config = _FROZEN
    choice: OptionLetter


class QuestionArgs(BaseModel):
    model_config = _FROZEN
    question: QuestionNumber


class MaybeQuestionArgs(BaseModel):
    """``question`` is null for the current question (or, for review, all questions)."""

    model_config = _FROZEN
    question: QuestionNumber | None = None


class ChangeAnswerArgs(BaseModel):
    model_config = _FROZEN
    choice: OptionLetter
    question: QuestionNumber | None = None


class SectionArgs(BaseModel):
    """Sections by number only; matching a spoken section name is done by the rules,
    against the outline, so the model never needs to see exam content."""

    model_config = _FROZEN
    section: SectionNumber


class _Action(BaseModel):
    model_config = _FROZEN


# --- Answering --------------------------------------------------------------------------


class Answer(_Action):
    """Choose an option for the current question."""

    action: Literal["answer"] = "answer"
    args: ChoiceArgs


class Confirm(_Action):
    """Say yes to what was just read back."""

    action: Literal["confirm"] = "confirm"
    args: NoArgs = NoArgs()


class Cancel(_Action):
    """Say no, or back out of what was just read back."""

    action: Literal["cancel"] = "cancel"
    args: NoArgs = NoArgs()


class ChangeAnswer(_Action):
    """Replace an answer already given, for this question or a numbered one."""

    action: Literal["change_answer"] = "change_answer"
    args: ChangeAnswerArgs


class ClearAnswer(_Action):
    """Remove the answer, leaving the question unanswered."""

    action: Literal["clear_answer"] = "clear_answer"
    args: MaybeQuestionArgs = MaybeQuestionArgs()


# --- Moving -----------------------------------------------------------------------------


class Next(_Action):
    """Go to the next question."""

    action: Literal["next"] = "next"
    args: NoArgs = NoArgs()


class Previous(_Action):
    """Go back one question."""

    action: Literal["previous"] = "previous"
    args: NoArgs = NoArgs()


class GoTo(_Action):
    """Go to a question by number."""

    action: Literal["go_to"] = "go_to"
    args: QuestionArgs


class NextUnanswered(_Action):
    """Go to the next question with no answer."""

    action: Literal["next_unanswered"] = "next_unanswered"
    args: NoArgs = NoArgs()


class GoToSection(_Action):
    """Go to the start of a section by number."""

    action: Literal["go_to_section"] = "go_to_section"
    args: SectionArgs


class First(_Action):
    """Go to the first question."""

    action: Literal["first"] = "first"
    args: NoArgs = NoArgs()


class Last(_Action):
    """Go to the last question."""

    action: Literal["last"] = "last"
    args: NoArgs = NoArgs()


# --- Hearing again ----------------------------------------------------------------------


class RepeatQuestion(_Action):
    """Read the current question and its options again."""

    action: Literal["repeat_question"] = "repeat_question"
    args: NoArgs = NoArgs()


class RepeatOptions(_Action):
    """Read only the options again."""

    action: Literal["repeat_options"] = "repeat_options"
    args: NoArgs = NoArgs()


class RepeatOption(_Action):
    """Read one option again."""

    action: Literal["repeat_option"] = "repeat_option"
    args: ChoiceArgs


class SpellOption(_Action):
    """Spell one option letter by letter."""

    action: Literal["spell_option"] = "spell_option"
    args: ChoiceArgs


# --- Status -----------------------------------------------------------------------------


class WhereAmI(_Action):
    """Say the current question number and total."""

    action: Literal["where_am_i"] = "where_am_i"
    args: NoArgs = NoArgs()


class TimeLeft(_Action):
    """Say how much time is left."""

    action: Literal["time_left"] = "time_left"
    args: NoArgs = NoArgs()


class CountAnswered(_Action):
    """Say how many questions are answered."""

    action: Literal["count_answered"] = "count_answered"
    args: NoArgs = NoArgs()


class ListUnanswered(_Action):
    """List the question numbers with no answer."""

    action: Literal["list_unanswered"] = "list_unanswered"
    args: NoArgs = NoArgs()


class ReviewAnswers(_Action):
    """Read back the candidate's own answers: one question, or all if null."""

    action: Literal["review_answers"] = "review_answers"
    args: MaybeQuestionArgs = MaybeQuestionArgs()


# --- Listening comfort ------------------------------------------------------------------


class Faster(_Action):
    """Speak faster."""

    action: Literal["faster"] = "faster"
    args: NoArgs = NoArgs()


class Slower(_Action):
    """Speak slower."""

    action: Literal["slower"] = "slower"
    args: NoArgs = NoArgs()


class NormalSpeed(_Action):
    """Return to normal speaking speed."""

    action: Literal["normal_speed"] = "normal_speed"
    args: NoArgs = NoArgs()


class LouderCues(_Action):
    """Make the sound cues louder."""

    action: Literal["louder_cues"] = "louder_cues"
    args: NoArgs = NoArgs()


class QuieterCues(_Action):
    """Make the sound cues quieter."""

    action: Literal["quieter_cues"] = "quieter_cues"
    args: NoArgs = NoArgs()


# --- Finishing --------------------------------------------------------------------------


class Submit(_Action):
    """Finish and submit the exam."""

    action: Literal["submit"] = "submit"
    args: NoArgs = NoArgs()


class Help(_Action):
    """Say what the candidate can say."""

    action: Literal["help"] = "help"
    args: NoArgs = NoArgs()


# --- Outcomes that are not actions ------------------------------------------------------


class Unclear(_Action):
    """The sentence does not match any action."""

    action: Literal["unclear"] = "unclear"
    args: NoArgs = NoArgs()


class NotAllowed(_Action):
    """A request outside the exam script: answers, hints, explanations, anything else."""

    action: Literal["not_allowed"] = "not_allowed"
    args: NoArgs = NoArgs()


Action = (
    Answer
    | Confirm
    | Cancel
    | ChangeAnswer
    | ClearAnswer
    | Next
    | Previous
    | GoTo
    | NextUnanswered
    | GoToSection
    | First
    | Last
    | RepeatQuestion
    | RepeatOptions
    | RepeatOption
    | SpellOption
    | WhereAmI
    | TimeLeft
    | CountAnswered
    | ListUnanswered
    | ReviewAnswers
    | Faster
    | Slower
    | NormalSpeed
    | LouderCues
    | QuieterCues
    | Submit
    | Help
)

# What understanding returns. ``Unclear`` and ``NotAllowed`` get a fixed reply and never
# change session state.
Intent = Annotated[Action | Unclear | NotAllowed, Field(discriminator="action")]

ACTION_TYPES: Final[tuple[type[_Action], ...]] = get_args(Action)
INTENT_TYPES: Final[tuple[type[_Action], ...]] = (*ACTION_TYPES, Unclear, NotAllowed)

_intent_adapter: TypeAdapter[Action | Unclear | NotAllowed] = TypeAdapter(Intent)


def intent_name(intent_type: type[_Action]) -> str:
    name: str = intent_type.model_fields["action"].default
    return name


# --- The model's output ------------------------------------------------------------------
#
# Flat JSON: the action and its arguments side by side, optional arguments left out when
# empty, e.g. {"action": "go_to", "question": 12} or {"action": "next"}. The service writes
# the opening itself (LLM_OUTPUT_PREFIX); the model only completes it, under a grammar that
# allows nothing but the canonical text of a real action. On a CPU every token the model
# writes costs a full step (about 0.1 s on a laptop), and the grammar's fixed punctuation
# counts the same as a meaningful token, so the form is kept as short as it can be.

LLM_OUTPUT_PREFIX: Final = '{"action": "'


def _args_type(intent_type: type[_Action]) -> type[BaseModel]:
    args: type[BaseModel] = intent_type.model_fields["args"].annotation  # type: ignore[assignment]
    return args


def llm_output_text(intent: Intent) -> str:
    """The canonical text of ``intent``: the only form the grammar can produce."""
    out: dict[str, Any] = {"action": intent.action}
    out.update({k: v for k, v in intent.args.model_dump().items() if v is not None})
    return json.dumps(out)


def llm_output_grammar() -> str:
    """GBNF for what the model writes after ``LLM_OUTPUT_PREFIX``: one canonical action."""
    alternatives = []
    for t in INTENT_TYPES:
        parts = [json.dumps(intent_name(t) + '"')]
        for name, field in _args_type(t).model_fields.items():
            value = "letter" if name == "choice" else "number"
            piece = f"{json.dumps(f', "{name}": ')} {value}"
            parts.append(piece if field.is_required() else f"({piece})?")
        alternatives.append(f"({' '.join(parts)} {json.dumps('}')})")
    return (
        "root ::= " + " | ".join(alternatives) + "\n"
        'letter ::= "\\"" [A-E] "\\""\n'
        "number ::= [1-9] [0-9]? [0-9]?\n"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("duplicate key")
    return obj


_BY_NAME: Final[dict[str, type[_Action]]] = {intent_name(t): t for t in INTENT_TYPES}


def parse_llm_output(raw: str) -> Action | Unclear | NotAllowed:
    """Validate model output. Anything that is not exactly one valid intent is Unclear."""
    if len(raw) > MAX_LLM_OUTPUT_CHARS:
        return Unclear()
    raw = raw.strip()
    try:
        data = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        intent_type = _BY_NAME[data.pop("action")]
        args = _args_type(intent_type).model_validate(data, strict=True)
        intent = _intent_adapter.validate_python(
            {"action": intent_name(intent_type), "args": args}, strict=True
        )
    except (ValueError, ValidationError, KeyError, TypeError, AttributeError):
        return Unclear()
    # Accept only the canonical text: no nulls, no coerced values, no other spacing or
    # order. The grammar can only emit that text, so anything else means it was not
    # applied, and guessing what was meant is exactly what must not happen.
    if llm_output_text(intent) != raw:
        return Unclear()
    return intent


_KEY_ACTIONS: Final[dict[Key, Action]] = {
    "1": Answer(args=ChoiceArgs(choice="A")),
    "2": Answer(args=ChoiceArgs(choice="B")),
    "3": Answer(args=ChoiceArgs(choice="C")),
    "4": Answer(args=ChoiceArgs(choice="D")),
    "5": Answer(args=ChoiceArgs(choice="E")),
    "R": RepeatQuestion(),
    "N": Next(),
    "P": Previous(),
    "Enter": Confirm(),
    "Esc": Cancel(),
}


def action_for_key(key: Key) -> Action:
    """Keyboard fallback: each key is the same action as its voice command."""
    return _KEY_ACTIONS[key]
