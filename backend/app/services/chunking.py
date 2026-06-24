"""Paragraph-aware chunking tuned for tender documents.

REQ-001 Data Requirements target 500–800 tokens per chunk, paragraph-boundary
aware, with a 1-sentence overlap between adjacent chunks. (This is larger than
the rag-architect skill's 256–512 default; REQ-001 is the contract and wins.
Flagged for re-tuning when retrieval recall is measured in Week 2.)

Token count is approximated by characters: ~3 chars/token is a robust blended
estimate for Arabic + English prose. We don't pull in a tokenizer dependency
for an estimate that's within 10% — chunk boundaries are soft.
"""
from __future__ import annotations

from dataclasses import dataclass

# REQ-001 target: 500–800 tokens. At ~3 chars/token that's ~1500–2400 chars.
# We aim for the midpoint (~600 tok → ~1800 chars) and allow paragraph-aware
# drift within [min, max].
CHARS_PER_TOKEN = 3
TARGET_TOKENS = 600
MIN_TOKENS = 500
MAX_TOKENS = 800
TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN  # 1800
MIN_CHARS = MIN_TOKENS * CHARS_PER_TOKEN        # 1500
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN        # 2400


@dataclass(frozen=True)
class Chunk:
    """A chunk ready to embed and persist. Provenance (page_number) preserved."""

    content: str
    page_number: int
    detected_language: str  # filled in by the caller after creation


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; drop empties and trim."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _approx_sentences(text: str) -> list[str]:
    """Sentence-ish split. Handles both '.' (English) and '۔' / Arabic full stop.

    Good enough for overlap; we are not doing NLP segmentation here.
    """
    # Normalize common sentence terminators across scripts to a sentinel.
    normalized = text.replace("।", ".").replace("۔", ".")
    parts = [s.strip() for s in normalized.split(".")]
    return [s for s in parts if s]


def chunk_page(text: str, page_number: int) -> list[Chunk]:
    """Chunk a single page's text into retrieval-sized units.

    Returns [] for empty/whitespace-only text (the caller uses that to detect
    scanned pages). Chunks never span page boundaries — provenance stays exact.
    """
    if not text or not text.strip():
        return []

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(
                Chunk(
                    content="\n\n".join(current),
                    page_number=page_number,
                    detected_language="",  # filled by caller via language.detect
                )
            )
            current = []
            current_len = 0

    for para in paragraphs:
        para_len = len(para)
        # A single paragraph longer than MAX becomes a chunk on its own; we
        # don't split mid-paragraph (semantic boundary > token target).
        if para_len >= MAX_CHARS:
            flush()
            chunks.append(Chunk(content=para, page_number=page_number, detected_language=""))
            continue

        if current_len + para_len > MAX_CHARS and current_len >= MIN_CHARS:
            # Current chunk is full enough — flush before adding this paragraph.
            flush()

        current.append(para)
        current_len += para_len + 2  # account for the joining "\n\n"

    flush()
    return _apply_overlap(chunks)


def _apply_overlap(chunks: list[Chunk]) -> list[Chunk]:
    """Prepend the previous chunk's last sentence to the current chunk.

    1-sentence overlap prevents losing a clause that straddles a boundary
    (rag-architect skill). We re-detect language per chunk AFTER overlap so the
    stored language reflects the actual content the embedding will see.
    """
    if len(chunks) < 2:
        return chunks

    overlapped: list[Chunk] = [chunks[0]]
    for prev, curr in zip(chunks, chunks[1:]):
        prev_sentences = _approx_sentences(prev.content)
        lead = prev_sentences[-1] if prev_sentences else ""
        if lead:
            new_content = f"{lead}.\n\n{curr.content}"
        else:
            new_content = curr.content
        overlapped.append(
            Chunk(content=new_content, page_number=curr.page_number, detected_language="")
        )
    return overlapped
