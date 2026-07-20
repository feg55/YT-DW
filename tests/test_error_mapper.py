from __future__ import annotations

import errno

from openmediadl.core.error_mapper import ErrorCategory, map_error


def test_maps_authentication_error() -> None:
    mapped = map_error("ERROR: Sign in to confirm your age; cookies are required")
    assert mapped.category is ErrorCategory.AUTHENTICATION_REQUIRED
    assert "cookies" in mapped.message.casefold() or "sign-in" in mapped.message.casefold()


def test_maps_disk_full_os_error() -> None:
    mapped = map_error(OSError(errno.ENOSPC, "No space left on device"))
    assert mapped.category is ErrorCategory.DISK_FULL


def test_maps_final_http_403_to_actionable_error() -> None:
    technical = "ERROR: unable to download video data: HTTP Error 403: Forbidden"

    mapped = map_error(technical)

    assert mapped.category is ErrorCategory.HTTP_FORBIDDEN
    assert mapped.message == (
        "The media server denied access (HTTP 403). Try again later or use browser cookies."
    )
    assert mapped.technical == technical


def test_unknown_retains_technical_message() -> None:
    mapped = map_error("extractor exploded in a novel way")
    assert mapped.category is ErrorCategory.UNKNOWN
    assert "novel way" in mapped.technical
