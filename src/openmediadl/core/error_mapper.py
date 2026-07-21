"""Map low-level failures to concise, actionable user-facing errors."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from openmediadl.core.browser_cookies import BrowserCookiesUnavailableError


class ErrorCategory(StrEnum):
    FFMPEG_NOT_FOUND = "ffmpeg_not_found"
    FFPROBE_NOT_FOUND = "ffprobe_not_found"
    UNSUPPORTED_URL = "unsupported_url"
    PRIVATE_CONTENT = "private_content"
    AUTHENTICATION_REQUIRED = "authentication_required"
    BROWSER_COOKIES_UNAVAILABLE = "browser_cookies_unavailable"
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
    (
        ("browser cookies are unavailable",),
        ErrorCategory.BROWSER_COOKIES_UNAVAILABLE,
        "Browser cookies could not be loaded. Close the browser and try again, or select "
        "another browser in Settings.",
    ),
    (
        ("failed to load cookies",),
        ErrorCategory.BROWSER_COOKIES_UNAVAILABLE,
        "Browser cookies could not be loaded. Close the browser and try again, or select "
        "another browser in Settings.",
    ),
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

    if isinstance(error, BaseException):
        messages = list(dict.fromkeys(message.strip() for message in _exception_messages(error)))
        messages = [message for message in messages if message]
        technical = "\nCaused by: ".join(messages) or error.__class__.__name__
    else:
        technical = str(error)
    cookie_failure = _find_browser_cookie_failure(error)
    if cookie_failure is not None:
        attempted = ", ".join(cookie_failure.attempted_browsers) or "none found"
        return MappedError(
            ErrorCategory.BROWSER_COOKIES_UNAVAILABLE,
            "Browser cookies could not authenticate this media. "
            f"Tried browser stores: {attempted}. Close the browsers listed and try again, "
            "or select another browser in Settings.",
            technical,
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
    lowered = "\n".join(_exception_messages(error)).casefold()
    for required, category, message in _TEXT_PATTERNS:
        if all(fragment in lowered for fragment in required):
            return MappedError(category, message, technical)
    return MappedError(
        ErrorCategory.UNKNOWN, "The operation failed. See the log for details.", technical
    )


def _exception_messages(error: BaseException | str) -> list[str]:
    if not isinstance(error, BaseException):
        return [str(error)]
    pending = [error]
    messages: list[str] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        messages.append(str(current))
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            original = exc_info[1]
            if isinstance(original, BaseException):
                pending.append(original)
    return messages


def _find_browser_cookie_failure(
    error: BaseException | str,
) -> BrowserCookiesUnavailableError | None:
    if not isinstance(error, BaseException):
        return None
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BrowserCookiesUnavailableError):
            return current
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            original = exc_info[1]
            if isinstance(original, BaseException):
                pending.append(original)
    return None
