"""Deterministic M4A metadata writing and post-write validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mutagen.mp4 import MP4, MP4Cover

PathLike = str | os.PathLike[str]


class MetadataWriteError(RuntimeError):
    """Raised when MP4 metadata cannot be written."""


class MetadataVerificationError(RuntimeError):
    """Raised when a saved M4A does not contain its required metadata."""


@dataclass(frozen=True, slots=True)
class MetadataTags:
    title: str
    artist: str
    album_artist: str | None = None
    album: str | None = None
    track_number: int | None = None
    track_total: int | None = None
    year: int | str | None = None
    source_url: str | None = None
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class MetadataVerification:
    path: Path
    title: str
    artist: str
    album_artist: str | None
    album: str | None
    has_cover: bool


def _required_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _year_text(value: int | str | None) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    match = re.fullmatch(r"(?P<year>\d{4})(?:\d{4})?", str(value).strip())
    return match.group("year") if match else None


def _validate_track(number: int | None, total: int | None) -> tuple[int, int] | None:
    if number is None:
        return None
    if isinstance(number, bool) or number < 1:
        raise ValueError("track_number must be a positive integer")
    if total is not None and (isinstance(total, bool) or total < 1):
        raise ValueError("track_total must be a positive integer")
    return number, total or 0


def _cover(path: PathLike) -> MP4Cover:
    cover_path = Path(path)
    data = cover_path.read_bytes()
    if data.startswith(b"\xff\xd8\xff"):
        image_format = MP4Cover.FORMAT_JPEG
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        image_format = MP4Cover.FORMAT_PNG
    else:
        raise ValueError(f"Cover must be JPEG or PNG: {cover_path}")
    return MP4Cover(data, imageformat=image_format)


def _set_optional(tags: Any, atom: str, value: str | None) -> None:
    if value is None:
        tags.pop(atom, None)
    else:
        tags[atom] = [value]


def _first_text(tags: Any, atom: str) -> str | None:
    value = tags.get(atom)
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        return first.strip() or None if isinstance(first, str) else None
    return None


class MetadataWriter:
    """Write Apple-style MP4 atoms and verify the persisted M4A."""

    def write(
        self,
        media_path: PathLike,
        metadata: MetadataTags,
        cover_path: PathLike | None = None,
        *,
        remove_existing_cover: bool = False,
        verify: bool = True,
    ) -> MetadataVerification | None:
        path = Path(media_path)
        if not path.is_file():
            raise FileNotFoundError(path)

        title = _required_text(metadata.title, "title")
        artist = _required_text(metadata.artist, "artist")
        album_artist = _optional_text(metadata.album_artist) or artist
        album = _optional_text(metadata.album)
        track = _validate_track(metadata.track_number, metadata.track_total)
        year = _year_text(metadata.year)
        comment = _optional_text(metadata.comment) or _optional_text(metadata.source_url)
        try:
            artwork = _cover(cover_path) if cover_path is not None else None
        except (OSError, ValueError) as exc:
            raise MetadataWriteError(f"Could not load cover artwork for {path}: {exc}") from exc

        try:
            audio = MP4(str(path))
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
            if tags is None:
                raise MetadataWriteError("Mutagen could not create an MP4 tag container")

            tags["\xa9nam"] = [title]
            tags["\xa9ART"] = [artist]
            tags["aART"] = [album_artist]
            _set_optional(tags, "\xa9alb", album)
            _set_optional(tags, "\xa9day", year)
            _set_optional(tags, "\xa9cmt", comment)
            if track is None:
                tags.pop("trkn", None)
            else:
                tags["trkn"] = [track]

            if artwork is not None:
                tags["covr"] = [artwork]
            elif remove_existing_cover:
                tags.pop("covr", None)
            audio.save()
        except MetadataWriteError:
            raise
        except Exception as exc:
            raise MetadataWriteError(f"Could not write metadata to {path}: {exc}") from exc

        if not verify:
            return None
        return self.verify(
            path,
            require_cover=artwork is not None,
            expected_title=title,
            expected_artist=artist,
        )

    def verify(
        self,
        media_path: PathLike,
        *,
        require_cover: bool = False,
        expected_title: str | None = None,
        expected_artist: str | None = None,
    ) -> MetadataVerification:
        path = Path(media_path)
        try:
            audio = MP4(str(path))
            tags = cast(Any, audio.tags)
            if tags is None:
                raise MetadataVerificationError(f"M4A has no metadata tags: {path}")

            title = _first_text(tags, "\xa9nam")
            artist = _first_text(tags, "\xa9ART")
            has_cover = bool(tags.get("covr"))
            if title is None:
                raise MetadataVerificationError(f"M4A title is missing: {path}")
            if artist is None:
                raise MetadataVerificationError(f"M4A artist is missing: {path}")
            if expected_title is not None and title != expected_title:
                raise MetadataVerificationError(f"M4A title verification failed: {path}")
            if expected_artist is not None and artist != expected_artist:
                raise MetadataVerificationError(f"M4A artist verification failed: {path}")
            if require_cover and not has_cover:
                raise MetadataVerificationError(f"M4A cover is missing: {path}")

            return MetadataVerification(
                path=path,
                title=title,
                artist=artist,
                album_artist=_first_text(tags, "aART"),
                album=_first_text(tags, "\xa9alb"),
                has_cover=has_cover,
            )
        except MetadataVerificationError:
            raise
        except Exception as exc:
            raise MetadataVerificationError(f"Could not read metadata from {path}: {exc}") from exc


def write_m4a_metadata(
    media_path: PathLike,
    metadata: MetadataTags,
    cover_path: PathLike | None = None,
    *,
    remove_existing_cover: bool = False,
    verify: bool = True,
) -> MetadataVerification | None:
    """Functional entry point for :class:`MetadataWriter`."""

    return MetadataWriter().write(
        media_path,
        metadata,
        cover_path,
        remove_existing_cover=remove_existing_cover,
        verify=verify,
    )


def verify_m4a_metadata(
    media_path: PathLike,
    *,
    require_cover: bool = False,
) -> MetadataVerification:
    """Reopen an M4A and require its title, artist, and optional cover."""

    return MetadataWriter().verify(media_path, require_cover=require_cover)
