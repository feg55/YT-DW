"""Conservative, Unicode-aware helpers for generated media metadata."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_WHITESPACE_RE: Final = re.compile(r"\s+", flags=re.UNICODE)
_EDGE_SEPARATOR: Final = r"(?:-|\N{EN DASH}|\N{EM DASH}|\|)"
_TRAILING_LABEL_RE: Final = re.compile(
    r"\s*[\[(]\s*(?:"
    r"official\s+(?:music\s+)?video|"
    r"official\s+audio|"
    r"lyrics|"
    r"lyric\s+video|"
    r"visualizer|"
    r"audio"
    r")\s*[\])]\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)


def normalize_text(value: str) -> str:
    """Return NFC-normalized text with runs of whitespace collapsed.

    NFC retains meaningful Unicode characters while making canonically equivalent
    channel and title strings compare consistently.
    """

    normalized = unicodedata.normalize("NFC", value)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def _strip_edge_channel(title: str, channel: str) -> str:
    escaped_channel = re.escape(channel)
    flags = re.IGNORECASE | re.UNICODE

    bracketed = re.match(
        rf"^\[\s*{escaped_channel}\s*\]\s*(?:{_EDGE_SEPARATOR}\s*)?(?P<title>.+)$",
        title,
        flags=flags,
    )
    if bracketed:
        return normalize_text(bracketed.group("title"))

    prefixed = re.match(
        rf"^{escaped_channel}\s*{_EDGE_SEPARATOR}\s*(?P<title>.+)$",
        title,
        flags=flags,
    )
    if prefixed:
        return normalize_text(prefixed.group("title"))

    suffixed = re.match(
        rf"^(?P<title>.+?)\s*{_EDGE_SEPARATOR}\s*{escaped_channel}$",
        title,
        flags=flags,
    )
    if suffixed:
        return normalize_text(suffixed.group("title"))

    return title


def _strip_trailing_labels(title: str) -> str:
    candidate = title
    while match := _TRAILING_LABEL_RE.search(candidate):
        candidate = normalize_text(candidate[: match.start()])
    return candidate


def clean_track_title(
    original_title: str,
    channel_name: str,
    remove_labels: bool = True,
) -> str:
    """Clean a track title without removing channel text from its middle.

    A channel is removed only when it is an exact bracketed prefix or is
    separated from an edge by ``-``, ``\N{EN DASH}``, ``\N{EM DASH}``, or ``|``.
    If the rules would consume the entire value, the normalized original title
    is returned.
    """

    original = normalize_text(original_title)
    if not original:
        return original

    candidate = original
    channel = normalize_text(channel_name)
    if channel:
        candidate = _strip_edge_channel(candidate, channel)
    if remove_labels:
        candidate = _strip_trailing_labels(candidate)

    return candidate or original


def sanitize_filename(value: str, platform: str | None = None) -> str:
    """Compatibility entry point for :mod:`filename_service`."""

    from .filename_service import sanitize_filename as _sanitize_filename

    return _sanitize_filename(value, platform=platform)
