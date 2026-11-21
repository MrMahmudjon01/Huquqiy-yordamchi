"""O'zbek kirill ↔ lotin yordamchilari."""
import re

_C2L = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "gʻ", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z",
    "и": "i", "й": "y", "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ў": "oʻ", "ф": "f", "х": "x", "ҳ": "h", "ц": "s", "ч": "ch", "ш": "sh",
    "ъ": "ʼ", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ы": "i", "щ": "sh",
}
CYR_RE = re.compile(r"[Ѐ-ӿ]")
LAT_RE = re.compile(r"[a-zA-Z]")


def is_cyrillic(text):
    return len(CYR_RE.findall(text or "")) > len(LAT_RE.findall(text or ""))


def to_latin(text):
    out = []
    prev = " "
    for ch in text:
        low = ch.lower()
        if low in _C2L:
            rep = _C2L[low]
            if low == "е" and not prev.isalpha():  # so'z boshida е → ye
                rep = "ye"
            if ch.isupper() and rep:
                rep = rep[0].upper() + rep[1:]
            out.append(rep)
        else:
            out.append(ch)
        prev = ch
    return "".join(out)
