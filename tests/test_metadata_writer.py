from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from openmediadl.core.metadata_writer import MetadataTags, MetadataWriteError, MetadataWriter

# Original 80 ms silent AAC-in-M4A fixture generated with FFmpeg 7.1.
_REAL_M4A = base64.b64decode(
    """
AAAAHGZ0eXBNNEEgAAACAE00QSBpc29taXNvMgAAAAhmcmVlAAAAIW1kYXTeAgBMYXZjNjEuMTkuMTAwAAIwQA4B
GCAHAAADAm1vb3YAAABsbXZoZAAAAAAAAAAAAAAAAAAAA+gAAABQAAEAAAEAAAAAAAAAAAAAAAABAAAAAAAAAAAA
AAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAItdHJhawAAAFx0
a2hkAAAAAwAAAAAAAAAAAAAAAQAAAAAAAABQAAAAAAAAAAAAAAABAQAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAA
AAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAJGVkdHMAAAAcZWxzdAAAAAAAAAABAAAAUAAABAAAAQAAAAABpW1kaWEA
AAAgbWRoZAAAAAAAAAAAAAAAAAAAH0AAAAaAVcQAAAAAAC1oZGxyAAAAAAAAAABzb3VuAAAAAAAAAAAAAAAAU291
bmRIYW5kbGVyAAAAAVBtaW5mAAAAEHNtaGQAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwg
AAAAAQAAARRzdGJsAAAAanN0c2QAAAAAAAAAAQAAAFptcDRhAAAAAAAAAAEAAAAAAAAAAAABABAAAAAAH0AAAAAA
ADZlc2RzAAAAAAOAgIAlAAEABICAgBdAFQAAAAAAPoAAAAPBBYCAgAUViFblAAaAgIABAgAAACBzdHRzAAAAAAAA
AAIAAAABAAAEAAAAAAEAAAKAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAACAAAAAQAAABxzdHN6AAAAAAAAAAAAAAAC
AAAAFQAAAAQAAAAUc3RjbwAAAAAAAAABAAAALAAAABpzZ3BkAQAAAHJvbGwAAAACAAAAAf//AAAAHHNiZ3AAAAAA
cm9sbAAAAAEAAAACAAAAAQAAAGF1ZHRhAAAAWW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAA
AAAAAAAALGlsc3QAAAAkqXRvbwAAABxkYXRhAAAAAQAAAABMYXZmNjEuNy4xMDA=
"""
)


def test_real_m4a_round_trip_writes_tags_and_cover(tmp_path: Path) -> None:
    media = tmp_path / "real-track.m4a"
    media.write_bytes(_REAL_M4A)
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (4, 4), (20, 80, 140)).save(cover, "JPEG")

    result = MetadataWriter().write(
        media,
        MetadataTags(
            title="Real Track",
            artist="Real Artist",
            album_artist="Real Artist",
            album="Real Album",
            track_number=2,
            track_total=9,
            year="2026",
            source_url="https://example.test/real",
        ),
        cover,
    )

    reopened = MP4(media)
    assert reopened.tags is not None
    assert reopened.tags["©nam"] == ["Real Track"]
    assert reopened.tags["©ART"] == ["Real Artist"]
    assert reopened.tags["aART"] == ["Real Artist"]
    assert reopened.tags["©alb"] == ["Real Album"]
    assert reopened.tags["trkn"] == [(2, 9)]
    assert reopened.tags["©day"] == ["2026"]
    assert reopened.tags["©cmt"] == ["https://example.test/real"]
    assert reopened.tags["covr"]
    assert result is not None and result.has_cover


def test_writes_and_reopens_required_m4a_atoms(tmp_path: Path) -> None:
    media = tmp_path / "track.m4a"
    media.write_bytes(b"fixture")
    audio = Mock()
    audio.tags = {}

    metadata = MetadataTags(
        title="Night Drive",
        artist="Cool Music Channel",
        album_artist="Cool Music Channel",
        album="Road Songs",
        track_number=3,
        track_total=12,
        year="20240517",
        source_url="https://example.test/watch?v=abc",
    )

    with patch("openmediadl.core.metadata_writer.MP4", return_value=audio) as mp4:
        result = MetadataWriter().write(media, metadata)

    assert mp4.call_count == 2
    audio.save.assert_called_once_with()
    assert audio.tags["©nam"] == ["Night Drive"]
    assert audio.tags["©ART"] == ["Cool Music Channel"]
    assert audio.tags["aART"] == ["Cool Music Channel"]
    assert audio.tags["©alb"] == ["Road Songs"]
    assert audio.tags["trkn"] == [(3, 12)]
    assert audio.tags["©day"] == ["2024"]
    assert audio.tags["©cmt"] == ["https://example.test/watch?v=abc"]
    assert result is not None
    assert result.title == "Night Drive"


def test_embeds_jpeg_cover_and_verifies_it(tmp_path: Path) -> None:
    media = tmp_path / "track.m4a"
    media.write_bytes(b"fixture")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff\xe0small-jpeg-fixture")
    audio = Mock()
    audio.tags = {}

    with patch("openmediadl.core.metadata_writer.MP4", return_value=audio):
        result = MetadataWriter().write(
            media,
            MetadataTags(title="Track", artist="Channel"),
            cover,
        )

    embedded = audio.tags["covr"][0]
    assert isinstance(embedded, MP4Cover)
    assert embedded.imageformat == MP4Cover.FORMAT_JPEG
    assert bytes(embedded) == cover.read_bytes()
    assert result is not None and result.has_cover


def test_existing_cover_is_preserved_unless_removal_is_requested(tmp_path: Path) -> None:
    media = tmp_path / "track.m4a"
    media.write_bytes(b"fixture")
    existing_cover = MP4Cover(b"\xff\xd8\xffold", imageformat=MP4Cover.FORMAT_JPEG)
    audio = Mock()
    audio.tags = {"covr": [existing_cover]}

    with patch("openmediadl.core.metadata_writer.MP4", return_value=audio):
        MetadataWriter().write(
            media,
            MetadataTags(title="Track", artist="Channel"),
            remove_existing_cover=True,
            verify=False,
        )

    assert "covr" not in audio.tags


def test_track_total_without_enabled_track_number_is_omitted(tmp_path: Path) -> None:
    media = tmp_path / "track.m4a"
    media.write_bytes(b"fixture")
    audio = Mock()
    audio.tags = {}

    with patch("openmediadl.core.metadata_writer.MP4", return_value=audio):
        MetadataWriter().write(
            media,
            MetadataTags(title="Track", artist="Channel", track_total=20),
            verify=False,
        )

    assert "trkn" not in audio.tags


def test_rejects_unsupported_cover_without_opening_media(tmp_path: Path) -> None:
    media = tmp_path / "track.m4a"
    media.write_bytes(b"fixture")
    cover = tmp_path / "cover.webp"
    cover.write_bytes(b"not-a-jpeg-or-png")

    with (
        patch("openmediadl.core.metadata_writer.MP4") as mp4,
        pytest.raises(MetadataWriteError, match="Cover must be JPEG or PNG"),
    ):
        MetadataWriter().write(
            media,
            MetadataTags(title="Track", artist="Channel"),
            cover,
        )

    mp4.assert_not_called()
