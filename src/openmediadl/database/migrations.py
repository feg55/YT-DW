"""Ordered, explicit SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "initial_queue_and_settings",
        (
            """
            CREATE TABLE queue_items (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                video_id TEXT,
                playlist_id TEXT,
                playlist_title TEXT,
                playlist_index INTEGER,
                playlist_count INTEGER,
                original_title TEXT NOT NULL DEFAULT '',
                cleaned_title TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                uploader TEXT NOT NULL DEFAULT '',
                artist TEXT NOT NULL DEFAULT '',
                album_artist TEXT NOT NULL DEFAULT '',
                album TEXT NOT NULL DEFAULT '',
                track_number INTEGER,
                upload_date TEXT,
                duration REAL,
                thumbnail_url TEXT,
                cached_thumbnail_path TEXT,
                final_media_path TEXT,
                download_mode TEXT NOT NULL DEFAULT 'audio',
                status TEXT NOT NULL DEFAULT 'pending',
                progress_percentage REAL NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER,
                speed REAL,
                eta REAL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_category TEXT,
                technical_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_item_id TEXT REFERENCES queue_items(id) ON DELETE SET NULL,
                source_url TEXT NOT NULL,
                video_id TEXT,
                title TEXT NOT NULL DEFAULT '',
                download_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                final_media_path TEXT,
                error_category TEXT,
                technical_error TEXT,
                created_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE download_archive (
                archive_key TEXT PRIMARY KEY,
                video_id TEXT,
                source_url TEXT NOT NULL,
                final_media_path TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_queue_status_position ON queue_items(status, position)",
            "CREATE INDEX idx_queue_created_at ON queue_items(created_at)",
            "CREATE INDEX idx_history_video_id ON download_history(video_id)",
        ),
    ),
    Migration(
        2,
        "editable_fields_and_progress_phase",
        (
            "ALTER TABLE queue_items ADD COLUMN selected INTEGER NOT NULL DEFAULT 1",
            """
            ALTER TABLE queue_items
            ADD COLUMN title_manually_edited INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE queue_items
            ADD COLUMN artist_manually_edited INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE queue_items
            ADD COLUMN album_manually_edited INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE queue_items
            ADD COLUMN track_manually_edited INTEGER NOT NULL DEFAULT 0
            """,
            "ALTER TABLE queue_items ADD COLUMN error_message TEXT",
            "ALTER TABLE queue_items ADD COLUMN current_phase TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE download_history ADD COLUMN error_message TEXT",
        ),
    ),
    Migration(
        3,
        "archive_uniqueness_and_window_state",
        (
            """
            DELETE FROM queue_items
            WHERE rowid IN (
                SELECT duplicate_rowid
                FROM (
                    SELECT
                        rowid AS duplicate_rowid,
                        ROW_NUMBER() OVER (
                            PARTITION BY video_id
                            ORDER BY position, created_at, rowid
                        ) AS duplicate_rank
                    FROM queue_items
                    WHERE video_id IS NOT NULL AND video_id <> ''
                )
                WHERE duplicate_rank > 1
            )
            """,
            """
            CREATE UNIQUE INDEX idx_queue_unique_video_id
            ON queue_items(video_id)
            WHERE video_id IS NOT NULL AND video_id <> ''
            """,
            """
            DELETE FROM download_archive
            WHERE rowid IN (
                SELECT duplicate_rowid
                FROM (
                    SELECT
                        rowid AS duplicate_rowid,
                        ROW_NUMBER() OVER (
                            PARTITION BY video_id
                            ORDER BY created_at DESC, rowid DESC
                        ) AS duplicate_rank
                    FROM download_archive
                    WHERE video_id IS NOT NULL AND video_id <> ''
                )
                WHERE duplicate_rank > 1
            )
            """,
            """
            CREATE UNIQUE INDEX idx_archive_unique_video_id
            ON download_archive(video_id)
            WHERE video_id IS NOT NULL AND video_id <> ''
            """,
            """
            CREATE TABLE window_state (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
)


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Apply unapplied migrations atomically and return the schema version."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    applied = {int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")}
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations(version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (migration.version, migration.name, datetime.now(UTC).isoformat()),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return MIGRATIONS[-1].version if MIGRATIONS else 0
