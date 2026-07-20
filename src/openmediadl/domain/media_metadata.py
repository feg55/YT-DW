"""Typed metadata exchanged between analysis, the queue, and writers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MediaMetadata:
    """Source and editable metadata for one remote media entry."""

    original_title: str = ""
    cleaned_title: str = ""
    channel: str = ""
    uploader: str = ""
    creator: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    playlist_title: str = ""
    playlist_index: int | None = None
    playlist_count: int | None = None
    track_number: int | None = None
    upload_date: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None

    @property
    def channel_name(self) -> str:
        """Return the preferred source name in yt-dlp priority order."""

        return self.channel or self.uploader or self.creator


@dataclass(slots=True)
class MetadataEditFlags:
    """Tracks which editable fields were explicitly changed by the user."""

    title_manually_edited: bool = False
    artist_manually_edited: bool = False
    album_manually_edited: bool = False
    track_manually_edited: bool = False
