"""Safe, deterministic output-file naming."""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Final

PathLike = str | os.PathLike[str]

_WHITESPACE_RE: Final = re.compile(r"\s+", flags=re.UNICODE)
_WINDOWS_INVALID_RE: Final = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PORTABLE_INVALID_RE: Final = re.compile(r"[/\x00]")
_WINDOWS_RESERVED_RE: Final = re.compile(
    r"^(?:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])$",
    flags=re.IGNORECASE,
)
_DEFAULT_MAX_COMPONENT_LENGTH: Final = 240


def _is_windows(platform: str | None) -> bool:
    name = platform if platform is not None else ("windows" if os.name == "nt" else sys.platform)
    folded = name.casefold()
    return folded in {"nt", "win", "windows"} or folded.startswith("win")


def _truncate_component(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value

    dot = value.rfind(".")
    suffix = value[dot:] if 0 < dot and len(value) - dot <= 20 else ""
    stem_length = max_length - len(suffix)
    if stem_length < 1:
        return value[:max_length].rstrip(" .")
    return f"{value[:stem_length].rstrip(' .')}{suffix}"


def _protect_windows_reserved_name(value: str) -> str:
    stem, separator, remainder = value.partition(".")
    if not _WINDOWS_RESERVED_RE.fullmatch(stem.rstrip(" .")):
        return value
    protected_stem = f"{stem.rstrip(' .')}_"
    return f"{protected_stem}{separator}{remainder}" if separator else protected_stem


def sanitize_filename(value: str, platform: str | None = None) -> str:
    """Return a single safe file-name component.

    Windows device names are suffixed with an underscore. Invalid characters
    are removed rather than interpreted as path separators. Empty and dot-only
    results become ``untitled``.
    """

    candidate = unicodedata.normalize("NFC", value)
    candidate = _WHITESPACE_RE.sub(" ", candidate).strip()
    invalid_re = _WINDOWS_INVALID_RE if _is_windows(platform) else _PORTABLE_INVALID_RE
    candidate = invalid_re.sub("", candidate)
    candidate = candidate.rstrip(" .") if _is_windows(platform) else candidate.rstrip("/")

    if candidate in {"", ".", ".."}:
        candidate = "untitled"
    if _is_windows(platform):
        candidate = _protect_windows_reserved_name(candidate)

    candidate = _truncate_component(candidate, _DEFAULT_MAX_COMPONENT_LENGTH)
    candidate = candidate.rstrip(" .") if _is_windows(platform) else candidate
    return candidate or "untitled"


def ensure_unique_path(
    proposed_path: PathLike,
    *,
    preferred_suffix: str | int | None = None,
    exists: Callable[[Path], bool] | None = None,
    max_attempts: int = 10_000,
) -> Path:
    """Return an unoccupied path, adding a stable suffix when necessary.

    The original path is returned when available. A supplied playlist index or
    video ID can be tried first via ``preferred_suffix``; numeric ``(2)`` style
    suffixes are then allocated deterministically.
    """

    path = Path(proposed_path)
    path_exists = exists or Path.exists
    if not path_exists(path):
        return path

    if max_attempts < 2:
        raise ValueError("max_attempts must be at least 2")

    if preferred_suffix is not None:
        safe_suffix = sanitize_filename(str(preferred_suffix), platform="windows")
        preferred = path.with_name(f"{path.stem} ({safe_suffix}){path.suffix}")
        if not path_exists(preferred):
            return preferred

    for index in range(2, max_attempts + 1):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not path_exists(candidate):
            return candidate

    raise FileExistsError(f"Could not allocate a unique path for {path}")


def unique_output_path(proposed_path: PathLike) -> Path:
    """Compatibility alias for the common numeric-suffix allocation."""

    return ensure_unique_path(proposed_path)


def build_output_path(
    directory: PathLike,
    title: str,
    extension: str,
    *,
    platform: str | None = None,
    preferred_suffix: str | int | None = None,
) -> Path:
    """Build and de-duplicate a sanitized output path."""

    normalized_extension = extension if extension.startswith(".") else f".{extension}"
    filename = f"{sanitize_filename(title, platform=platform)}{normalized_extension}"
    return ensure_unique_path(Path(directory) / filename, preferred_suffix=preferred_suffix)
