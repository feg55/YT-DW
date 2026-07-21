from __future__ import annotations

from pathlib import Path

from openmediadl.database import (
    ArchiveRepository,
    Database,
    HistoryRepository,
    QueueRepository,
    SettingsRepository,
)
from openmediadl.domain import (
    AppSettings,
    CookieBrowser,
    DownloadItem,
    DownloadMode,
    DownloadStatus,
    LanguagePreference,
    ThemePreference,
)


def test_fresh_settings_use_system_browser_cookie_mode(tmp_path: Path) -> None:
    repository = SettingsRepository(Database(tmp_path / "fresh.sqlite3"))

    application = repository.load()
    downloads = repository.load_download_settings()

    assert application.downloads is not None
    assert application.downloads.cookie_browser is CookieBrowser.SYSTEM
    assert downloads.cookie_browser is CookieBrowser.SYSTEM


def test_queue_item_round_trips_across_database_instances(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    first_repository = QueueRepository(Database(path))
    item = DownloadItem.new(
        "https://example.test/watch?v=abc",
        video_id="abc",
        playlist_id="playlist",
        playlist_title="An Album",
        playlist_index=7,
        playlist_count=42,
        original_title="Channel - Song",
        cleaned_title="Song",
        channel="Channel",
        uploader="Uploader",
        artist="Channel",
        album_artist="Channel",
        album="An Album",
        track_number=7,
        upload_date="20250102",
        duration=123.5,
        thumbnail_url="https://example.test/cover.webp",
        cached_thumbnail_path="cache/abc.jpg",
        final_media_path="music/Song.m4a",
        download_mode=DownloadMode.AUDIO,
        status=DownloadStatus.READY,
        progress_percentage=12.5,
        downloaded_bytes=125,
        total_bytes=1000,
        speed=50.0,
        eta=17.5,
        retry_count=2,
        current_phase="Downloading audio",
        title_manually_edited=True,
        artist_manually_edited=True,
    )

    first_repository.add(item)
    restored = QueueRepository(Database(path)).get(item.id)

    assert restored is not None
    assert restored.id == item.id
    assert restored.video_id == "abc"
    assert restored.cleaned_title == "Song"
    assert restored.download_mode is DownloadMode.AUDIO
    assert restored.status is DownloadStatus.READY
    assert restored.progress_percentage == 12.5
    assert restored.title_manually_edited is True
    assert restored.artist_manually_edited is True
    assert restored.created_at == item.created_at


def test_known_video_id_is_not_queued_twice(tmp_path: Path) -> None:
    repository = QueueRepository(Database(tmp_path / "queue.sqlite3"))
    original = repository.add(DownloadItem.new("https://example.test/one", video_id="same-id"))
    duplicate = repository.add(DownloadItem.new("https://example.test/two", video_id="same-id"))

    assert duplicate.id == original.id
    assert repository.count() == 1
    assert repository.get(original.id) is not None
    assert repository.get(original.id).source_url == "https://example.test/one"  # type: ignore[union-attr]


def test_settings_history_and_archive_are_persistent(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    database = Database(path)
    settings_repository = SettingsRepository(database)
    settings = AppSettings()
    assert settings.downloads is not None
    assert settings.metadata is not None
    settings.downloads.max_concurrent_downloads = 3
    settings.metadata.crop_cover_to_square = False
    assert settings.appearance is not None
    settings.appearance.theme = ThemePreference.LIGHT
    settings.appearance.language = LanguagePreference.RUSSIAN
    settings_repository.save(settings)

    item = DownloadItem.new(
        "https://example.test/watch?v=done",
        video_id="done",
        cleaned_title="Finished",
        status=DownloadStatus.COMPLETED,
        final_media_path="Finished.m4a",
    )
    QueueRepository(database).add(item)
    HistoryRepository(database).add(item)
    ArchiveRepository(database).add(item)

    reopened = Database(path)
    loaded_settings = SettingsRepository(reopened).load()
    assert loaded_settings.downloads is not None
    assert loaded_settings.metadata is not None
    assert loaded_settings.downloads.max_concurrent_downloads == 3
    assert loaded_settings.metadata.crop_cover_to_square is False
    assert loaded_settings.appearance is not None
    assert loaded_settings.appearance.theme is ThemePreference.LIGHT
    assert loaded_settings.appearance.language is LanguagePreference.RUSSIAN
    assert ArchiveRepository(reopened).contains("done")
    history = HistoryRepository(reopened).list()
    assert len(history) == 1
    assert history[0].video_id == "done"
    assert history[0].snapshot["cleaned_title"] == "Finished"


def test_database_enables_foreign_keys_wal_and_all_migrations(tmp_path: Path) -> None:
    database = Database(tmp_path / "queue.sqlite3")
    connection = database.connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3
    finally:
        connection.close()
