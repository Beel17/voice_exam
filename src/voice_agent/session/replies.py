"""Every sentence the service can say, as fixed templates.

The state machine decides *which* reply; this module decides the words. Exam content
(question and option text, section titles) is inserted verbatim from the adapter and is
never paraphrased. Nothing here ever comes from the language model.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, assert_never

from voice_agent.adapters.base import OPTION_LETTERS, Question, RejectReason
from voice_agent.contract import OptionLetter, SayKind


@dataclass(frozen=True, slots=True)
class Spoken:
    text: str
    kind: SayKind


Limit = Literal["fastest", "slowest", "loudest", "quietest"] | None


# --- Replies that read exam content (need the question) --------------------------------


@dataclass(frozen=True, slots=True)
class ReadQuestion:
    question: int
    of: int
    part: Literal["full", "options"]
    answer: OptionLetter | None


@dataclass(frozen=True, slots=True)
class ReadOption:
    question: int
    choice: OptionLetter


@dataclass(frozen=True, slots=True)
class SpellOption:
    question: int
    choice: OptionLetter


@dataclass(frozen=True, slots=True)
class ConfirmAnswer:
    question: int
    choice: OptionLetter


ContentReply = ReadQuestion | ReadOption | SpellOption | ConfirmAnswer


# --- Replies from templates alone -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IdentifyPrompt:
    pass


@dataclass(frozen=True, slots=True)
class NotRegistered:
    pass


@dataclass(frozen=True, slots=True)
class PlatformUnavailable:
    pass


@dataclass(frozen=True, slots=True)
class OneMoment:
    pass


@dataclass(frozen=True, slots=True)
class StillThinking:
    """The language model is taking longer than a second: the candidate is not left in
    silence wondering whether they were heard."""


@dataclass(frozen=True, slots=True)
class PracticeEnded:
    """A practice exam (the fake platform) that was already finished: say how to take it
    again, or a tester is left hearing "submitted" whatever they do."""


@dataclass(frozen=True, slots=True)
class NothingHeard:
    """The talk key was held, but recognition found no words."""


@dataclass(frozen=True, slots=True)
class KeyboardMode:
    pass


@dataclass(frozen=True, slots=True)
class VoiceBack:
    pass


@dataclass(frozen=True, slots=True)
class Welcome:
    of: int
    section_titles: tuple[str, ...]
    duration_s: int


@dataclass(frozen=True, slots=True)
class WelcomeBack:
    question: int
    of: int


@dataclass(frozen=True, slots=True)
class IntroPrompt:
    pass


@dataclass(frozen=True, slots=True)
class ConfirmClear:
    question: int
    previous: OptionLetter


@dataclass(frozen=True, slots=True)
class Saved:
    question: int
    choice: OptionLetter | None


@dataclass(frozen=True, slots=True)
class SavedLocally:
    question: int


@dataclass(frozen=True, slots=True)
class WriteRefused:
    question: int
    reason: RejectReason


@dataclass(frozen=True, slots=True)
class NotSaved:
    question: int
    answer: OptionLetter | None


@dataclass(frozen=True, slots=True)
class NothingToConfirm:
    pass


@dataclass(frozen=True, slots=True)
class SayYesToConfirm:
    pass


@dataclass(frozen=True, slots=True)
class NotAnswered:
    question: int


@dataclass(frozen=True, slots=True)
class NoSuchOption:
    choice: OptionLetter
    option_count: int


@dataclass(frozen=True, slots=True)
class NoSuchQuestion:
    question: int
    of: int


@dataclass(frozen=True, slots=True)
class NoSuchSection:
    section: int
    section_count: int


@dataclass(frozen=True, slots=True)
class NoSections:
    pass


@dataclass(frozen=True, slots=True)
class AtLast:
    of: int


@dataclass(frozen=True, slots=True)
class AtFirst:
    pass


@dataclass(frozen=True, slots=True)
class AllAnswered:
    pass


@dataclass(frozen=True, slots=True)
class QuestionNotAvailable:
    question: int


@dataclass(frozen=True, slots=True)
class WhereAmI:
    question: int
    of: int
    section_title: str | None
    answer: OptionLetter | None
    started: bool


@dataclass(frozen=True, slots=True)
class TimeLeft:
    seconds: int | None


@dataclass(frozen=True, slots=True)
class TimeWarning:
    minutes: int


@dataclass(frozen=True, slots=True)
class CountAnswered:
    answered: int
    of: int


@dataclass(frozen=True, slots=True)
class Unanswered:
    questions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReviewOne:
    question: int
    answer: OptionLetter | None


@dataclass(frozen=True, slots=True)
class ReviewAll:
    answers: tuple[tuple[int, OptionLetter | None], ...]


@dataclass(frozen=True, slots=True)
class SpeedChanged:
    speed: float
    limit: Limit


@dataclass(frozen=True, slots=True)
class CuesChanged:
    limit: Limit


@dataclass(frozen=True, slots=True)
class Help:
    pass


@dataclass(frozen=True, slots=True)
class NotUnderstood:
    offer_keyboard: bool


@dataclass(frozen=True, slots=True)
class OutOfScope:
    pass


@dataclass(frozen=True, slots=True)
class ConfirmSubmit:
    answered: int
    unanswered: int


@dataclass(frozen=True, slots=True)
class ConfirmSubmitFinal:
    unanswered: int


@dataclass(frozen=True, slots=True)
class NotSubmitted:
    pass


@dataclass(frozen=True, slots=True)
class SubmitBlocked:
    unsynced: int


@dataclass(frozen=True, slots=True)
class Submitting:
    pass


@dataclass(frozen=True, slots=True)
class Submitted:
    queued: bool


@dataclass(frozen=True, slots=True)
class SubmitRefused:
    reason: RejectReason


@dataclass(frozen=True, slots=True)
class TimeIsUp:
    pass


@dataclass(frozen=True, slots=True)
class ExamEnded:
    reason: Literal["submitted", "time_up"]


TemplateReply = (
    IdentifyPrompt
    | KeyboardMode
    | VoiceBack
    | OneMoment
    | StillThinking
    | NothingHeard
    | PracticeEnded
    | NotRegistered
    | PlatformUnavailable
    | Welcome
    | WelcomeBack
    | IntroPrompt
    | ConfirmClear
    | Saved
    | SavedLocally
    | WriteRefused
    | NotSaved
    | NothingToConfirm
    | SayYesToConfirm
    | NotAnswered
    | NoSuchOption
    | NoSuchQuestion
    | NoSuchSection
    | NoSections
    | AtLast
    | AtFirst
    | AllAnswered
    | QuestionNotAvailable
    | WhereAmI
    | TimeLeft
    | TimeWarning
    | CountAnswered
    | Unanswered
    | ReviewOne
    | ReviewAll
    | SpeedChanged
    | CuesChanged
    | Help
    | NotUnderstood
    | OutOfScope
    | ConfirmSubmit
    | ConfirmSubmitFinal
    | NotSubmitted
    | SubmitBlocked
    | Submitting
    | Submitted
    | SubmitRefused
    | TimeIsUp
    | ExamEnded
)
Reply = ContentReply | TemplateReply


def needs_question(reply: Reply) -> int | None:
    """The question number whose content ``render`` needs, if any."""
    return reply.question if isinstance(reply, ContentReply) else None


# --- Wording helpers --------------------------------------------------------------------


def _sentence(text: str) -> str:
    """Exam text as a sentence: verbatim, with a full stop only if it has no ending."""
    text = text.strip()
    return text if text[-1] in ".?!:" else text + "."


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _join(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _ranges(numbers: Iterable[int]) -> str:
    """1, 2, 3, 5, 7, 8, 9 -> "1 to 3, 5 and 7 to 9": short to hear, exact to act on."""
    parts: list[str] = []
    run: list[int] = []
    for n in sorted(numbers):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            parts.append(_run(run))
        run = [n]
    if run:
        parts.append(_run(run))
    return _join(parts)


def _run(run: list[int]) -> str:
    if len(run) == 1:
        return str(run[0])
    if len(run) == 2:
        return f"{run[0]}, {run[1]}"
    return f"{run[0]} to {run[-1]}"


def _duration(seconds: int) -> str:
    if seconds < 60:
        return "less than a minute"
    hours, minutes = divmod(seconds // 60, 60)
    parts = ([_plural(hours, "hour")] if hours else []) + (
        [_plural(minutes, "minute")] if minutes else []
    )
    return _join(parts)


def _letters(option_count: int) -> str:
    return f"{OPTION_LETTERS[0]} to {OPTION_LETTERS[option_count - 1]}"


def _answer_phrase(question: int, answer: OptionLetter | None) -> str:
    if answer is None:
        return f"Question {question} is not answered."
    return f"Your answer for question {question} is {answer}."


def _option_text(question: Question, choice: OptionLetter) -> str:
    option = question.options[OPTION_LETTERS.index(choice)]
    return option.spoken_text or option.text


def _spell(text: str) -> str:
    return ", ".join("space" if c == " " else c for c in text.strip())


HELP_TEXT = (
    "You can say: next, previous, or a question number to move. "
    "A letter, like B, to answer. Repeat, to hear the question again. "
    "Where am I, time left, or which questions are unanswered. "
    "Faster or slower, to change my speed. Submit, when you are finished."
)
KEYBOARD_TEXT = (
    "You can also use the keyboard: number keys 1 to 5 choose an option, "
    "Enter confirms, Escape cancels, and R repeats."
)
OUT_OF_SCOPE_TEXT = "I can only read the questions and options, and record your answers."


# --- Rendering --------------------------------------------------------------------------


def render(reply: Reply, question: Question | None = None) -> list[Spoken]:
    """The words for ``reply``. Content replies need the question ``needs_question`` names."""
    if isinstance(reply, ContentReply):
        if question is None or question.number != reply.question:
            raise ValueError(f"reply needs question {reply.question}")
        return _render_content(reply, question)
    return [_render_template(reply)]


def _render_content(reply: ContentReply, q: Question) -> list[Spoken]:
    match reply:
        case ReadQuestion(part=part):
            if part == "options":
                return _read_options(reply, q)
            body = _sentence(q.spoken_text or q.text)
            return [
                Spoken(f"Question {reply.question} of {reply.of}. {body}", "question"),
                *_read_options(reply, q),
            ]
        case ReadOption(choice=choice):
            return [Spoken(f"Option {choice}: {_sentence(_option_text(q, choice))}", "options")]
        case SpellOption(choice=choice):
            spelt = _spell(q.options[OPTION_LETTERS.index(choice)].text)
            return [Spoken(f"Option {choice} is spelt: {spelt}.", "options")]
        case ConfirmAnswer(question=n, choice=choice):
            text = _sentence(_option_text(q, choice))
            return [
                Spoken(
                    f"For question {n}, you chose option {choice}: {text} "
                    "Say yes to save it, or no to cancel.",
                    "confirm",
                )
            ]


def _read_options(reply: ReadQuestion, q: Question) -> list[Spoken]:
    options = " ".join(
        f"Option {o.letter}: {_sentence(o.spoken_text or o.text)}" for o in q.options
    )
    prompt = (
        f"Your answer is {reply.answer}."
        if reply.answer
        else f"Say a letter, {_letters(len(q.options))}, to answer."
    )
    return [Spoken(options, "options"), Spoken(prompt, "prompt")]


def _render_template(reply: TemplateReply) -> Spoken:
    match reply:
        case IdentifyPrompt():
            return Spoken(
                "Welcome to your exam. Please wait while the invigilator signs you in.", "prompt"
            )
        case NotRegistered():
            return Spoken(
                "That registration number is not registered for this exam. "
                "Please let the invigilator know.",
                "prompt",
            )
        case PlatformUnavailable():
            return Spoken(
                "The exam system cannot be reached right now. Please let the invigilator know.",
                "prompt",
            )
        case OneMoment():
            return Spoken("One moment, please. Then say that again, or use the keyboard.", "prompt")
        case StillThinking():
            return Spoken("One moment.", "prompt")
        case PracticeEnded():
            return Spoken(
                "This practice exam is finished. To take it again, sign in with another "
                "registration number, or close the service window and run Start over.",
                "prompt",
            )
        case NothingHeard():
            return Spoken(
                "I did not hear anything. Hold Space while you speak, then let go.", "prompt"
            )
        case KeyboardMode():
            return Spoken(
                "Voice is not available for a moment. Please use the keyboard: number keys "
                "1 to 5 choose an option, Enter confirms, Escape cancels, R repeats, and N "
                "and P move to the next and previous question. I will tell you when voice "
                "is back.",
                "prompt",
            )
        case VoiceBack():
            return Spoken("Voice is back. You can speak again.", "prompt")
        case Welcome(of=of, section_titles=titles, duration_s=duration):
            sections = (
                f" in {_plural(len(titles), 'section')}: {_join(list(titles))}" if titles else ""
            )
            return Spoken(
                f"Welcome. This exam has {_plural(of, 'question')}{sections}. "
                f"You have {_duration(duration)}. "
                "Say start to begin, or say a question number to start there. "
                "Say help at any time.",
                "prompt",
            )
        case WelcomeBack(question=n, of=of):
            return Spoken(f"Welcome back. You were on question {n} of {of}.", "prompt")
        case IntroPrompt():
            return Spoken("Say start to begin, or say a question number.", "prompt")
        case ConfirmClear(question=n, previous=previous):
            return Spoken(
                f"Remove your answer {previous} for question {n}? Say yes to remove it, "
                "or no to keep it.",
                "confirm",
            )
        case Saved(choice=None, question=n):
            return Spoken(f"Answer removed. Question {n} is now unanswered.", "prompt")
        case Saved():
            return Spoken("Saved.", "prompt")
        case SavedLocally():
            return Spoken(
                "Saved on this computer, waiting to sync. Please let the invigilator know.",
                "prompt",
            )
        case WriteRefused(question=n, reason=reason):
            return Spoken(
                f"Your answer for question {n} was not saved. {_refusal(reason)}", "prompt"
            )
        case NotSaved(question=n, answer=answer):
            return Spoken(f"Not saved. {_answer_phrase(n, answer)}", "prompt")
        case NothingToConfirm():
            return Spoken("There is nothing to confirm right now.", "prompt")
        case SayYesToConfirm():
            return Spoken("To confirm, please say yes, or press Enter.", "confirm")
        case NotAnswered(question=n):
            return Spoken(f"Question {n} has no answer to remove.", "prompt")
        case NoSuchOption(choice=choice, option_count=count):
            return Spoken(
                f"There is no option {choice}. This question has options {_letters(count)}.",
                "prompt",
            )
        case NoSuchQuestion(question=n, of=of):
            return Spoken(
                f"There is no question {n}. This exam has {_plural(of, 'question')}.", "prompt"
            )
        case NoSuchSection(section=s, section_count=count):
            return Spoken(
                f"There is no section {s}. This exam has {_plural(count, 'section')}.", "prompt"
            )
        case NoSections():
            return Spoken("This exam has no sections. Say a question number instead.", "prompt")
        case AtLast(of=of):
            return Spoken(
                f"This is the last question, {of}. Say submit when you are ready, "
                "or ask which questions are unanswered.",
                "prompt",
            )
        case AtFirst():
            return Spoken("This is the first question.", "prompt")
        case AllAnswered():
            return Spoken("You have answered every question.", "prompt")
        case QuestionNotAvailable(question=n):
            return Spoken(
                f"Sorry, question {n} cannot be loaded right now. Please try again, "
                "or let the invigilator know.",
                "prompt",
            )
        case WhereAmI(started=False, of=of):
            return Spoken(
                f"You have not started yet. This exam has {_plural(of, 'question')}.", "prompt"
            )
        case WhereAmI(question=n, of=of, section_title=title, answer=answer):
            where = f"You are on question {n} of {of}" + (f", in {title}." if title else ".")
            return Spoken(f"{where} {_answer_phrase(n, answer)}", "prompt")
        case TimeLeft(seconds=seconds):
            if seconds is None:
                return Spoken(
                    "I cannot get the time left right now. Please ask the invigilator.", "prompt"
                )
            return Spoken(f"You have {_duration(seconds)} left.", "prompt")
        case TimeWarning(minutes=minutes):
            return Spoken(f"{_plural(minutes, 'minute')} left.", "prompt")
        case CountAnswered(answered=answered, of=of):
            text = f"You have answered {answered} of {_plural(of, 'question')}."
            if answered < of:  # "how many are left" is the usual way to ask
                text += f" {of - answered} {'is' if of - answered == 1 else 'are'} left."
            return Spoken(text, "prompt")
        case Unanswered(questions=()):
            return Spoken("You have answered every question.", "prompt")
        case Unanswered(questions=qs):
            label = "question" if len(qs) == 1 else "questions"
            return Spoken(f"You have not answered {label} {_ranges(qs)}.", "prompt")
        case ReviewOne(question=n, answer=answer):
            text = (
                f"For question {n} you chose {answer}."
                if answer
                else f"Question {n} is not answered."
            )
            return Spoken(text, "prompt")
        case ReviewAll(answers=answers):
            return Spoken(_review_all(answers), "prompt")
        case SpeedChanged(limit="fastest"):
            return Spoken("This is already my fastest speed.", "prompt")
        case SpeedChanged(limit="slowest"):
            return Spoken("This is already my slowest speed.", "prompt")
        case SpeedChanged(speed=speed):
            return Spoken(f"Speed set to {speed:g} times.", "prompt")
        case CuesChanged(limit="loudest"):
            return Spoken("The sounds are already at their loudest.", "prompt")
        case CuesChanged(limit="quietest"):
            return Spoken("The sounds are already at their quietest.", "prompt")
        case CuesChanged():
            return Spoken("Sound volume changed.", "prompt")
        case Help():
            return Spoken(HELP_TEXT, "prompt")
        case NotUnderstood(offer_keyboard=offer):
            text = (
                "Sorry, I did not catch that. You can say next, previous, repeat, "
                "a question number, or a letter."
            )
            return Spoken(f"{text} {KEYBOARD_TEXT}" if offer else text, "prompt")
        case OutOfScope():
            return Spoken(OUT_OF_SCOPE_TEXT, "prompt")
        case ConfirmSubmit(answered=answered, unanswered=unanswered):
            left = f" {_plural(unanswered, 'question')} not answered." if unanswered else ""
            return Spoken(
                f"You have answered {answered}.{left} Do you want to submit your exam? "
                "Say yes or no.",
                "confirm",
            )
        case ConfirmSubmitFinal():
            return Spoken(
                "Submitting is final. You cannot change any answer afterwards. "
                "Say yes again to submit, or no to go back.",
                "confirm",
            )
        case NotSubmitted():
            return Spoken("Not submitted. You can carry on.", "prompt")
        case SubmitBlocked(unsynced=n):
            return Spoken(
                f"{_plural(n, 'answer')} still waiting to sync, so the exam cannot be "
                "submitted yet. Please let the invigilator know.",
                "prompt",
            )
        case Submitting():
            return Spoken("Your exam is being submitted. Please wait.", "prompt")
        case Submitted(queued=True):
            return Spoken(
                "Your exam is submitted on this computer and waiting to sync. "
                "Please let the invigilator know.",
                "prompt",
            )
        case Submitted():
            return Spoken("Your exam has been submitted. Thank you.", "prompt")
        case SubmitRefused(reason=reason):
            return Spoken(f"The exam could not be submitted. {_refusal(reason)}", "prompt")
        case TimeIsUp():
            return Spoken("Time is up. Your exam has ended.", "prompt")
        case ExamEnded(reason="time_up"):
            return Spoken("Time is up, so your exam has ended.", "prompt")
        case ExamEnded():
            return Spoken("Your exam has been submitted, so nothing more can change.", "prompt")
        case _:  # pragma: no cover - mypy proves this unreachable
            assert_never(reply)


def _review_all(answers: tuple[tuple[int, OptionLetter | None], ...]) -> str:
    # Consecutive unanswered questions are grouped so a long gap is one phrase, not many.
    parts: list[str] = []
    gap: list[int] = []
    for n, answer in answers:
        if answer is None:
            gap.append(n)
            continue
        if gap:
            parts.append(_gap(gap))
            gap = []
        parts.append(f"Question {n}, {answer}.")
    if gap:
        parts.append(_gap(gap))
    return " ".join(parts)


def _gap(gap: list[int]) -> str:
    if len(gap) == 1:
        return f"Question {gap[0]}, not answered."
    return f"Questions {_ranges(gap)}, not answered."


def _refusal(reason: RejectReason) -> str:
    match reason:
        case RejectReason.TIME_UP:
            return "Time is up."
        case RejectReason.ALREADY_SUBMITTED:
            return "The exam has already been submitted."
        case _:
            return "Please let the invigilator know."
