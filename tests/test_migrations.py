from __future__ import annotations

import sqlite3

import pytest

from openmediadl.database.migrations import MIGRATIONS, apply_migrations


def test_v3_migration_deduplicates_legacy_video_ids() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in MIGRATIONS[:2]:
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (?, ?, ?)
            """,
            (migration.version, migration.name, "2026-01-01T00:00:00+00:00"),
        )
    connection.execute("PRAGMA user_version = 2")

    connection.executemany(
        """
        INSERT INTO queue_items(
            id, source_url, video_id, created_at, updated_at, position
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            (
                "later-position",
                "https://example.test/later",
                "duplicate-video",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                20,
            ),
            (
                "earlier-position",
                "https://example.test/earlier",
                "duplicate-video",
                "2026-01-02T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
                10,
            ),
        ),
    )
    connection.executemany(
        """
        INSERT INTO download_archive(
            archive_key, video_id, source_url, final_media_path, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            (
                "legacy:first",
                "archived-video",
                "https://example.test/old",
                "old.m4a",
                "2026-01-01T00:00:00+00:00",
            ),
            (
                "legacy:second",
                "archived-video",
                "https://example.test/new",
                "new.m4a",
                "2026-01-02T00:00:00+00:00",
            ),
        ),
    )
    connection.commit()

    assert apply_migrations(connection) == 3

    queue_rows = connection.execute(
        "SELECT id FROM queue_items WHERE video_id = 'duplicate-video'"
    ).fetchall()
    archive_rows = connection.execute(
        """
        SELECT final_media_path
        FROM download_archive
        WHERE video_id = 'archived-video'
        """
    ).fetchall()
    assert queue_rows == [("earlier-position",)]
    assert archive_rows == [("new.m4a",)]
    assert connection.execute("PRAGMA user_version").fetchone() == (3,)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO queue_items(
                id, source_url, video_id, created_at, updated_at, position
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "post-migration-duplicate",
                "https://example.test/duplicate",
                "duplicate-video",
                "2026-01-03T00:00:00+00:00",
                "2026-01-03T00:00:00+00:00",
                30,
            ),
        )
