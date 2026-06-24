"""Upload validation — type and size checks before the file touches disk.

REQ-001 says validate by MIME type. The api-security-reviewer skill is stricter:
"extensions lie, validate magic bytes." We do both. A polyglot file claiming
`application/pdf` but not starting with `%PDF-` is rejected here.
"""
from __future__ import annotations

from app.config import get_settings
from app.errors import FileTooLarge, UnsupportedFileType

PDF_MAGIC = b"%PDF-"  # every valid PDF starts with this (optionally with BOM)


def validate_upload(content_type: str | None, body: bytes) -> None:
    """Raise the appropriate ApiError subclass, or return if all checks pass."""
    settings = get_settings()

    # --- Size first (cheapest, and avoids parsing anything huge) ---
    if len(body) > settings.max_upload_bytes:
        raise FileTooLarge(f"File exceeds {settings.max_upload_mb}MB limit.")

    # --- MIME ---
    if content_type is None or content_type.lower() != "application/pdf":
        raise UnsupportedFileType("Only PDF files are supported.")

    # --- Magic bytes (defence against polyglot / mislabelled files) ---
    if not body[:5] == PDF_MAGIC:
        raise UnsupportedFileType("Only PDF files are supported.")


def reject_oversize_declared(declared_size: int | None) -> None:
    """Pre-check the Content-Length/file.size BEFORE buffering the body.

    `validate_upload` reads the full body, so a malicious client declaring a
    huge size would otherwise force us to buffer gigabytes before the size
    check fires. Calling this first lets us reject early. `declared_size` is
    None when the client didn't send Content-Length (chunked) — in that case we
    fall back to the post-read check in `validate_upload`.
    """
    if declared_size is None:
        return
    if declared_size > get_settings().max_upload_bytes:
        raise FileTooLarge(f"File exceeds {get_settings().max_upload_mb}MB limit.")


def sanitize_filename(raw: str | None) -> str:
    """Make a client-supplied filename safe to store and log.

    We never use the client filename on disk (the on-disk name is the server
    UUID), but the raw value lands in the DB and in log lines. Strip control
    chars / path separators / newlines to prevent log injection and confusion.
    """
    if not raw:
        return "tender.pdf"
    # Drop path components (a client may send "C:\\evil\\x.pdf" or "../x").
    name = raw.replace("\\", "/").split("/")[-1]
    # Remove control chars (incl. \n, \r) that could break log parsing.
    name = "".join(ch for ch in name if ch.isprintable())
    name = name.strip() or "tender.pdf"
    # Cap length to keep the column tidy.
    return name[:500]

