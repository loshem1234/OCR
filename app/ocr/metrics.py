"""Accuracy metrics against a verified ground truth (used by tools/benchmark.py and the tests).

All comparisons are made after the documented normalization:
  * NFC (canonical equivalence only — see app/ocr/greek.py),
  * whitespace runs collapsed to one space for character/word metrics (line structure is measured
    separately by the completeness metric).
Nothing else is normalized: accents, breathings, iota subscripts, punctuation and brackets all count.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import ratio as _ratio

from .greek import DIACRITIC_KINDS, EDITORIAL, PUNCT, clusters, mark_name, nfc


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", nfc(text)).strip()


def lines_of(text: str) -> list[str]:
    return [l.strip() for l in nfc(text).splitlines() if l.strip()]


@dataclass
class Score:
    chars: int = 0
    char_errors: int = 0
    clusters: int = 0
    cluster_errors: int = 0
    words: int = 0
    word_errors: int = 0
    base_letter_errors: int = 0
    diacritics_total: Counter = field(default_factory=Counter)      # occurrences in ground truth
    diacritics_missing: Counter = field(default_factory=Counter)    # in GT, absent in OCR
    diacritics_extra: Counter = field(default_factory=Counter)      # in OCR, absent in GT
    accent_substitutions: int = 0
    breathing_substitutions: int = 0
    symbols_total: Counter = field(default_factory=Counter)
    symbol_errors: Counter = field(default_factory=Counter)
    gt_lines: int = 0
    missing_lines: int = 0
    extra_lines: int = 0
    duplicated_lines: int = 0

    def add(self, o: "Score") -> None:
        for k, v in asdict(o).items():
            cur = getattr(self, k)
            if isinstance(cur, Counter):
                cur.update(getattr(o, k))
            else:
                setattr(self, k, cur + v)

    def summary(self) -> dict:
        def rate(e, n):
            return round(e / n, 5) if n else None
        dia = {k: {"total": self.diacritics_total[k], "missing": self.diacritics_missing[k],
                   "extra": self.diacritics_extra[k],
                   "error_rate": rate(self.diacritics_missing[k] + self.diacritics_extra[k], self.diacritics_total[k])}
               for k in DIACRITIC_KINDS if self.diacritics_total[k] or self.diacritics_extra[k]}
        sym_total = sum(self.symbols_total.values())
        return {
            "CER": rate(self.char_errors, self.chars),
            "grapheme_ER": rate(self.cluster_errors, self.clusters),
            "WER": rate(self.word_errors, self.words),
            "base_letter_ER": rate(self.base_letter_errors, self.clusters),
            "diacritic_errors": sum(self.diacritics_missing.values()) + sum(self.diacritics_extra.values()),
            "diacritic_ER": rate(sum(self.diacritics_missing.values()) + sum(self.diacritics_extra.values()),
                                 sum(self.diacritics_total.values())),
            "accent_substitutions": self.accent_substitutions,
            "breathing_substitutions": self.breathing_substitutions,
            "diacritics": dia,
            "symbol_ER": rate(sum(self.symbol_errors.values()), sym_total),
            "symbol_errors": dict(self.symbol_errors),
            "chars": self.chars, "clusters": self.clusters, "words": self.words,
            "gt_lines": self.gt_lines, "missing_lines": self.missing_lines,
            "extra_lines": self.extra_lines, "duplicated_lines": self.duplicated_lines,
        }


def _diacritics(gt_cl, hy_cl, s: Score) -> None:
    """Compare marks on letters aligned by their base characters."""
    gt_b = [c.base.lower() for c in gt_cl]
    hy_b = [c.base.lower() for c in hy_cl]
    for c in gt_cl:
        for m in c.marks:
            s.diacritics_total[mark_name(m)] += 1
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(gt_b, hy_b):
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for g, h in zip(gt_cl[i1:i2], hy_cl[j1:j2]):
                gm, hm = Counter(mark_name(m) for m in g.marks), Counter(mark_name(m) for m in h.marks)
                s.diacritics_missing.update(gm - hm)
                s.diacritics_extra.update(hm - gm)
                ga, ha = g.mark_names & {"acute", "grave", "circumflex"}, h.mark_names & {"acute", "grave", "circumflex"}
                if ga and ha and ga != ha:
                    s.accent_substitutions += 1
                gb, hb = g.mark_names & {"smooth", "rough"}, h.mark_names & {"smooth", "rough"}
                if gb and hb and gb != hb:
                    s.breathing_substitutions += 1
        elif tag in ("delete", "replace"):
            for g in gt_cl[i1:i2]:
                s.diacritics_missing.update(mark_name(m) for m in g.marks)
            if tag == "replace":
                for h in hy_cl[j1:j2]:
                    s.diacritics_extra.update(mark_name(m) for m in h.marks)
        elif tag == "insert":
            for h in hy_cl[j1:j2]:
                s.diacritics_extra.update(mark_name(m) for m in h.marks)
    s.base_letter_errors += Levenshtein.distance(gt_b, hy_b)


def _symbols(gt: str, hy: str, s: Score) -> None:
    syms = EDITORIAL | PUNCT
    for ch in gt:
        if ch in syms:
            s.symbols_total[ch] += 1
    for op in Levenshtein.editops(gt, hy):
        a = gt[op.src_pos] if op.tag in ("replace", "delete") else ""
        b = hy[op.dest_pos] if op.tag in ("replace", "insert") else ""
        for ch in {a, b}:
            if ch and ch in syms:
                s.symbol_errors[ch] += 1


def _lines(gt_lines: list[str], hy_lines: list[str], s: Score) -> None:
    """Monotonic line alignment; a GT line counts as present if some OCR line is ≥60% similar."""
    n, m = len(gt_lines), len(hy_lines)
    s.gt_lines += n
    sim = [[_ratio(g, h) for h in hy_lines] for g in gt_lines]
    # DP maximizing number of matched pairs (similarity >= 60), order-preserving.
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = best[i + 1][j + 1] + 1 if sim[i][j] >= 60 else -1
            best[i][j] = max(take, best[i + 1][j], best[i][j + 1])
    matched = best[0][0] if n and m else 0
    s.missing_lines += n - matched
    s.extra_lines += max(0, m - matched)
    s.duplicated_lines += sum(1 for a, b in zip(hy_lines, hy_lines[1:]) if a and _ratio(a, b) >= 90)


def score(gt_text: str, hy_text: str) -> Score:
    s = Score()
    g, h = flat(gt_text), flat(hy_text)
    s.chars = len(g)
    s.char_errors = Levenshtein.distance(g, h)
    gc, hc = clusters(g), clusters(h)
    s.clusters = len(gc)
    s.cluster_errors = Levenshtein.distance([c.text for c in gc], [c.text for c in hc])
    gw, hw = g.split(), h.split()
    s.words = len(gw)
    s.word_errors = Levenshtein.distance(gw, hw)
    _diacritics(gc, hc, s)
    _symbols(g, h, s)
    _lines(lines_of(gt_text), lines_of(hy_text), s)
    return s
