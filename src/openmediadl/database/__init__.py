"""SQLite persistence API."""

from openmediadl.database.connection import Database, SQLiteDatabase, initialize_database
from openmediadl.database.migrations import MIGRATIONS, apply_migrations
from openmediadl.database.repositories import (
    ArchiveEntry,
    ArchiveRepository,
    HistoryEntry,
    HistoryRepository,
    QueueRepository,
    SettingsRepository,
    WindowStateRepository,
)

__all__ = [
    "ArchiveEntry",
    "ArchiveRepository",
    "Database",
    "HistoryEntry",
    "HistoryRepository",
    "MIGRATIONS",
    "QueueRepository",
    "SQLiteDatabase",
    "SettingsRepository",
    "WindowStateRepository",
    "apply_migrations",
    "initialize_database",
]
