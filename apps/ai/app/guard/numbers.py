"""The numbers in a spoken French sentence: « quatorze euros quatre-vingt-dix » → 14.9, « quarante-deux kilos » → 42.

The voice agent says numbers in words (prompts: « nombres en toutes lettres »). The Garde-fou uses them to point at
the ERP value a sentence repeats (the ✓ shows « 14,90 € » next to the source), never to decide: Jev decides.
"""

import re

_UNITS = {
    "zéro": 0, "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
    "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
}  # fmt: skip
_TOKEN = re.compile(r"\d+(?:[.,]\d+)?|[a-zàâçéèêëîïôûùüÿœ]+", re.I)


def _words_value(words: list[str]) -> int:
    total, cur = 0, 0
    for w in words:
        if w in ("cent", "cents"):
            cur = (cur or 1) * 100
        elif w == "mille":
            total += (cur or 1) * 1000
            cur = 0
        elif w in ("vingt", "vingts") and cur % 100 == 4:  # quatre-vingt(s)
            cur += 76
        else:
            cur += _UNITS[w]
    return total + cur


def numbers_in(text: str) -> list[float]:
    """Every number said in the sentence, in order. Money « X euros Y » → X.Y; « et demi » → +0.5; « virgule » decimals."""
    tokens = [t.lower() for t in _TOKEN.findall(text.replace("-", " "))]
    out: list[float] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t[0].isdigit():
            out.append(float(t.replace(",", ".")))
            i += 1
            continue
        if (t not in _UNITS and t not in ("cent", "cents", "mille")) or (t == "cent" and i and tokens[i - 1] == "pour"):
            i += 1
            continue
        if t in ("un", "une") and not _is_number_context(tokens, i):  # « un instant », « une commande »
            i += 1
            continue
        run: list[str] = []
        while i < len(tokens) and (tokens[i] in _UNITS or tokens[i] in ("cent", "cents", "mille")
                                   or (tokens[i] == "et" and run and i + 1 < len(tokens) and tokens[i + 1] in ("un", "une", "onze"))):  # fmt: skip
            if tokens[i] != "et":
                run.append(tokens[i])
            i += 1
        value: float = _words_value(run)
        if i + 1 < len(tokens) and tokens[i] == "et" and tokens[i + 1] in ("demi", "demie"):
            value += 0.5
            i += 2
        elif (i + 2 < len(tokens) and tokens[i].rstrip("s") in UNITS_WORDS and tokens[i + 1] == "et"
              and tokens[i + 2] in ("demi", "demie")):  # fmt: skip  # « soixante et un kilos et demi »
            value += 0.5
            i += 3
        elif i + 1 < len(tokens) and tokens[i] == "virgule":
            j, dec = i + 1, []
            while j < len(tokens) and tokens[j] in _UNITS:
                dec.append(tokens[j])
                j += 1
            if dec:
                d = _words_value(dec)
                value = float(f"{int(value)}.{d}")
                i = j
        elif i < len(tokens) and tokens[i] in ("euro", "euros"):
            j, cents = i + 1, []
            if j < len(tokens) and tokens[j] == "et":
                j += 1
            while j < len(tokens) and tokens[j] in _UNITS:
                cents.append(tokens[j])
                j += 1
            if cents and (c := _words_value(cents)) < 100:
                value += c / 100
                i = j
        out.append(round(value, 2))
    return out


def _is_number_context(tokens: list[str], i: int) -> bool:
    """« un » / « une » counts as a number only before a unit or another number word (« un kilo », « une bête »)."""
    nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
    return nxt in _UNITS or nxt.rstrip("s") in UNITS_WORDS


UNITS_WORDS = {"kilo", "kg", "gramme", "pièce", "piece", "carton", "colis", "barquette", "bête", "bete", "broche", "litre", "euro",
               "caisse", "carcasse", "agneau", "palette", "sac"}  # fmt: skip


def fmt_number(x: float) -> str:
    """14.9 → « 14,90 » (two decimals when not whole), 42.0 → « 42 »."""
    return f"{x:g}" if x == int(x) else f"{x:.2f}".replace(".", ",")
