"""An examination number spelled aloud: "two zero two six, zero zero zero one, P for Papa".

Letters are best said as words (the NATO alphabet, or "P for Papa"): a recogniser hears
"pee", "tea" and "see" as other words, but rarely mistakes "Papa". Anything not
recognised as a digit, a letter or a filler word makes the whole sentence unreadable,
so a half-heard number is asked for again rather than guessed.
"""

import re
from typing import Final

NATO: Final[dict[str, str]] = {
    "alfa": "A", "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D", "echo": "E",
    "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I", "juliet": "J", "juliett": "J",
    "kilo": "K", "lima": "L", "mike": "M", "november": "N", "oscar": "O", "papa": "P",
    "quebec": "Q", "romeo": "R", "sierra": "S", "tango": "T", "uniform": "U", "victor": "V",
    "whiskey": "W", "whisky": "W", "xray": "X", "yankee": "Y", "zulu": "Z",
}  # fmt: skip
SPOKEN_LETTER: Final[dict[str, str]] = {letter: word.capitalize() for word, letter in NATO.items()}
SPOKEN_LETTER.update({"A": "Alpha", "J": "Juliet", "W": "Whiskey"})

# How a recogniser writes a letter said on its own.
_LETTER_NAMES: Final[dict[str, str]] = {
    "ay": "A", "bee": "B", "be": "B", "see": "C", "sea": "C", "cee": "C", "dee": "D",
    "ef": "F", "eff": "F", "gee": "G", "aitch": "H", "jay": "J", "kay": "K", "el": "L",
    "em": "M", "en": "N", "pee": "P", "pea": "P", "queue": "Q", "cue": "Q", "kew": "Q",
    "ar": "R", "are": "R", "es": "S", "ess": "S", "tee": "T", "tea": "T", "vee": "V",
    "ex": "X", "zed": "Z", "zee": "Z",
}  # fmt: skip
_DIGITS: Final[dict[str, str]] = {
    "zero": "0", "oh": "0", "nought": "0", "naught": "0", "nil": "0", "one": "1", "two": "2",
    "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
}  # fmt: skip
_TEENS: Final[dict[str, str]] = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
}  # fmt: skip
_TENS: Final[dict[str, str]] = {
    "twenty": "2", "thirty": "3", "forty": "4", "fifty": "5", "sixty": "6", "seventy": "7",
    "eighty": "8", "ninety": "9",
}  # fmt: skip
_REPEAT: Final = {"double": 2, "triple": 3}
_FILLER: Final = {
    "my", "the", "is", "it", "its", "number", "examination", "exam", "registration", "reg",
    "no", "and", "then", "um", "uh", "er", "erm", "letter", "digit", "capital",
}  # fmt: skip
_MIN_LENGTH: Final = 4
_MAX_LENGTH: Final = 20


def _tokens(text: str) -> list[str]:
    text = text.lower().replace("x-ray", "xray").replace("it's", "it is")
    return re.findall(r"[a-z]+|\d+", text)


def _single(word: str) -> str | None:
    """One word as the characters it stands for, or None."""
    if word.isdigit():
        return word
    if word in _DIGITS:
        return _DIGITS[word]
    if word in _TEENS:
        return _TEENS[word]
    if word in NATO:
        return NATO[word]
    if word in _LETTER_NAMES:
        return _LETTER_NAMES[word]
    if len(word) <= 2:  # "p", or "pr" as a recogniser may join two letters
        return word.upper()
    return None


def spoken_code(text: str) -> str | None:
    """The examination number in ``text``, upper case, or None if it cannot be read."""
    words = [w for w in _tokens(text) if w not in _FILLER]
    out: list[str] = []
    k = 0
    while k < len(words):
        word = words[k]
        # "P for Papa": the word after "for" says which letter was meant.
        if k + 2 < len(words) and words[k + 1] == "for" and words[k + 2] in NATO:
            out.append(NATO[words[k + 2]])
            k += 3
            continue
        if word in _REPEAT and k + 1 < len(words):
            what = _single(words[k + 1])
            if what is None or len(what) != 1:
                return None
            out.append(what * _REPEAT[word])
            k += 2
            continue
        if word in _TENS:
            unit = _DIGITS.get(words[k + 1]) if k + 1 < len(words) else None
            if unit is not None and unit != "0":
                out.append(_TENS[word] + unit)
                k += 2
            else:
                out.append(_TENS[word] + "0")
                k += 1
            continue
        if word == "hundred":
            return None  # "one hundred and five": too easy to mishear as digits
        what = _single(word)
        if what is None:
            return None
        out.append(what)
        k += 1
    code = "".join(out).upper()
    if not _MIN_LENGTH <= len(code) <= _MAX_LENGTH or not code.isalnum():
        return None
    return code


def read_back(code: str) -> str:
    """The code as it is read back for checking: digits one by one, letters as words."""
    return ", ".join(f"{c} for {SPOKEN_LETTER[c]}" if c.isalpha() else c for c in code)
