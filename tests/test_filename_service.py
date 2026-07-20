from __future__ import annotations

from pathlib import Path

import pytest

from openmediadl.core.filename_service import ensure_unique_path, sanitize_filename


def test_windows_invalid_characters_and_trailing_dots_are_removed() -> None:
    assert sanitize_filename(' A<B>:"C"/\\|?* . ', platform="windows") == "ABC"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("CON", "CON_"),
        ("con.txt", "con_.txt"),
        ("COM1", "COM1_"),
        ("LPT9.log", "LPT9_.log"),
        ("normal.m4a", "normal.m4a"),
    ],
)
def test_windows_reserved_device_names_are_protected(value: str, expected: str) -> None:
    assert sanitize_filename(value, platform="win32") == expected


def test_dot_only_and_empty_names_get_a_safe_fallback() -> None:
    assert sanitize_filename("... ", platform="windows") == "untitled"
    assert sanitize_filename("", platform="windows") == "untitled"


def test_duplicate_paths_receive_incrementing_suffix(tmp_path: Path) -> None:
    original = tmp_path / "Night Drive.m4a"
    second = tmp_path / "Night Drive (2).m4a"
    original.touch()
    second.touch()

    assert ensure_unique_path(original) == tmp_path / "Night Drive (3).m4a"


def test_preferred_stable_suffix_is_used_before_a_number(tmp_path: Path) -> None:
    original = tmp_path / "Night Drive.m4a"
    original.touch()

    assert ensure_unique_path(original, preferred_suffix="abc123") == (
        tmp_path / "Night Drive (abc123).m4a"
    )
