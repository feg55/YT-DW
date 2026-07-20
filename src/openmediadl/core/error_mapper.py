"""Map low-level failures to concise, actionable user-facing errors."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class ErrorCategory(StrEnum):
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    UNSUPPORTED_URL = "unsupported_url"
    PRIVATE_CONTENT = "private_content"
    AUTHENTICATION_REQUIRED = "authentication_required"
    RATE_LIMITED = "rate_limited"
    HTTP_FORBIDDEN = "http_forbidden"
    NETWORK_TIMEOUT = "network_timeout"
    DISK_FULL = "disk_full"
    PERMISSION_DENIED = "permission_denied"
    OUTPUT_LOCKED = "output_locked"
    THUMBNAIL_CONVERSION_FAILED = "thumbnail_conversion_failed"
    METADATA_WRITING_FAILED = "metadata_writing_failed"
    MEDIA_UNAVAILABLE = "media_unavailable"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MappedError:
    category: ErrorCategory
    message: str
    technical: str


_TEXT_PATTERNS: Final[tuple[tuple[tuple[str, ...], ErrorCategory, str], ...]] = (
    (
        ("ffmpeg", "not found"),
        ErrorCategory.FFMPEG_NOT_FOUND,
        "FFmpeg was not found. Configure its installation directory.",
    ),
    (
        ("ffprobe", "not found"),
        ErrorCategory.FFPROBE_NOT_FOUND,
        "FFprobe was not found. Configure its installation directory.",
    ),
    (("unsupported url",), ErrorCategory.UNSUPPORTED_URL, "This URL is not supported."),
    (("private video",), ErrorCategory.PRIVATE_CONTENT, "This content is private."),
    (
        ("private", "sign in"),
        ErrorCategory.AUTHENTICATION_REQUIRED,
        "Sign-in or browser cookies are required.",
    ),
    (
        ("login required",),
        ErrorCategory.AUTHENTICATION_REQUIRED,
        "Sign-in or browser cookies are required.",
    ),
    (
        ("sign in to confirm",),
        ErrorCategory.AUTHENTICATION_REQUIRED,
        "Sign-in or browser cookies are required.",
    ),
    (
        ("cookies", "required"),
        ErrorCategory.AUTHENTICATION_REQUIRED,
        "Browser cookies are required.",
    ),
    (
        ("http error 429",),
        ErrorCategory.RATE_LIMITED,
        "The service is rate limiting requests. Try again later.",
    ),
    (
        ("too many requests",),
        ErrorCategory.RATE_LIMITED,
        "The service is rate limiting requests. Try again later.",
    ),
    (
        ("http error 403",),
        ErrorCategory.HTTP_FORBIDDEN,
        "The media server denied access (HTTP 403). Try again later or use browser cookies.",
    ),
    (
        ("403", "forbidden"),
        ErrorCategory.HTTP_FORBIDDEN,
        "The media server denied access (HTTP 403). Try again later or use browser cookies.",
    ),
    (("timed out",), ErrorCategory.NETWORK_TIMEOUT, "The network request timed out."),
    (("timeout",), ErrorCategory.NETWORK_TIMEOUT, "The network request timed out."),
    (("no space left",), ErrorCategory.DISK_FULL, "The destination disk is full."),
    (("disk full",), ErrorCategory.DISK_FULL, "The destination disk is full."),
    (
        ("permission denied",),
        ErrorCategory.PERMISSION_DENIED,
        "Permission was denied for the destination.",
    ),
    (
        ("access is denied",),
        ErrorCategory.PERMISSION_DENIED,
        "Permission was denied for the destination.",
    ),
    (
        ("being used by another process",),
        ErrorCategory.OUTPUT_LOCKED,
        "The output file is in use by another application.",
    ),
    (("video unavailable",), ErrorCategory.MEDIA_UNAVAILABLE, "This media is unavailable."),
    (("media is not available",), ErrorCategory.MEDIA_UNAVAILABLE, "This media is unavailable."),
    (("download was cancelled",), ErrorCategory.CANCELLED, "The operation was cancelled."),
)


def map_error(error: BaseException | str) -> MappedError:
    """Return a stable category without exposing technical details in the UI."""

    technical = (
        str(error).strip() or error.__class__.__name__
        if isinstance(error, BaseException)
        else str(error)
    )
    if isinstance(error, OSError):
        if error.errno == errno.ENOSPC:
            return MappedError(ErrorCategory.DISK_FULL, "The destination disk is full.", technical)
        if getattr(error, "winerror", None) in {32, 33}:
            return MappedError(
                ErrorCategory.OUTPUT_LOCKED,
                "The output file is in use by another application.",
                technical,
            )
        if error.errno in {errno.EACCES, errno.EPERM}:
            return MappedError(
                ErrorCategory.PERMISSION_DENIED,
                "Permission was denied for the destination.",
                technical,
            )
    lowered = technical.casefold()
    for required, category, message in _TEXT_PATTERNS:
        if all(fragment in lowered for fragment in required):
            return MappedError(category, message, technical)
    return MappedError(
        ErrorCategory.UNKNOWN, "The operation failed. See the log for details.", technical
    )
