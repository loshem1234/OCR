"""Small text helpers shared by the engines and exporters."""
from __future__ import annotations

import re
import unicodedata

LINE_HYPHENS = "-‐­¬⸗"  # hyphen, unicode hyphen, soft hyphen, not-sign, Fraktur double hyphen

SCRIPT_RANGES = {
    "greek": [(0x0370, 0x03FF), (0x1F00, 0x1FFF)],
    "polytonic": [(0x1F00, 0x1FFF)],
    "hebrew": [(0x0590, 0x05FF), (0xFB1D, 0xFB4F)],
    "arabic": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
    "syriac": [(0x0700, 0x074F)],
    "latin": [(0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F), (0x1E00, 0x1EFF)],
}


def script_counts(text: str) -> dict[str, int]:
    counts = {k: 0 for k in SCRIPT_RANGES}
    for ch in text:
        o = ord(ch)
        for name, ranges in SCRIPT_RANGES.items():
            if any(a <= o <= b for a, b in ranges):
                counts[name] += 1
    return counts


def is_rtl(text: str) -> bool:
    c = script_counts(text)
    rtl = c["hebrew"] + c["arabic"] + c["syriac"]
    ltr = c["latin"] + c["greek"]
    return rtl > 0 and rtl >= ltr


def _starts_lower(s: str) -> bool:
    s = s.lstrip()
    return bool(s) and s[0].isalpha() and s[0].islower()


def join_lines(lines: list[str]) -> str:
    """Join OCR lines into one paragraph, rejoining words hyphenated at line ends."""
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out[-1] in LINE_HYPHENS and len(out) > 1 and out[-2].isalpha() and _starts_lower(line):
            out = out[:-1] + line
        else:
            out = out + " " + line
    return out


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


TERMINAL = tuple(".!?;:··;»\"”’)]…")


def ends_sentence(s: str) -> bool:
    s = s.rstrip()
    return not s or s.endswith(TERMINAL)


starts_lower = _starts_lower
