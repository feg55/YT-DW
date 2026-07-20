"""Queue state and download mode enumerations."""

from __future__ import annotations

from enum import StrEnum


class DownloadStatus(StrEnum):
    """Persistent states in the lifetime of a queue item."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    READY = "ready"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self in ACTIVE_STATUSES


class DownloadMode(StrEnum):
    """Media output requested for a queue item."""

    VIDEO = "video"
    AUDIO = "audio"

    # Readable aliases for callers which want to be explicit about the format.
    AUDIO_ONLY = "audio"
    AUDIO_M4A = "audio"


TERMINAL_STATUSES: frozenset[DownloadStatus] = frozenset(
    {
        DownloadStatus.COMPLETED,
        DownloadStatus.SKIPPED,
        DownloadStatus.CANCELLED,
        DownloadStatus.FAILED,
    }
)

ACTIVE_STATUSES: frozenset[DownloadStatus] = frozenset(
    {
        DownloadStatus.ANALYZING,
        DownloadStatus.DOWNLOADING,
        DownloadStatus.PROCESSING,
    }
)

UNFINISHED_STATUSES: frozenset[DownloadStatus] = frozenset(
    {
        DownloadStatus.PENDING,
        DownloadStatus.ANALYZING,
        DownloadStatus.READY,
        DownloadStatus.DOWNLOADING,
        DownloadStatus.PROCESSING,
    }
)
