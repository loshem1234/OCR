"""Collation of two independent transcriptions ("witnesses") of the same printed lines.

Design rules (see README → Scholarly Greek profile):
  * Inputs are lists of printed lines from each engine. Every difference stays traceable to a line.
  * Comparison is on NFC text, so canonically-equivalent spellings (precomposed vs decomposed,
    oxia vs tonos) are NOT reported as disagreements.
  * Differences are classified (accent, breathing, iota subscript, letter, punctuation, editorial sign,
    omission, addition, duplication, line omission, reordering, word division, script artifact …).
  * Agreement between engines is reported as *agreement*, never as verification.
  * Nothing here chooses a reading: items carry both readings and status "unresolved".
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from .greek import (ACCENTS, BREATHINGS, Cluster, clusters, is_editorial, is_punct, mark_name, mixed_script_words, nfc,
                    strip_marks)

REF_TOKEN = re.compile(r"^⟦[^⟧]*⟧$")
APOSTROPHES = str.maketrans({"\u2019": "\u2019", "\u1fbd": "\u2019", "'": "\u2019", "\u02bc": "\u2019", "\u1fbf": "\u2019"})
HYPHENS = "-‐­¬⸗"

MINOR = {"apostrophe-form", "word-division"}


@dataclass
class Token:
    text: str
    key: str
    line: int
    start: int
    end: int
    idx: int = 0


def _loose(s: str) -> str:
    return nfc(s).translate(APOSTROPHES)


def tokenize(lines: list[str]) -> tuple[list[Token], int]:
    toks, refs = [], 0
    for li, line in enumerate(lines):
        line = nfc(line)
        for m in re.finditer(r"\S+", line):
            t = m.group(0)
            if REF_TOKEN.match(t):  # ⟦17a⟧ reference markers are navigation, not text
                refs += 1
                continue
            toks.append(Token(t, _loose(t), li, m.start(), m.end(), len(toks)))
    return toks, refs


def _joined(tokens: list[Token]) -> str:
    """Tokens joined without spaces and without line-end hyphens: detects pure word-division differences."""
    s = "".join(t.key for t in tokens)
    return "".join(ch for ch in s if ch not in HYPHENS)


def _span(t: Token) -> dict:
    return {"text": t.text, "line": t.line, "start": t.start, "end": t.end}


def _run(tokens: list[Token]) -> dict | None:
    if not tokens:
        return None
    return {"text": " ".join(t.text for t in tokens), "line": tokens[0].line, "line_end": tokens[-1].line,
            "start": tokens[0].start, "end": tokens[-1].end}


def classify_pair(a: str, b: str) -> tuple[str, list[dict]]:
    """Classify the difference between two words that occupy the same position."""
    a, b = nfc(a), nfc(b)
    if a == b:
        return "same", []
    if _loose(a) == _loose(b):
        return "apostrophe-form", []
    if (mixed_script_words(a) or mixed_script_words(b)):
        return "script-artifact", [{"a": a, "b": b, "note": "word mixes Greek with Latin/Cyrillic letters"}]
    ca, cb = clusters(a), clusters(b)
    if strip_marks(a).lower() == strip_marks(b).lower() and len(ca) == len(cb):
        kinds, details = set(), []
        for i, (x, y) in enumerate(zip(ca, cb)):
            if x.base != y.base:
                kinds.add("case")
            mx, my = Counter(mark_name(m) for m in x.marks), Counter(mark_name(m) for m in y.marks)
            if mx != my:
                missing, extra = sorted((mx - my).elements()), sorted((my - mx).elements())
                for k in set(missing) | set(extra):
                    kinds.add("accent" if k in ACCENTS else "breathing" if k in BREATHINGS
                              else "iota-subscript" if k == "iota_subscript" else "diaeresis" if k == "diaeresis"
                              else "other-diacritic")
                details.append({"cluster": i, "a": x.text, "b": y.text, "only_in_a": missing, "only_in_b": extra})
            elif x.base != y.base:
                details.append({"cluster": i, "a": x.text, "b": y.text, "case": True})
        if len(kinds) == 1:
            return next(iter(kinds)), details
        return "diacritics", details
    # Letters differ — is it only punctuation / editorial signs?
    core_a = "".join(ch for ch in a if not (is_punct(ch) or is_editorial(ch)))
    core_b = "".join(ch for ch in b if not (is_punct(ch) or is_editorial(ch)))
    if nfc(core_a) == nfc(core_b):
        ed = any(is_editorial(ch) for ch in a + b if (a.count(ch) != b.count(ch)))
        return ("editorial-sign" if ed else "punctuation"), [{"a": a, "b": b}]
    if nfc(core_a) != nfc(core_b) and strip_marks(core_a).lower() == strip_marks(core_b).lower():
        # same letters once punctuation is set aside, but diacritics differ too
        sub, det = classify_pair(core_a, core_b)
        return ("punctuation+" + sub if sub != "same" else "punctuation"), det
    return "letter", _letter_details(ca, cb)


def _letter_details(ca: list[Cluster], cb: list[Cluster]) -> list[dict]:
    sm = SequenceMatcher(None, [c.text for c in ca], [c.text for c in cb], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            out.append({"op": tag, "a": "".join(c.text for c in ca[i1:i2]), "b": "".join(c.text for c in cb[j1:j2]),
                        "a_clusters": [i1, i2], "b_clusters": [j1, j2]})
    return out


def _division_prefix(A: list[Token], B: list[Token]) -> tuple[int, int] | None:
    """Smallest (ka, kb) such that the first ka words of A spell the same letters as the first kb of B."""
    if A[0].key == B[0].key:
        return 1, 1
    for ka in range(1, 4):
        for kb in range(1, 4):
            if (ka, kb) != (1, 1) and ka <= len(A) and kb <= len(B) and _joined(A[:ka]) == _joined(B[:kb]):
                return ka, kb
    return None


def _pair_runs(A: list[Token], B: list[Token]):
    """Inside a replaced block of unequal length, pair similar words; leftovers are omissions/additions."""
    sm = SequenceMatcher(None, [strip_marks(t.key).lower() for t in A], [strip_marks(t.key).lower() for t in B],
                         autojunk=False)
    pairs, only_a, only_b = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal",) or (tag == "replace" and i2 - i1 == j2 - j1):
            pairs.extend(zip(A[i1:i2], B[j1:j2]))
        else:
            only_a.extend(A[i1:i2])
            only_b.extend(B[j1:j2])
    return pairs, only_a, only_b


def collate(a_lines: list[str], b_lines: list[str], a_name: str = "tesseract", b_name: str = "gemini") -> dict:
    ta, refs_a = tokenize(a_lines)
    tb, refs_b = tokenize(b_lines)
    items: list[dict] = []
    agree = 0

    def add(category: str, a_tok=None, b_tok=None, details=None):
        items.append({
            "id": len(items) + 1, "category": category,
            "severity": "minor" if category in MINOR else "substantive",
            "a": a_tok, "b": b_tok, "details": details or [], "status": "unresolved", "proposal": None,
        })

    def omission_items(toks: list[Token], side: str, other_lines: int):
        """Group missing tokens by line; a fully missing line becomes one line-omission item."""
        lines_of_side = a_lines if side == "a" else b_lines
        by_line: dict[int, list[Token]] = {}
        for t in toks:
            by_line.setdefault(t.line, []).append(t)
        for li, group in by_line.items():
            full_line_tokens = [m.group(0) for m in re.finditer(r"\S+", nfc(lines_of_side[li]))
                                if not REF_TOKEN.match(m.group(0))]
            cat = "line-omission" if len(group) == len(full_line_tokens) else "omission"
            # omission = present in this witness, absent from the other
            if side == "a":
                add(cat if cat == "line-omission" else "omission", _run(group), None,
                    [{"note": f"only in {a_name}"}])
            else:
                add("line-addition" if cat == "line-omission" else "addition", None, _run(group),
                    [{"note": f"only in {b_name}"}])

    sm = SequenceMatcher(None, [t.key for t in ta], [t.key for t in tb], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        A, B = ta[i1:i2], tb[j1:j2]
        if tag == "equal":
            for x, y in zip(A, B):
                agree += 1
                if x.text != y.text:  # loose keys equal, raw differs → apostrophe form only
                    add("apostrophe-form", _span(x), _span(y))
            continue
        if A and B and _joined(A) == _joined(B):
            add("word-division", _run(A), _run(B), [{"note": "same letters, different spacing or line-end hyphenation"}])
            continue
        # Peel off leading word-division units (e.g. "πλῆ- θος," vs "πλῆθος,") before pairing words.
        while A and B:
            unit = _division_prefix(A, B)
            if not unit:
                break
            ka, kb = unit
            if ka == 1 and kb == 1:
                agree += 1
            else:
                add("word-division", _run(A[:ka]), _run(B[:kb]),
                    [{"note": "same letters, different spacing or line-end hyphenation"}])
            A, B = A[ka:], B[kb:]
        if not A and not B:
            continue
        if A and B and len(A) == len(B):
            pairs, only_a, only_b = list(zip(A, B)), [], []
        elif A and B:
            pairs, only_a, only_b = _pair_runs(A, B)
        else:
            pairs, only_a, only_b = [], list(A), list(B)
        for x, y in pairs:
            cat, det = classify_pair(x.text, y.text)
            if cat == "same":
                agree += 1
            else:
                add(cat, _span(x), _span(y), det)
        if only_a:
            omission_items(only_a, "a", len(b_lines))
        if only_b:
            # duplication: B repeats the words immediately before it
            k, j0 = len(only_b), only_b[0].idx
            keys = [t.key for t in only_b]
            prev = [t.key for t in tb[max(0, j0 - k):j0]]
            nxt = [t.key for t in tb[j0 + k:j0 + 2 * k]]
            if prev == keys or nxt == keys:
                add("duplication", None, _run(only_b), [{"note": f"{b_name} repeats adjacent words"}])
            else:
                omission_items(only_b, "b", len(a_lines))

    _detect_reordering(items)
    for it in items:
        it["id"] = items.index(it) + 1
    counts = Counter(it["category"] for it in items)
    substantive = sum(1 for it in items if it["severity"] == "substantive")
    denom = max(len(ta), len(tb)) or 1
    return {
        "witnesses": {"a": a_name, "b": b_name},
        "tokens": {"a": len(ta), "b": len(tb), "agreeing": agree},
        "agreement": round(agree / denom, 5),
        "lines": {"a": len(a_lines), "b": len(b_lines)},
        "reference_markers": {"a": refs_a, "b": refs_b},
        "counts": dict(counts),
        "substantive": substantive,
        "status": "engines-agree" if substantive == 0 and len(ta) == len(tb) else "engines-disagree",
        "items": items,
    }


def _detect_reordering(items: list[dict]) -> None:
    """An omission in A whose text appears as an addition in B (or vice versa) is a reordering."""
    oms = [it for it in items if it["category"] in ("omission", "line-omission") and it["a"]]
    adds = [it for it in items if it["category"] in ("addition", "line-addition") and it["b"]]
    used = set()
    for o in oms:
        for a in adds:
            if id(a) in used:
                continue
            if _loose(o["a"]["text"]) == _loose(a["b"]["text"]) and len(o["a"]["text"]) > 3:
                o["category"] = "reordering"
                o["b"] = a["b"]
                o["details"] = [{"note": "same words appear at a different position in the other witness"}]
                used.add(id(a))
                break
    for a in [x for x in items if id(x) in used]:
        items.remove(a)


def disagreement_words(result: dict, side: str = "b") -> list[str]:
    """Words of one witness involved in substantive disagreements (for highlighting in the editor)."""
    out = []
    for it in result.get("items", []):
        if it["severity"] != "substantive":
            continue
        tok = it.get(side)
        if tok and tok.get("text"):
            out.extend(tok["text"].split())
    seen, uniq = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return uniq
