"""Exam text as words a voice can say, for questions that come without ``spoken_text``.

Only what is spoken changes: the ``say`` message keeps the exact exam text for braille
displays and captions. The voice already reads plain numbers, so this covers the symbols
it would skip or misread. It never rewords: a symbol becomes its usual spoken name, and
anything it does not recognise is left for the voice as it is. Writers should still give
``spoken_text`` for anything subtle (chemical formulae, long equations, tables).
"""

import re
from typing import Final

_SUPERSCRIPTS: Final = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUBSCRIPTS: Final = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")

# Symbol -> words, with spaces so neighbours stay separate words.
_SYMBOLS: Final = {
    "×": " times ", "÷": " divided by ", "±": " plus or minus ", "−": " minus ",
    "√": " the square root of ", "π": " pi ", "∞": " infinity ", "≈": " approximately ",
    "≠": " is not equal to ", "≤": " is less than or equal to ",
    "≥": " is greater than or equal to ", "<": " is less than ", ">": " is greater than ",
    "=": " equals ", "°": " degrees", "%": " percent", "∠": " angle ", "△": " triangle ",
    "…": " and so on", "&": " and ", "+": " plus ", "θ": " theta ", "α": " alpha ", "β": " beta ",
    "Δ": " delta ", "μ": " micro ", "Ω": " ohms",
}  # fmt: skip
_CURRENCY: Final = {"₦": "naira", "$": "dollars", "£": "pounds", "€": "euros"}

_POWER: Final = re.compile(r"(?<=\w)([⁰¹²³⁴⁵⁶⁷⁸⁹⁻]+)")
_SUBSCRIPT: Final = re.compile(r"(?<=\w)([₀₁₂₃₄₅₆₇₈₉]+)")
_MONEY: Final = re.compile(r"([₦$£€])\s?(\d[\d,]*(?:\.\d+)?)")
_FRACTION: Final = re.compile(r"(?<![\w/])(\d+)/(\d+)(?![\w/])")


def _power(match: re.Match[str]) -> str:
    exponent = match.group(1).translate(_SUPERSCRIPTS)
    spoken = {"2": "squared", "3": "cubed"}.get(exponent, f"to the power {exponent}")
    return f" {spoken}"


def speakable(text: str) -> str:
    """The words to synthesise for ``text``."""
    text = _MONEY.sub(lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", text)
    text = _POWER.sub(_power, text)
    text = _SUBSCRIPT.sub(lambda m: f" base {m.group(1).translate(_SUBSCRIPTS)}", text)
    text = _FRACTION.sub(lambda m: f"{m.group(1)} over {m.group(2)}", text)
    for symbol, words in _SYMBOLS.items():
        text = text.replace(symbol, words)
    text = re.sub(r"\s+", " ", text).replace(" ,", ",").replace(" .", ".").replace(" ?", "?")
    return text.strip()
