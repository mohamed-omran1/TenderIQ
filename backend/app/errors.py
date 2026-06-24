"""Typed API errors mapped to specific status codes.

REQ-001 Usability NFR: error messages must be specific enough to act on
("file too large" vs "unsupported format"), never a generic 500. Each class
here carries a concrete status code and message so the router can raise and
the exception handler renders a precise response.
"""
from __future__ import annotations


class ApiError(Exception):
    """Base. Subclasses set `status_code` and `detail`."""

    status_code: int = 400
    detail: str = "Bad request"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.detail)
        if detail is not None:
            self.detail = detail


class UnsupportedFileType(ApiError):
    status_code = 422
    detail = "Only PDF files are supported."


class FileTooLarge(ApiError):
    status_code = 413
    detail = "File exceeds 50MB limit."


class RateLimited(ApiError):
    status_code = 429
    detail = "Rate limit exceeded."

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Rate limit exceeded.")
        self.retry_after = retry_after_seconds


class QuotaExceeded(ApiError):
    status_code = 429
    detail = "Monthly document upload quota exceeded."


class Unauthorized(ApiError):
    status_code = 401
    detail = "Missing or invalid API key."


class NotFound(ApiError):
    """404 (not 403) for resources outside the caller's tenant — avoids leaking existence."""

    status_code = 404
    detail = "Not found."
