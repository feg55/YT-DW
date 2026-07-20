from __future__ import annotations

import pytest

from openmediadl.core.metadata_cleaner import clean_track_title, normalize_text


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("Cool Music Channel — Night Drive", "Night Drive"),
        ("Cool Music Channel - Night Drive", "Night Drive"),
        ("Cool Music Channel | Night Drive", "Night Drive"),
        ("[Cool Music Channel] Night Drive", "Night Drive"),
    ],
)
def test_removes_channel_from_distinct_prefix(original: str, expected: str) -> None:
    assert clean_track_title(original, "Cool Music Channel") == expected


@pytest.mark.parametrize("separator", [" — ", " - ", " | "])
def test_removes_channel_from_distinct_suffix(separator: str) -> None:
    title = f"Night Drive{separator}Cool Music Channel"
    assert clean_track_title(title, "cool music channel") == "Night Drive"


@pytest.mark.parametrize(
    "label",
    [
        "(Official Video)",
        "[Official Audio]",
        "(Official Music Video)",
        "[Lyrics]",
        "(Lyric Video)",
        "(Visualizer)",
        "(Audio)",
    ],
)
def test_removes_supported_trailing_labels(label: str) -> None:
    assert clean_track_title(f"Night Drive {label}", "") == "Night Drive"


def test_unicode_and_cyrillic_are_preserved() -> None:
    original = "  Музыка Канал —   Ночной\u00a0экспресс  [Lyrics] "
    assert clean_track_title(original, "МУЗЫКА КАНАЛ") == "Ночной экспресс"


def test_channel_in_middle_is_not_removed() -> None:
    original = "A Night with Cool Music Channel in Berlin"
    assert clean_track_title(original, "Cool Music Channel") == original


def test_cleaning_that_would_be_empty_falls_back_to_normalized_original() -> None:
    original = " [Cool Music Channel]   (Official Video) "
    assert clean_track_title(original, "Cool Music Channel") == normalize_text(original)


def test_labels_can_be_kept() -> None:
    assert clean_track_title("Song (Official Video)", "", remove_labels=False) == (
        "Song (Official Video)"
    )
