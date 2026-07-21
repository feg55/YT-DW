"""SQLite persistence API."""

from openmediadl.database.connection import Database, SQLiteDatabase, initialize_database
from openmediadl.database.migrations import MIGRATIONS, apply_migrations
from openmediadl.database.repositories import (
    ArchiveEntry,
    ArchiveRepository,
    ClearedDownloadState,
    HistoryEntry,
    HistoryRepository,
    QueueRepository,
    SettingsRepository,
    WindowStateRepository,
    clear_download_state,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveRepository",
    "ClearedDownloadState",
    "Database",
    "HistoryEntry",
    "HistoryRepository",
    "MIGRATIONS",
    "QueueRepository",
    "SQLiteDatabase",
    "SettingsRepository",
    "WindowStateRepository",
    "apply_migrations",
    "clear_download_state",
    "initialize_database",
]
