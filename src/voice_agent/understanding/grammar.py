"""The rules fast path: common sentences to actions in well under a millisecond.

Every rule must match the *whole* normalised sentence, so extra words ("what is the answer
to question 5") never trigger a command by accident; anything the rules do not fully
recognise returns ``None`` and goes to the language model. The one exception is the
out-of-scope scan at the end, which looks for requests for answers, hints or
explanations, and for instruction-injection phrases, anywhere in the sentence.

Option text and section titles come from the current question and the exam outline.
They are only ever compared against, never executed: saying an option's text chooses
that option (still subject to confirmation), whatever the text says.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from voice_agent.adapters.base import OPTION_LETTERS
from voice_agent.contract import OptionLetter
from voice_agent.understanding import intents as i

# Longer than any command; longer input goes to the model (or is refused by its caps).
MAX_RULE_CHARS: Final = 200


@dataclass(frozen=True, slots=True)
class RulesContext:
    """What the rules may compare against. Empty before the first question is read."""

    option_texts: tuple[tuple[str, ...], ...] = ()  # per option: its text and spoken_text
    section_titles: tuple[str, ...] = ()


# --- Normalisation ----------------------------------------------------------------------

_CONTRACTIONS: Final = {
    "what's": "what is", "it's": "it is", "that's": "that is", "i'm": "i am",
    "i'll": "i will", "i've": "i have", "i'd": "i would", "you're": "you are",
    "don't": "do not", "didn't": "did not", "haven't": "have not", "hasn't": "has not",
    "can't": "cannot", "won't": "will not", "let's": "let us", "where's": "where is",
}  # fmt: skip

_UNITS: Final = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
_TENS: Final = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip

# Politeness and hesitation around a command; stripped from either end, repeatedly.
_LEADING: Final = re.compile(
    r"^(?:please|kindly|ok|okay|so|um+|uh+|er+|erm|hmm+|now|then|alright|sorry|"
    r"(?:can|could|would|will) you(?: please)?|i want to|i would like to|i wanna|"
    r"i need to|let me|let us|i want you to|i do not understand(?: the question)?|"
    r"help me(?= (?:repeat|read|go|spell|move|check)))\s+"
)
# "Slow down small": "small" and "a bit" soften a request without changing it.
_TRAILING: Final = re.compile(r"\s+(?:please|now|for me|thank you|thanks|small|a bit|a little)$")


def normalise(text: str) -> str:
    """Lower case, contractions expanded, punctuation dropped, number words as digits."""
    text = text.lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    words = [_CONTRACTIONS.get(w, w) for w in text.split()]
    # Keep a minus sign as a word, so "question -5" is not heard as "question 5".
    text = re.sub(r"(?<![a-z0-9])-(?=\d)", " minus ", " ".join(words))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = _number_words(text.split())
    while True:
        stripped = _TRAILING.sub("", _LEADING.sub("", text))
        if stripped == text or not stripped:
            return text
        text = stripped


def _number_words(words: list[str]) -> str:
    """ "question twenty five" -> "question 25"; "one hundred and five" -> "105"."""
    out: list[str] = []
    value: int | None = None
    for k, w in enumerate(words):
        n = _UNITS.get(w, _TENS.get(w))
        if w == "one" and value is None and not _one_is_a_number(words, k):
            n = None  # "the third one", "the one that says", "next one"
        if n is not None:
            if value is None:
                value = n
            elif (value >= 100 and value % 100 == 0) or (
                value % 100 >= 20 and value % 10 == 0 and n < 10
            ):
                value += n  # "one hundred five", "twenty five"
            else:
                out.append(str(value))
                value = n
        elif w == "hundred" and value is not None and 1 <= value <= 9:
            value *= 100
        elif w == "and" and value is not None and value >= 100 and value % 100 == 0:
            following = words[k + 1] if k + 1 < len(words) else ""
            if following not in _UNITS and following not in _TENS:
                out.extend([str(value), w])
                value = None
        else:
            if value is not None:
                out.append(str(value))
                value = None
            out.append(w)
    if value is not None:
        out.append(str(value))
    return " ".join(out)


def _one_is_a_number(words: list[str], k: int) -> bool:
    before = words[k - 1] if k > 0 else ""
    after = words[k + 1] if k + 1 < len(words) else ""
    return len(words) == 1 or after == "hundred" or before in _BEFORE_A_NUMBER


_BEFORE_A_NUMBER: Final = frozenset(
    {"question", "number", "no", "to", "option", "from", "at", "with", "for", "review", "section"}
)


# --- Rules ------------------------------------------------------------------------------

_LETTER_WORDS: Final[dict[str, OptionLetter]] = {
    "a": "A", "ay": "A", "b": "B", "be": "B", "bee": "B", "c": "C", "see": "C", "sea": "C",
    "cee": "C", "d": "D", "dee": "D", "e": "E", "ee": "E",
}  # fmt: skip
_ORDINALS: Final[dict[str, int]] = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
}  # fmt: skip

L: Final = r"(?P<l>" + "|".join(sorted(_LETTER_WORDS, key=len, reverse=True)) + ")"
N: Final = r"(?P<n>\d{1,3})"
QN: Final = r"(?:question |number |no )?" + N
OPT: Final = r"(?:option |letter |answer )?"

_Builder = Callable[[re.Match[str]], i.Intent | None]
_Rule = tuple[re.Pattern[str], _Builder]


def _letter(m: re.Match[str]) -> OptionLetter:
    return _LETTER_WORDS[m["l"]]


def _number(m: re.Match[str]) -> int | None:
    """The question number in the match: None if absent, 0 if out of range."""
    raw = m.groupdict().get("n")
    if raw is None:
        return None
    n = int(raw)
    return n if 1 <= n <= i.MAX_QUESTIONS else 0


def _go_to(m: re.Match[str]) -> i.Intent | None:
    n = _number(m)
    return i.GoTo(args=i.QuestionArgs(question=n)) if n else None


def _answer(m: re.Match[str]) -> i.Intent:
    return i.Answer(args=i.ChoiceArgs(choice=_letter(m)))


def _answer_by_position(m: re.Match[str]) -> i.Intent:
    k = _ORDINALS.get(m["o"]) or int(m["o"])
    return i.Answer(args=i.ChoiceArgs(choice=OPTION_LETTERS[k - 1]))


def _change(m: re.Match[str]) -> i.Intent | None:
    n = _number(m)
    if n == 0:
        return None
    return i.ChangeAnswer(args=i.ChangeAnswerArgs(choice=_letter(m), question=n))


def _clear(m: re.Match[str]) -> i.Intent | None:
    n = _number(m)
    if n == 0:
        return None
    return i.ClearAnswer(args=i.MaybeQuestionArgs(question=n))


def _review(m: re.Match[str]) -> i.Intent | None:
    n = _number(m)
    return i.ReviewAnswers(args=i.MaybeQuestionArgs(question=n)) if n else None


def _section(m: re.Match[str]) -> i.Intent | None:
    n = int(m["n"])
    return i.GoToSection(args=i.SectionArgs(section=n)) if 1 <= n <= i.MAX_SECTIONS else None


def _repeat_option(m: re.Match[str]) -> i.Intent:
    return i.RepeatOption(args=i.ChoiceArgs(choice=_letter(m)))


def _spell_option(m: re.Match[str]) -> i.Intent:
    return i.SpellOption(args=i.ChoiceArgs(choice=_letter(m)))


def _rules(build: _Builder | i.Intent, *patterns: str) -> list[_Rule]:
    if callable(build):
        builder = build
    else:
        fixed = build
        builder = lambda _m: fixed  # noqa: E731
    return [(re.compile(p), builder) for p in patterns]


# Where two patterns could match the same sentence, the more specific one comes first.
_RULES: Final[list[_Rule]] = [
    # Yes and no.
    *_rules(
        i.Confirm(),
        r"yes|yeah|yea|yep|yes please|confirm|confirmed|correct|that is (?:correct|right)"
        r"|ok|okay|sure|save|save it|go ahead|right|yes save it|yes confirm|yes i am sure"
        r"|i am sure|definitely|absolutely|yes it is|yes that is it|yes that is right"
        r"|yes that is what i want|yes go ahead|yes submit|yes submit it|confirm it",
    ),
    *_rules(
        i.Cancel(),
        r"no|nope|cancel|do not save|do not save it|not that|not that one|wrong"
        r"|that is wrong|no thank you|no cancel",
    ),
    # "No, C" while confirming: the new letter replaces the pending one.
    *_rules(_answer, rf"no (?:it is |make it |i mean |i said )?{OPT}{L}"),
    # Answering.
    *_rules(
        _answer,
        rf"{OPT}{L}",
        rf"(?:my answer is|the answer is|answer is|it is|i choose|i pick|i select|i will choose"
        rf"|i will pick|i will go with|i go with|i will take|i take|i think it is|i say|choose"
        rf"|pick|select|mark|tick) {OPT}{L}",
        rf"{L} is my answer",
    ),
    *_rules(
        _answer_by_position,
        r"(?:i choose |i pick |i will go with |i will take |choose |pick )?(?:the )?"
        r"(?P<o>first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th) (?:one|option|answer)",
        r"option (?P<o>[1-5])",
    ),
    *_rules(
        _change,
        rf"(?:actually|make it|make that|change it to|change my answer to|change to) {OPT}{L}",
        rf"change (?:my answer for |the answer for |answer for |answer to )?"
        rf"(?:question |number )?{N} to {OPT}{L}",
    ),
    *_rules(
        _clear,
        r"(?:clear|remove|delete|erase|undo) (?:my |the )?answer|unanswer",
        rf"(?:clear|remove|delete|erase|undo) (?:my |the )?answer (?:for|to|on|in) {QN}",
        rf"(?:clear|unanswer) (?:question |number ){N}",
    ),
    # Moving.
    *_rules(
        i.Next(),
        r"next|next question|next one|go next|go to the next(?: question| one)?|move on"
        r"|(?:go to |take me to )?the following(?: question| one)?|following question"
        r"|move to the next(?: question| one)?|continue|skip|skip it|skip this(?: one| question)?"
        r"|go forward|forward",
    ),
    *_rules(
        i.Previous(),
        r"previous|previous question|previous one|back|go back|go back one|the one before"
        r"|(?:go to|go back to) the previous(?: question| one)?|back one",
    ),
    *_rules(
        i.First(),
        r"(?:go to |take me to |start from |start at )?(?:the )?(?:first question|beginning)"
        r"|start again|start from the start",
    ),
    *_rules(
        i.Last(),
        r"(?:go to |take me to |jump to )(?:the )?(?:last|final) question"
        r"|(?:the )?final question|(?:go to )?the end",
    ),
    *_rules(
        i.NextUnanswered(),
        r"(?:go to |take me to |skip to |jump to )?(?:the )?(?:next unanswered(?: question| one)?"
        r"|next (?:question|one) (?:that )?i (?:have not|did not) answer(?:ed)?)",
    ),
    *_rules(
        _go_to,
        rf"(?:go|take me|bring me|carry me|move|jump|skip|switch|turn)(?: back)? to {QN}",
        rf"(?:question|number) {N}",
        N,
        rf"(?:start|begin) (?:from|at|with) {QN}",
        rf"(?:open|read) (?:question|number) {N}",
    ),
    *_rules(
        _section,
        rf"(?:go to |take me to |move to |jump to |switch to )?(?:the )?section {N}",
    ),
    # "Start" is what the welcome asks for. Reading the current question begins the exam
    # from the introduction, and later only re-reads: it can never confirm or move.
    *_rules(
        i.RepeatQuestion(),
        r"start|begin|start the exam|begin the exam|i am ready|ready|start now",
    ),
    # Hearing again.
    *_rules(
        i.RepeatQuestion(),
        r"repeat|repeat (?:the|that|this) question|repeat it|repeat that|repeat yourself"
        r"|say (?:it|that) again|again|read (?:it|that|the question) again|read the question"
        r"|pardon|pardon me|come again|i did not (?:hear|get|catch) (?:that|it|you)"
        r"|one more time|what was the question|what is the question|repeat question",
    ),
    *_rules(
        i.RepeatOptions(),
        r"(?:repeat|read|say)(?: all)? (?:the )?(?:options|choices)(?: again)?|options|choices"
        r"|what are the (?:options|choices)",
    ),
    *_rules(
        _repeat_option,
        rf"(?:repeat|read|say|read out) (?:option |letter )?{L}(?: again)?",
        rf"what (?:is|was) (?:option |letter ){L}",
        rf"option {L} again",
    ),
    *_rules(_spell_option, rf"(?:spell|spell out|how do you spell) (?:option |letter )?{L}"),
    # Status.
    *_rules(
        i.WhereAmI(),
        r"where am i|which question (?:am i on|is this)|what question (?:am i on|is this)"
        r"|what number is this|current question"
        r"|(?:which|what) (?:question )?number am i(?: on)?",
    ),
    *_rules(
        i.TimeLeft(),
        r"(?:how much )?time(?: is)? left|how much time (?:do i have|have i got|remains"
        r"|is remaining)(?: left)?|how many minutes(?: do i have)?(?: left)?|time remaining"
        r"|remaining time|how long (?:do i have|have i got)(?: left)?|what is the time left"
        r"|how many minutes (?:remain|remaining|are remaining)|how much time remains",
    ),
    *_rules(
        i.CountAnswered(),
        r"how many (?:questions )?(?:have i|did i) (?:answered|answer|done)"
        r"|how many (?:questions )?(?:are|have been) answered|how many have i done",
    ),
    *_rules(
        i.ListUnanswered(),
        r"(?:which|what) (?:questions |ones |numbers )?(?:have i not|did i not|did i) "
        r"(?:answered|answer|skip|skipped|miss|missed)|(?:list|read|tell me)(?: the)? "
        r"unanswered(?: questions| ones)?|unanswered(?: questions)?|what did i skip"
        r"|which (?:ones|questions) are (?:left|remaining|unanswered)",
    ),
    *_rules(
        i.ReviewAnswers(args=i.MaybeQuestionArgs(question=None)),
        r"review(?: my)? answers|(?:read|go through|check|skim through|tell me|list)(?: all)?"
        r" my answers|what (?:are|were) my answers|read back my answers",
    ),
    *_rules(
        _review,
        rf"what did i (?:put|choose|pick|answer|select|mark)(?: for| in| on)? {QN}",
        rf"what is my answer (?:for|to|on) {QN}",
        rf"review {QN}",
    ),
    # Listening comfort.
    *_rules(
        i.Faster(),
        r"faster|(?:speak|talk|read|go) faster|speed up|(?:you are )?too slow"
        r"|(?:you are )?(?:talking|speaking|reading) too slow(?:ly)?|increase(?: the)? speed"
        r"|quicker|(?:speak|talk) quicker",
    ),
    *_rules(
        i.Slower(),
        r"slower|(?:speak|talk|read|go) slower|slow down|(?:you are )?too fast"
        r"|(?:you are )?(?:talking|speaking|reading) too fast"
        r"|(?:reduce|decrease)(?: the)? speed|more slowly",
    ),
    *_rules(
        i.NormalSpeed(),
        r"normal speed|(?:reset|default)(?: the)? speed|speak normally|back to normal(?: speed)?",
    ),
    *_rules(
        i.LouderCues(),
        r"(?:louder|increase(?: the)?|turn up(?: the)?) (?:sounds|beeps|tones|cues)"
        r"|(?:sounds|beeps|tones|cues) louder",
    ),
    *_rules(
        i.QuieterCues(),
        r"(?:quieter|softer|reduce(?: the)?|decrease(?: the)?|turn down(?: the)?)"
        r" (?:sounds|beeps|tones|cues)|(?:sounds|beeps|tones|cues) (?:quieter|softer)",
    ),
    # Finishing.
    *_rules(
        i.Submit(),
        r"submit|submit (?:my |the )?(?:exam|test|answers|paper)"
        r"|i am (?:done|finished|through)(?: with the (?:exam|test))?"
        r"|i have (?:finished|completed)(?: (?:the|my) (?:exam|test))?(?: i want to submit)?"
        r"|finish(?: the| my)? (?:exam|test)|end(?: the| my)? (?:exam|test)",
    ),
    *_rules(
        i.Help(),
        r"help|what can i say|what (?:can|should) i (?:say|do)|commands|instructions"
        r"|how does this work",
    ),
]

# Requests the service must refuse, wherever they appear in the sentence.
_OUT_OF_SCOPE: Final = re.compile(
    r"\b(?:correct answer|right answer|the answer to|answer to (?:this|that|question)"
    r"|which (?:one|option|answer|letter) is (?:correct|right|true|the answer)"
    r"|is (?:it|this|that|option [a-e]|[a-e]) (?:correct|right|the answer)"
    r"|am i (?:correct|right)|hint|clue|explain|explanation|what does .+ mean|meaning of"
    r"|define|definition|solve|work (?:it|this) out|tell me the answer|give me the answer"
    r"|help me (?:answer|solve|with)|what is the answer"
    r"|ignore (?:all |your |the |previous |prior )*(?:instructions|rules)"
    r"|you are now|system prompt|pretend|act as|role ?play|developer mode|jailbreak"
    r"|new instructions|forget (?:your|the|all)"
    # Asking the service to choose, judge or narrow down.
    r"|(?:would|should|do|will|can) (?:you|i) (?:choose|pick|select|go with|recommend|suggest)"
    r"|(?:you|i) think is (?:best|right|correct)|recommend|most likely|likely answer"
    r"|is my (?:answer|choice) (?:correct|right|wrong|good)|got (?:it|this|that|them) right"
    r"|get (?:it |this |that |any )?right|which of (?:them|these|the options) is"
    r"|eliminate|rule out|narrow (?:it )?down|fifty fifty|reveal|the answers|score|grade"
    r"|mark my|for all (?:the )?questions"
    # Claims of authority, games and hypotheticals.
    r"|i am (?:the |an |a )?(?:developer|invigilator|examiner|admin|administrator|teacher)"
    r"|(?:invigilator|examiner|teacher) (?:says|said|told)|debug|you are dan|do anything now"
    r"|no restrictions|unrestricted|play a game|lets play|hypothetical(?:ly)?|if you knew"
    r"|grandmother|grandma|repeat after me|translate|has confirmed|already confirmed"
    # Text shaped like the model's own input or output: only an injection looks like that.
    r"|utterance|assistant|system|args|json|free text|your instructions|from now on)\b"
)

_NO_CONTEXT: Final = RulesContext()

_ONE_THAT_SAYS: Final = re.compile(
    r"(?:the )?(?:one|option|answer) (?:that says|that reads|with|saying|which says) (?P<t>.+)"
)
_CHOOSING: Final = re.compile(
    r"(?:i choose|i pick|i will go with|i go with|choose|pick|my answer is|the answer is)"
    r" (?P<t>.+)"
)

_SECTION_ALIASES: Final = {"maths": "mathematics", "math": "mathematics"}
_SECTION_PHRASE: Final = re.compile(
    r"(?:go to |take me to |move to |jump to |switch to |start )?(?:the )?(?P<s>.+?)"
    r"(?: section| part| questions)?"
)
_SECTION_STOPWORDS: Final = frozenset({"use", "of", "and", "the", "for", "in", "paper", "part"})


def names_option(text: str, choice: OptionLetter, option_count: int | None) -> bool:
    """Whether the sentence itself names ``choice``: by letter ("probably B"), by position
    ("the fourth one", "option number four") or as "the last one".

    The language model may recognise an answer, but never choose one: it never sees the
    question, so a letter it picked would be a guess the candidate might take for a hint.
    """
    position = OPTION_LETTERS.index(choice) + 1
    for word in normalise(text).split():
        if _LETTER_WORDS.get(word) == choice or _ORDINALS.get(word) == position:
            return True
        if word == str(position) or (word in ("last", "final") and position == option_count):
            return True
    return False


def match(text: str, ctx: RulesContext = _NO_CONTEXT) -> i.Intent | None:
    """The intent for ``text``, or None if the rules do not recognise it."""
    if len(text) > MAX_RULE_CHARS:
        return None
    said = normalise(text)
    if not said:
        return None
    for pattern, build in _RULES:
        if (m := pattern.fullmatch(said)) is not None:
            return build(m)
    if (by_text := _match_option_text(said, ctx)) is not None:
        return by_text
    if (by_section := _match_section(said, ctx)) is not None:
        return by_section
    if _OUT_OF_SCOPE.search(said):
        return i.NotAllowed()
    return None


def _match_option_text(said: str, ctx: RulesContext) -> i.Intent | None:
    """Choose an option by saying its text, exactly ("photosynthesis"), after a choosing
    phrase ("I'll go with photosynthesis"), or as a distinctive whole-word part of it after
    "the one that says ..."."""
    if not ctx.option_texts:
        return None
    options = [{normalise(t) for t in texts} for texts in ctx.option_texts]
    hits = [k for k, texts in enumerate(options) if said in texts]
    if not hits and (m := _CHOOSING.fullmatch(said)) is not None:
        hits = [k for k, texts in enumerate(options) if m["t"] in texts]
    if not hits and (m := _ONE_THAT_SAYS.fullmatch(said)) is not None:
        phrase = m["t"]
        hits = [k for k, texts in enumerate(options) if phrase in texts]
        if not hits and len(phrase) >= 4:
            word = re.compile(rf"\b{re.escape(phrase)}\b")
            hits = [k for k, texts in enumerate(options) if any(word.search(t) for t in texts)]
    if len(hits) != 1:
        return None
    return i.Answer(args=i.ChoiceArgs(choice=OPTION_LETTERS[hits[0]]))


def _match_section(said: str, ctx: RulesContext) -> i.Intent | None:
    """Go to a section by its title ("go to the use of english section") or a distinctive
    word of it ("physics", "maths")."""
    if not ctx.section_titles or (m := _SECTION_PHRASE.fullmatch(said)) is None:
        return None
    name = _SECTION_ALIASES.get(m["s"], m["s"])
    titles = [normalise(t) for t in ctx.section_titles]
    hits = [k for k, t in enumerate(titles) if name == t]
    if not hits and name not in _SECTION_STOPWORDS and len(name) >= 4:
        hits = [k for k, t in enumerate(titles) if name in t.split()]
    if len(hits) != 1:
        return None
    return i.GoToSection(args=i.SectionArgs(section=hits[0] + 1))
