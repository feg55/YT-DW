"""Domain model for one persistent download queue entry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from typing import Any, ClassVar, Self
from uuid import uuid4

from openmediadl.domain.download_status import DownloadMode, DownloadStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_datetime(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if value:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return utc_now()


@dataclass(slots=True)
class DownloadItem:
    """Mutable state for an analyzed item and its download lifecycle."""

    source_url: str
    id: str = field(default_factory=lambda: str(uuid4()))
    video_id: str | None = None
    playlist_id: str | None = None
    playlist_title: str | None = None
    playlist_index: int | None = None
    playlist_count: int | None = None
    original_title: str = ""
    cleaned_title: str = ""
    channel: str = ""
    uploader: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    track_number: int | None = None
    upload_date: str | None = None
    duration: float | None = None
    thumbnail_url: str | None = None
    cached_thumbnail_path: str | None = None
    final_media_path: str | None = None
    download_mode: DownloadMode = DownloadMode.AUDIO
    status: DownloadStatus = DownloadStatus.PENDING
    progress_percentage: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None
    retry_count: int = 0
    error_category: str | None = None
    error_message: str | None = None
    technical_error: str | None = None
    current_phase: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    selected: bool = True
    title_manually_edited: bool = False
    artist_manually_edited: bool = False
    album_manually_edited: bool = False
    track_manually_edited: bool = False

    _BOOLEAN_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "selected",
            "title_manually_edited",
            "artist_manually_edited",
            "album_manually_edited",
            "track_manually_edited",
        }
    )

    def __post_init__(self) -> None:
        self.id = str(self.id)
        if not self.id:
            self.id = str(uuid4())
        if not isinstance(self.download_mode, DownloadMode):
            self.download_mode = DownloadMode(self.download_mode)
        if not isinstance(self.status, DownloadStatus):
            self.status = DownloadStatus(self.status)
        self.created_at = _as_datetime(self.created_at)
        self.updated_at = _as_datetime(self.updated_at)
        self.progress_percentage = min(100.0, max(0.0, float(self.progress_percentage)))
        self.downloaded_bytes = max(0, int(self.downloaded_bytes))
        if self.total_bytes is not None:
            self.total_bytes = max(0, int(self.total_bytes))
        self.retry_count = max(0, int(self.retry_count))

    @classmethod
    def new(cls, source_url: str, **values: Any) -> Self:
        """Construct a new queue item with a generated UUID and UTC timestamps."""

        return cls(source_url=source_url, **values)

    @property
    def progress(self) -> float:
        """Compatibility name used by table models."""

        return self.progress_percentage

    @progress.setter
    def progress(self, value: float) -> None:
        self.progress_percentage = min(100.0, max(0.0, float(value)))

    @property
    def has_analyzed_metadata(self) -> bool:
        return bool(self.video_id or self.original_title or self.cleaned_title)

    def touch(self, when: datetime | None = None) -> None:
        self.updated_at = when or utc_now()

    def clear_error(self) -> None:
        self.error_category = None
        self.error_message = None
        self.technical_error = None
        self.touch()

    def to_record(self) -> dict[str, Any]:
        """Return values in the representation used by SQLite repositories."""

        result: dict[str, Any] = {}
        for item_field in fields(self):
            name = item_field.name
            value = getattr(self, name)
            if isinstance(value, (DownloadMode, DownloadStatus)):
                result[name] = value.value
            elif isinstance(value, datetime):
                result[name] = value.isoformat()
            elif name in self._BOOLEAN_FIELDS:
                result[name] = int(bool(value))
            else:
                result[name] = value
        return result

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> Self:
        """Build an item from a sqlite Row or equivalent mapping."""

        values = dict(record)
        known = {item_field.name for item_field in fields(cls)}
        for name in cls._BOOLEAN_FIELDS:
            if name in values:
                values[name] = bool(values[name])
        return cls(**{key: value for key, value in values.items() if key in known})
