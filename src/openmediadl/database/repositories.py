"""Repositories for queue state, settings, history, and the download archive."""

from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openmediadl.database.connection import Database
from openmediadl.domain.download_item import DownloadItem, utc_now
from openmediadl.domain.download_status import UNFINISHED_STATUSES, DownloadStatus
from openmediadl.domain.settings import AppSettings, DownloadSettings, MetadataSettings

ITEM_COLUMNS: tuple[str, ...] = (
    "id",
    "source_url",
    "video_id",
    "playlist_id",
    "playlist_title",
    "playlist_index",
    "playlist_count",
    "original_title",
    "cleaned_title",
    "channel",
    "uploader",
    "artist",
    "album_artist",
    "album",
    "track_number",
    "upload_date",
    "duration",
    "thumbnail_url",
    "cached_thumbnail_path",
    "final_media_path",
    "download_mode",
    "status",
    "progress_percentage",
    "downloaded_bytes",
    "total_bytes",
    "speed",
    "eta",
    "retry_count",
    "error_category",
    "error_message",
    "technical_error",
    "current_phase",
    "created_at",
    "updated_at",
    "selected",
    "title_manually_edited",
    "artist_manually_edited",
    "album_manually_edited",
    "track_manually_edited",
)


def _database(value: Database | str | Path) -> Database:
    return value if isinstance(value, Database) else Database(value)


def _status_values(statuses: Iterable[DownloadStatus | str]) -> tuple[str, ...]:
    return tuple(DownloadStatus(status).value for status in statuses)


class QueueRepository:
    """Persistent queue CRUD with duplicate-video protection."""

    def __init__(self, database: Database | str | Path) -> None:
        self.database = _database(database)

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> DownloadItem | None:
        return DownloadItem.from_record(dict(row)) if row is not None else None

    def get(self, item_id: str) -> DownloadItem | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._from_row(row)

    def get_by_video_id(self, video_id: str | None) -> DownloadItem | None:
        if not video_id:
            return None
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM queue_items WHERE video_id = ?", (video_id,)
            ).fetchone()
        return self._from_row(row)

    def list(
        self,
        statuses: Iterable[DownloadStatus | str] | None = None,
        *,
        selected_only: bool = False,
    ) -> list[DownloadItem]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if statuses is not None:
            values = _status_values(statuses)
            if not values:
                return []
            clauses.append(f"status IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        if selected_only:
            clauses.append("selected = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM queue_items{where} ORDER BY position, created_at, id",
                parameters,
            ).fetchall()
        return [DownloadItem.from_record(dict(row)) for row in rows]

    def count(self, statuses: Iterable[DownloadStatus | str] | None = None) -> int:
        if statuses is None:
            sql = "SELECT COUNT(*) FROM queue_items"
            parameters: tuple[str, ...] = ()
        else:
            values = _status_values(statuses)
            if not values:
                return 0
            sql = f"SELECT COUNT(*) FROM queue_items WHERE status IN ({','.join('?' for _ in values)})"
            parameters = values
        with self.database.transaction() as connection:
            return int(connection.execute(sql, parameters).fetchone()[0])

    def add(self, item: DownloadItem) -> DownloadItem:
        """Insert an item, returning an existing row when its video ID is queued."""

        return self.upsert(item)

    def upsert(self, item: DownloadItem) -> DownloadItem:
        """Insert or update by UUID without duplicating a known source video ID."""

        item.touch()
        record = item.to_record()
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM queue_items WHERE id = ?", (item.id,)
            ).fetchone()
            if (
                current is not None
                and DownloadStatus(str(current["status"])) is DownloadStatus.CANCELLED
                and item.status.is_active
            ):
                return DownloadItem.from_record(dict(current))
            if current is None and item.video_id:
                duplicate = connection.execute(
                    "SELECT * FROM queue_items WHERE video_id = ?", (item.video_id,)
                ).fetchone()
                if duplicate is not None:
                    return DownloadItem.from_record(dict(duplicate))
            if current is None:
                position = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(position), -1) + 1 FROM queue_items"
                    ).fetchone()[0]
                )
                column_sql = ", ".join((*ITEM_COLUMNS, "position"))
                placeholders = ", ".join("?" for _ in range(len(ITEM_COLUMNS) + 1))
                connection.execute(
                    f"INSERT INTO queue_items ({column_sql}) VALUES ({placeholders})",
                    [record[column] for column in ITEM_COLUMNS] + [position],
                )
            else:
                # A delayed GUI/worker snapshot must not roll the attempt count back.
                item.retry_count = max(item.retry_count, int(current["retry_count"]))
                record["retry_count"] = item.retry_count
                assignments = ", ".join(
                    f"{column} = ?" for column in ITEM_COLUMNS if column != "id"
                )
                connection.execute(
                    f"UPDATE queue_items SET {assignments} WHERE id = ?",
                    [record[column] for column in ITEM_COLUMNS if column != "id"] + [item.id],
                )
        return item

    save = upsert

    def update(self, item: DownloadItem) -> DownloadItem:
        if self.get(item.id) is None:
            raise KeyError(f"Unknown queue item: {item.id}")
        return self.upsert(item)

    def delete(self, item_id: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM queue_items WHERE id = ?", (item_id,))
            return cursor.rowcount > 0

    def delete_completed(self) -> int:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "DELETE FROM queue_items WHERE status = ?",
                (DownloadStatus.COMPLETED.value,),
            )
            return cursor.rowcount

    def set_status(
        self,
        item_id: str,
        status: DownloadStatus,
        *,
        error_category: str | None = None,
        error_message: str | None = None,
        technical_error: str | None = None,
        current_phase: str = "",
    ) -> DownloadItem | None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE queue_items
                SET status = ?, error_category = ?, error_message = ?,
                    technical_error = ?, current_phase = ?, updated_at = ?,
                    speed = NULL, eta = NULL
                WHERE id = ?
                """,
                (
                    status.value,
                    error_category,
                    error_message,
                    technical_error,
                    current_phase,
                    utc_now().isoformat(),
                    item_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM queue_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._from_row(row)

    def update_progress(self, item: DownloadItem) -> bool:
        """Persist one already-throttled progress snapshot."""

        item.touch()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE queue_items
                SET status = ?, progress_percentage = ?, downloaded_bytes = ?,
                    total_bytes = ?, speed = ?, eta = ?, current_phase = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    item.status.value,
                    item.progress_percentage,
                    item.downloaded_bytes,
                    item.total_bytes,
                    item.speed,
                    item.eta,
                    item.current_phase,
                    item.updated_at.isoformat(),
                    item.id,
                ),
            )
            return cursor.rowcount > 0

    def claim_next_ready(self) -> DownloadItem | None:
        """Atomically claim the next selected ready item for one worker."""

        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM queue_items
                WHERE status = ? AND selected = 1
                ORDER BY position, created_at, id
                LIMIT 1
                """,
                (DownloadStatus.READY.value,),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE queue_items
                SET status = ?, current_phase = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    DownloadStatus.DOWNLOADING.value,
                    "Downloading",
                    utc_now().isoformat(),
                    row["id"],
                    DownloadStatus.READY.value,
                ),
            )
            if changed.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM queue_items WHERE id = ?", (row["id"],)
            ).fetchone()
        return self._from_row(claimed)

    def cancel_pending(self) -> int:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE queue_items
                SET status = ?, current_phase = '', speed = NULL, eta = NULL,
                    updated_at = ?
                WHERE status IN (?, ?)
                """,
                (
                    DownloadStatus.CANCELLED.value,
                    now,
                    DownloadStatus.PENDING.value,
                    DownloadStatus.READY.value,
                ),
            )
            return cursor.rowcount

    def retry_failed(self) -> builtins.list[DownloadItem]:
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT id FROM queue_items WHERE status = ? ORDER BY position",
                (DownloadStatus.FAILED.value,),
            ).fetchall()
            connection.execute(
                """
                UPDATE queue_items
                SET status = CASE
                        WHEN COALESCE(video_id, '') <> ''
                          OR original_title <> '' OR cleaned_title <> ''
                        THEN ? ELSE ? END,
                    current_phase = '', speed = NULL, eta = NULL, updated_at = ?
                WHERE status = ?
                """,
                (
                    DownloadStatus.READY.value,
                    DownloadStatus.PENDING.value,
                    now,
                    DownloadStatus.FAILED.value,
                ),
            )
            ids = [str(row["id"]) for row in rows]
            if not ids:
                return []
            restored = connection.execute(
                f"SELECT * FROM queue_items WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY position",
                ids,
            ).fetchall()
        return [DownloadItem.from_record(dict(row)) for row in restored]

    def restore_unfinished(self) -> builtins.list[DownloadItem]:
        """Recover interrupted active jobs without claiming work on startup."""

        interrupted = (
            DownloadStatus.ANALYZING.value,
            DownloadStatus.DOWNLOADING.value,
            DownloadStatus.PROCESSING.value,
        )
        now = utc_now().isoformat()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE queue_items
                SET status = CASE
                        WHEN status = ? THEN ?
                        WHEN COALESCE(video_id, '') <> ''
                          OR original_title <> '' OR cleaned_title <> ''
                        THEN ? ELSE ? END,
                    current_phase = '', speed = NULL, eta = NULL, updated_at = ?
                WHERE status IN (?, ?, ?)
                """,
                (
                    DownloadStatus.ANALYZING.value,
                    DownloadStatus.PENDING.value,
                    DownloadStatus.READY.value,
                    DownloadStatus.PENDING.value,
                    now,
                    *interrupted,
                ),
            )
            values = _status_values(UNFINISHED_STATUSES)
            rows = connection.execute(
                f"SELECT * FROM queue_items WHERE status IN ({','.join('?' for _ in values)}) ORDER BY position, created_at, id",
                values,
            ).fetchall()
        return [DownloadItem.from_record(dict(row)) for row in rows]


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    queue_item_id: str | None
    source_url: str
    video_id: str | None
    title: str
    download_mode: str
    status: str
    final_media_path: str | None
    error_category: str | None
    error_message: str | None
    technical_error: str | None
    created_at: str
    finished_at: str
    snapshot: dict[str, Any]


class HistoryRepository:
    def __init__(self, database: Database | str | Path) -> None:
        self.database = _database(database)

    def add(self, item: DownloadItem) -> int:
        snapshot = item.to_record()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO download_history(
                    queue_item_id, source_url, video_id, title, download_mode,
                    status, final_media_path, error_category, error_message,
                    technical_error, created_at, finished_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.source_url,
                    item.video_id,
                    item.cleaned_title or item.original_title,
                    item.download_mode.value,
                    item.status.value,
                    item.final_media_path,
                    item.error_category,
                    item.error_message,
                    item.technical_error,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a history row ID")
            return cursor.lastrowid

    record = add

    def list(self, *, limit: int | None = None) -> list[HistoryEntry]:
        sql = "SELECT * FROM download_history ORDER BY id DESC"
        parameters: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (max(0, int(limit)),)
        with self.database.transaction() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            HistoryEntry(
                id=int(row["id"]),
                queue_item_id=row["queue_item_id"],
                source_url=str(row["source_url"]),
                video_id=row["video_id"],
                title=str(row["title"]),
                download_mode=str(row["download_mode"]),
                status=str(row["status"]),
                final_media_path=row["final_media_path"],
                error_category=row["error_category"],
                error_message=row["error_message"],
                technical_error=row["technical_error"],
                created_at=str(row["created_at"]),
                finished_at=str(row["finished_at"]),
                snapshot=json.loads(row["snapshot_json"]),
            )
            for row in rows
        ]


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    archive_key: str
    video_id: str | None
    source_url: str
    final_media_path: str | None
    created_at: str


def _archive_key(video_id: str | None, source_url: str) -> str:
    if video_id:
        return f"video:{video_id}"
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return f"url:{digest}"


class ArchiveRepository:
    def __init__(self, database: Database | str | Path) -> None:
        self.database = _database(database)

    def contains(self, video_id: str | None = None, source_url: str = "") -> bool:
        if not video_id and not source_url:
            return False
        key = _archive_key(video_id, source_url)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT 1 FROM download_archive WHERE archive_key = ?", (key,)
            ).fetchone()
        return row is not None

    def add(self, item: DownloadItem) -> None:
        key = _archive_key(item.video_id, item.source_url)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO download_archive(
                    archive_key, video_id, source_url, final_media_path, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(archive_key) DO UPDATE SET
                    final_media_path = excluded.final_media_path,
                    created_at = excluded.created_at
                """,
                (
                    key,
                    item.video_id,
                    item.source_url,
                    item.final_media_path,
                    item.updated_at.isoformat(),
                ),
            )

    record = add

    def remove(self, video_id: str | None = None, source_url: str = "") -> bool:
        if not video_id and not source_url:
            return False
        key = _archive_key(video_id, source_url)
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "DELETE FROM download_archive WHERE archive_key = ?", (key,)
            )
            return cursor.rowcount > 0

    def list(self) -> list[ArchiveEntry]:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM download_archive ORDER BY created_at DESC"
            ).fetchall()
        return [ArchiveEntry(**dict(row)) for row in rows]


class SettingsRepository:
    """JSON settings store with typed helpers and forward-compatible loading."""

    APP_KEY = "application"
    METADATA_KEY = "metadata"
    DOWNLOAD_KEY = "downloads"

    def __init__(self, database: Database | str | Path) -> None:
        self.database = _database(database)

    def set(self, key: str, value: Any) -> None:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)
        payload = json.dumps(value, ensure_ascii=False)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO app_settings(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, payload, datetime.now(UTC).isoformat()),
            )

    def get(self, key: str, default: Any = None) -> Any:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except (TypeError, json.JSONDecodeError):
            return default

    def delete(self, key: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute("DELETE FROM app_settings WHERE key = ?", (key,))
            return cursor.rowcount > 0

    def save(self, settings: AppSettings) -> None:
        self.set(self.APP_KEY, settings.to_dict())

    save_app_settings = save

    def load(self) -> AppSettings:
        value = self.get(self.APP_KEY, {})
        return AppSettings.from_dict(value) if isinstance(value, dict) else AppSettings()

    load_app_settings = load

    def save_metadata_settings(self, settings: MetadataSettings) -> None:
        self.set(self.METADATA_KEY, settings.to_dict())

    def load_metadata_settings(self) -> MetadataSettings:
        value = self.get(self.METADATA_KEY, {})
        return MetadataSettings.from_dict(value) if isinstance(value, dict) else MetadataSettings()

    def save_download_settings(self, settings: DownloadSettings) -> None:
        self.set(self.DOWNLOAD_KEY, settings.to_dict())

    def load_download_settings(self) -> DownloadSettings:
        value = self.get(self.DOWNLOAD_KEY, {})
        return DownloadSettings.from_dict(value) if isinstance(value, dict) else DownloadSettings()


class WindowStateRepository:
    def __init__(self, database: Database | str | Path) -> None:
        self.database = _database(database)

    def set(self, key: str, value: bytes) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO window_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, sqlite3.Binary(value), datetime.now(UTC).isoformat()),
            )

    def get(self, key: str) -> bytes | None:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT value FROM window_state WHERE key = ?", (key,)
            ).fetchone()
        return bytes(row["value"]) if row is not None else None
