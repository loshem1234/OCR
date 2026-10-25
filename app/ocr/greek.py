"""Unicode utilities for polytonic Greek: normalization policy, grapheme clusters, diacritic names,
script classification and detection of non-transcription artifacts.

Normalization policy (documented in README):
  * Storage and comparison use NFC. NFC is *canonical* normalization: it never strips marks and never
    changes a letter, but it does unify canonically-equivalent spellings. For Greek that means:
      - oxia letters (e.g. U+1F71 ά) become tonos letters (U+03AC ά)        — identical by Unicode definition
      - GREEK ANO TELEIA U+0387 becomes MIDDLE DOT U+00B7
      - GREEK QUESTION MARK U+037E becomes SEMICOLON U+003B
      - combining koronis U+0343 becomes combining comma above U+0313 (smooth breathing shape)
    The untouched engine output is always stored separately (recognitions.raw) for audit.
  * NFKC/NFKD, accent stripping, monotonic conversion and transliteration are never used.
  * `greek_punctuation()` can restore U+0387 / U+037E at export time on request.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ------------------------------------------------------------------ normalization

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s or "")


def greek_punctuation(s: str) -> str:
    """Use the Greek-specific code points for ano teleia and question mark, but only where they follow a
    Greek letter (so Latin/English punctuation is untouched). NFC will undo this, by Unicode design."""
    s = re.sub(r"(?<=[Ͱ-Ͽἀ-῿̀-ͯ])·", "·", s)
    s = re.sub(r"(?<=[Ͱ-Ͽἀ-῿̀-ͯ]);", ";", s)
    return s


# ------------------------------------------------------------------ marks

MARKS = {
    "́": "acute",
    "̀": "grave",
    "͂": "circumflex",
    "̓": "smooth",          # psili (also koronis after NFC)
    "̔": "rough",           # dasia
    "ͅ": "iota_subscript",  # ypogegrammeni
    "̈": "diaeresis",
    "̄": "macron",
    "̆": "breve",
}
DIACRITIC_KINDS = ("acute", "grave", "circumflex", "smooth", "rough", "iota_subscript", "diaeresis", "macron", "breve")
ACCENTS = {"acute", "grave", "circumflex"}
BREATHINGS = {"smooth", "rough"}


def mark_name(c: str) -> str:
    return MARKS.get(c, "other_mark")


@dataclass(frozen=True)
class Cluster:
    base: str
    marks: tuple[str, ...]   # combining characters in NFD canonical order

    @property
    def text(self) -> str:
        return nfc(self.base + "".join(self.marks))

    @property
    def mark_names(self) -> frozenset:
        return frozenset(mark_name(m) for m in self.marks)


def clusters(s: str) -> list[Cluster]:
    """Split into grapheme-like clusters: one base character plus its combining marks (after NFD).
    Precomposed and decomposed spellings of the same letter therefore produce identical clusters."""
    out: list[Cluster] = []
    base, marks = None, []
    for ch in nfd(s):
        if unicodedata.combining(ch) and base is not None:
            marks.append(ch)
            continue
        if base is not None:
            out.append(Cluster(base, tuple(marks)))
        base, marks = ch, []
    if base is not None:
        out.append(Cluster(base, tuple(marks)))
    return out


def strip_marks(s: str) -> str:
    """Base letters only — used for *alignment*, never for output."""
    return "".join(c.base for c in clusters(s))


# ------------------------------------------------------------------ scripts

def script_of(ch: str) -> str:
    o = ord(ch)
    if 0x0370 <= o <= 0x03FF or 0x1F00 <= o <= 0x1FFF:
        return "greek"
    if (0x41 <= o <= 0x5A) or (0x61 <= o <= 0x7A) or 0xC0 <= o <= 0x24F or 0x1E00 <= o <= 0x1EFF:
        return "latin"
    if 0x0590 <= o <= 0x05FF:
        return "hebrew"
    if 0x0600 <= o <= 0x06FF:
        return "arabic"
    if 0x0400 <= o <= 0x04FF:
        return "cyrillic"
    if ch.isdigit():
        return "digit"
    if unicodedata.combining(ch):
        return "mark"
    return "other"


def greek_ratio(s: str) -> float:
    letters = [c for c in nfd(s) if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if script_of(c) == "greek") / len(letters)


# Latin/Cyrillic letters that look like Greek ones — a classic OCR confusion inside Greek words.
LOOKALIKES = set("AaBEeHIiKMNOoPpTXxYyvuωw") | set("АВЕНІКМОРСТХаеорсух")

EDITORIAL = set("⟨⟩〈〉<>[]{}()†‡*⸏⸐⁅⁆⟦⟧|")
PUNCT = set(".,;:··;’᾽'ʼ᾽᾿`΅῭\"«»“”‘—–-‐…!?")


def is_editorial(ch: str) -> bool:
    return ch in EDITORIAL


def is_punct(ch: str) -> bool:
    return ch in PUNCT


# ------------------------------------------------------------------ artifacts

HARD_ARTIFACTS = [
    (re.compile(r"```"), "code fence"),
    (re.compile(r"\"\s*[a-z_]+\"\s*:"), "JSON key"),
    (re.compile(r"^\s*[\[{]\s*\"", re.M), "JSON fragment"),
    (re.compile(r"\\[nrtu]"), "escaped control sequence"),
    (re.compile(r"[�]"), "replacement character"),
    (re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"), "control character"),
    (re.compile(r"(?i)\b(lorem ipsum|todo|fixme|placeholder|insert text|as an ai)\b"), "placeholder text"),
    (re.compile(r"<\/?(?:br|p|div|span|b|i|em|strong)\b[^>]*>", re.I), "HTML tag"),
]


def hard_artifacts(text: str) -> list[str]:
    """Things that are never part of a printed Greek page; a response containing them is rejected."""
    return sorted({label for rx, label in HARD_ARTIFACTS if rx.search(text or "")})


def mixed_script_words(text: str) -> list[str]:
    """Words mixing Greek with Latin/Cyrillic letters (e.g. a Latin 'o' inside a Greek word)."""
    out = []
    for w in re.findall(r"[^\s]+", nfd(text or "")):
        scripts = {script_of(c) for c in w if c.isalpha()}
        if "greek" in scripts and (scripts & {"latin", "cyrillic"}):
            out.append(nfc(w))
    return out
