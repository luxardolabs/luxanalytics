"""What of a text renders: the one check for "nothing visible" that settings and forms share."""

import string
import unicodedata

_INVISIBLE_CATEGORIES = {"Cc", "Cf", "Cn", "Co", "Cs", "Me", "Mn"}
# Assigned letters and symbols that draw nothing: the Hangul fillers (U+115F, U+1160, U+3164,
# U+FFA0), the braille blank (U+2800), the object replacement character (U+FFFC) and the musical
# null notehead (U+1D159). Spaces such as U+3000 are whitespace, which `blank` strips.
_FILLERS = {
    "ᅟ",
    "ᅠ",
    "⠀",
    "ㅤ",
    "ﾠ",
    "￼",
    "\U0001d159",
}


def visible(text: str) -> str:
    """The text without what renders as nothing: controls, format characters, unassigned and
    private code points, combining marks and the blank fillers. Whitespace stays."""
    return "".join(
        c
        for c in text
        if c in string.whitespace
        or (unicodedata.category(c) not in _INVISIBLE_CATEGORIES and c not in _FILLERS)
    )


def blank(text: str) -> bool:
    """Nothing but whitespace and invisible characters."""
    return not visible(text).strip()
