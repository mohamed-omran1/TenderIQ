"""Per-chunk language detection for Arabic / English / mixed text.

We use a deterministic script-ratio heuristic instead of a probabilistic
library (langdetect). Reasons:
  - langdetect is flaky on short chunks and was never trained for Arabic.
  - Arabic vs English is a *script* distinction, not a subtle linguistic one —
    the Unicode block of each character tells you the language directly.
  - Deterministic → reproducible eval results (senior-qa skill).

Threshold: >70% of non-space letters in one script → that script's language;
otherwise `mixed`. REQ-001 aggregates this into primary_language as
ar | en | bilingual (bilingual when neither script exceeds 70% across the doc).
"""
from __future__ import annotations

# Unicode ranges. Arabic spans several blocks; the main one (0x0600-0x06FF)
# covers the vast majority of real text. We include the Arabic Supplement and
# Extended ranges to be thorough.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)

# Latin basic + Extended Latin. Good enough for EN tender docs.
_LATIN_RANGES = (
    (0x0041, 0x005A),   # A-Z
    (0x0061, 0x007A),   # a-z
    (0x00C0, 0x024F),   # Latin Extended-A/B
)


def _is_arabic(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _ARABIC_RANGES)


def _is_latin(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _LATIN_RANGES)


def detect_language(text: str) -> str:
    """Classify a chunk as 'ar', 'en', or 'mixed'.

    Returns 'mixed' for near-empty text too — we can't claim a language we
    can't see. The ingestor treats 0-chunk / all-mixed docs as a signal that
    extraction failed (see services/pdf.py scanned-page detection).
    """
    arabic = 0
    latin = 0
    for ch in text:
        if _is_arabic(ch):
            arabic += 1
        elif _is_latin(ch):
            latin += 1

    total = arabic + latin
    if total == 0:
        return "mixed"

    if arabic / total > 0.70:
        return "ar"
    if latin / total > 0.70:
        return "en"
    return "mixed"


def primary_language(lang_counts: dict[str, int]) -> str:
    """Aggregate per-chunk language counts into the tenders.primary_language value.

    REQ-001 step 9: dominant language across chunks; 'bilingual' if neither
    exceeds 70%. `lang_counts` is e.g. {'ar': 40, 'en': 5, 'mixed': 3}.
    """
    total = sum(lang_counts.values())
    if total == 0:
        # No chunks extracted (scanned PDF) — caller already failed the run.
        return "bilingual"

    ar = lang_counts.get("ar", 0)
    en = lang_counts.get("en", 0)
    # 'mixed' chunks count half toward each script's dominance.
    mixed = lang_counts.get("mixed", 0)
    ar_share = (ar + mixed * 0.5) / total
    en_share = (en + mixed * 0.5) / total

    if ar_share > 0.70:
        return "ar"
    if en_share > 0.70:
        return "en"
    return "bilingual"
