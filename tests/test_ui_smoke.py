from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTableView

from openmediadl.application import ApplicationPaths
from openmediadl.core.analyzer import AnalyzedEntry
from openmediadl.core.ffmpeg_service import FFmpegService
from openmediadl.core.queue_manager import QueueManager
from openmediadl.core.runtime_tools import RuntimeToolsStatus
from openmediadl.core.thumbnail_service import ThumbnailService
from openmediadl.database.connection import Database
from openmediadl.database.repositories import SettingsRepository, WindowStateRepository
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import (
    AppearanceSettings,
    AppSettings,
    CookieBrowser,
    LanguagePreference,
    MetadataSettings,
    ThemePreference,
)
from openmediadl.i18n import Translator
from openmediadl.ui.delegates import TwoLineTextDelegate
from openmediadl.ui.main_window import MainWindow
from openmediadl.ui.models import Column
from openmediadl.ui.settings_dialog import SettingsDialog


def _paths(tmp_path: Path) -> ApplicationPaths:
    paths = ApplicationPaths(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        database_file=tmp_path / "data" / "app.sqlite3",
        archive_file=tmp_path / "data" / "archive.txt",
        bundled_tools_dir=tmp_path / "tools",
    )
    paths.create()
    return paths


def _window(
    paths: ApplicationPaths,
    database: Database,
    *,
    translator: Translator | None = None,
    runtime_tools_service: Any | None = None,
) -> MainWindow:
    return MainWindow(
        paths,
        QueueManager(database),
        SettingsRepository(database),
        WindowStateRepository(database),
        FFmpegService(paths.bundled_tools_dir),
        ThumbnailService(paths.cache_dir / "thumbnails"),
        translator=translator,
        runtime_tools_service=runtime_tools_service,
    )


def _ready_item(destination: Path) -> DownloadItem:
    return DownloadItem.new(
        "https://example.com/watch?v=state-test",
        video_id="state-test",
        original_title="Original title",
        cleaned_title="Initial title",
        artist="Initial artist",
        final_media_path=str(destination / "Initial title.m4a"),
        download_mode=DownloadMode.AUDIO,
        status=DownloadStatus.READY,
    )


def _close(window: MainWindow, database: Database) -> None:
    window.close()
    QApplication.processEvents()
    database.close()


def test_main_window_constructs_three_persistent_tabs(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)

    assert window.windowTitle() == "YT-DW"
    assert window.tabs.count() == 3
    assert window.tabs.tabText(0).startswith(window.translator.tr("tab.analyze"))
    assert window.tabs.tabText(1).startswith(window.translator.tr("tab.review"))
    assert window.tabs.tabText(2).startswith(window.translator.tr("tab.download"))
    assert isinstance(window.table, QTableView)
    assert isinstance(window.download_table, QTableView)
    assert window.table is not window.download_table
    assert window.table.queue_model is window.queue_model
    assert window.download_table.queue_model is window.queue_model
    assert window.queue_model.rowCount() == 0
    assert window.metadata_checks["embed_thumbnail_as_cover"].isChecked()
    assert not window.metadata_checks["use_playlist_title_as_album"].isChecked()
    assert window.table.columnWidth(int(Column.SELECTED)) <= 40
    assert window.table.isColumnHidden(int(Column.ALBUM))
    assert window.table.isColumnHidden(int(Column.ERROR))
    assert isinstance(
        window.table.itemDelegateForColumn(int(Column.ORIGINAL_TITLE)),
        TwoLineTextDelegate,
    )
    assert isinstance(
        window.table.itemDelegateForColumn(int(Column.CLEANED_TITLE)),
        TwoLineTextDelegate,
    )

    _close(window, database)
    del app


def test_automatic_runtime_tool_setup_enables_actions_and_records_deno(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    tool_directory = tmp_path / "managed tools"
    expected = RuntimeToolsStatus(
        ffmpeg=tool_directory / "ffmpeg.exe",
        ffprobe=tool_directory / "ffprobe.exe",
        deno=tool_directory / "deno.exe",
        ffmpeg_version="ffmpeg version test",
        ffprobe_version="ffprobe version test",
        deno_version="deno 2.9.3",
        ffmpeg_source="managed",
        deno_source="managed",
    )

    class ReadyService:
        @staticmethod
        def provision(
            _manual: str | None,
            *,
            progress: Any,
            is_cancelled: Any,
        ) -> RuntimeToolsStatus:
            assert not is_cancelled()
            progress("ffmpeg", 100, 100)
            return expected

    window = _window(paths, database, runtime_tools_service=ReadyService())
    worker = window._ffmpeg_check_worker
    assert worker is not None
    assert worker.wait(2000)
    QApplication.processEvents()

    assert window._ffmpeg_installation is not None
    assert window._ffmpeg_installation.available
    assert window._js_runtime_path == str(expected.deno)
    assert window.analyze_button.isEnabled()
    assert window.download_button.isEnabled()
    assert window.runtime_tools_progress.isHidden()
    assert window.runtime_tools_retry_button.isHidden()

    _close(window, database)
    del app


def test_partial_runtime_tool_setup_keeps_media_actions_disabled(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    tool_directory = tmp_path / "managed tools"
    partial = RuntimeToolsStatus(
        ffmpeg=tool_directory / "ffmpeg.exe",
        ffprobe=tool_directory / "ffprobe.exe",
        deno=None,
        ffmpeg_version="ffmpeg version test",
        ffprobe_version="ffprobe version test",
        ffmpeg_source="managed",
        errors=("Deno: network unavailable",),
    )

    class PartialService:
        @staticmethod
        def provision(
            _manual: str | None,
            *,
            progress: Any,
            is_cancelled: Any,
        ) -> RuntimeToolsStatus:
            assert not is_cancelled()
            progress("deno", 0, 100)
            return partial

    window = _window(paths, database, runtime_tools_service=PartialService())
    worker = window._ffmpeg_check_worker
    assert worker is not None
    assert worker.wait(2000)
    QApplication.processEvents()

    assert window._ffmpeg_installation is not None
    assert window._ffmpeg_installation.available
    assert window._js_runtime_path is None
    assert not window.analyze_button.isEnabled()
    assert not window.download_button.isEnabled()
    assert window.runtime_tools_progress.isHidden()
    assert not window.runtime_tools_retry_button.isHidden()

    _close(window, database)
    del app


def test_clear_all_button_resets_download_state_without_deleting_media_or_url_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    playlist_url = "https://example.com/playlist?list=again"
    window.url_input.setPlainText(playlist_url)
    media_file = tmp_path / "downloads" / "finished.m4a"
    media_file.parent.mkdir()
    media_file.write_bytes(b"media")

    completed = window.queue_manager.add(
        _ready_item(media_file.parent),
        skip_if_archived=False,
    )
    completed = window.queue_manager.mark_completed(
        completed.id,
        final_media_path=str(media_file),
    )
    assert completed is not None
    window.queue_model.add_items([completed])
    paths.archive_file.write_text("youtube state-test\n", encoding="utf-8")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes),
    )

    window._clear_all()

    assert window.clear_all_button.text() == window.translator.tr("action.clear_all")
    assert window.queue_model.rowCount() == 0
    assert window.queue_manager.list() == []
    assert window.queue_manager.history.list() == []
    assert window.queue_manager.archive.list() == []
    assert not paths.archive_file.exists()
    assert media_file.read_bytes() == b"media"
    assert window.url_input.toPlainText() == playlist_url
    assert window.tabs.currentIndex() == window.analyze_tab_index

    _close(window, database)
    del app


def test_clear_all_refuses_while_analysis_worker_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    item = window.queue_manager.add(_ready_item(tmp_path / "downloads"))
    window.queue_model.add_items([item])
    warnings: list[tuple[str, str]] = []

    class RunningWorker:
        @staticmethod
        def isRunning() -> bool:  # noqa: N802 - mirrors Qt's API
            return True

    window._analysis_worker = RunningWorker()  # type: ignore[assignment]
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, message: warnings.append((str(title), str(message)))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: pytest.fail("confirmation must not be shown")),
    )

    window._clear_all()

    assert warnings
    assert window.queue_manager.get(item.id) is not None
    assert window.queue_model.rowCount() == 1

    window._analysis_worker = None
    _close(window, database)
    del app


def test_settings_dialog_defaults_and_persists_appearance(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    repository = SettingsRepository(database)
    settings = AppSettings()
    translator = Translator(LanguagePreference.ENGLISH)
    dialog = SettingsDialog(
        settings,
        FFmpegService(paths.bundled_tools_dir),
        translator=translator,
    )

    assert dialog.theme.currentData() == ThemePreference.DARK.value
    original_index = dialog.theme.findData(ThemePreference.ORIGINAL.value)
    assert original_index >= 0
    assert dialog.theme.itemText(original_index) == translator.tr("theme.original")
    assert dialog.language.currentData() == LanguagePreference.SYSTEM.value
    assert dialog.remember_last_tab.isChecked()

    dialog.theme.setCurrentIndex(original_index)
    dialog.language.setCurrentIndex(dialog.language.findData(LanguagePreference.RUSSIAN.value))
    dialog.remember_last_tab.setChecked(False)
    dialog.apply_to(settings)
    repository.save(settings)

    restored = repository.load()
    assert restored.appearance is not None
    assert restored.appearance.theme is ThemePreference.ORIGINAL
    assert restored.appearance.language is LanguagePreference.RUSSIAN
    assert not restored.appearance.remember_last_tab

    dialog.close()
    database.close()
    del app


def test_settings_dialog_persists_cookie_preference_and_explicit_profile(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    repository = SettingsRepository(database)
    translator = Translator(LanguagePreference.ENGLISH)
    settings = AppSettings()
    dialog = SettingsDialog(
        settings,
        FFmpegService(paths.bundled_tools_dir),
        translator=translator,
    )

    assert dialog.browser.itemData(0) == CookieBrowser.SYSTEM.value
    assert dialog.browser.itemText(0) == translator.tr("settings.cookies_system")
    assert dialog.browser.itemData(1) == CookieBrowser.DISABLED.value
    assert dialog.browser.itemText(1) == translator.tr("settings.cookies_disabled")
    assert dialog.browser.currentData() == CookieBrowser.SYSTEM.value
    assert not dialog.profile.isEnabled()

    dialog.apply_to(settings)
    repository.save(settings)
    restored = repository.load()
    assert restored.downloads is not None
    assert restored.downloads.cookie_browser is CookieBrowser.SYSTEM
    assert restored.downloads.cookie_profile is None

    firefox_index = dialog.browser.findData(CookieBrowser.FIREFOX.value)
    dialog.browser.setCurrentIndex(firefox_index)
    assert dialog.profile.isEnabled()
    dialog.profile.setText("developer-edition-default")
    dialog.apply_to(settings)
    repository.save(settings)

    restored = repository.load()
    assert restored.downloads is not None
    assert restored.downloads.cookie_browser is CookieBrowser.FIREFOX
    assert restored.downloads.cookie_profile == "developer-edition-default"

    disabled_index = dialog.browser.findData(CookieBrowser.DISABLED.value)
    dialog.browser.setCurrentIndex(disabled_index)
    assert not dialog.profile.isEnabled()
    dialog.apply_to(restored)
    repository.save(restored)
    disabled = repository.load()
    assert disabled.downloads is not None
    assert disabled.downloads.cookie_browser is CookieBrowser.DISABLED
    assert disabled.downloads.cookie_profile is None

    dialog.close()
    database.close()
    del app


def test_runtime_language_switch_retranslates_without_replacing_queue_state(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    translator = Translator(LanguagePreference.ENGLISH)
    window = _window(paths, database, translator=translator)
    item = window.queue_manager.add(_ready_item(tmp_path / "output"))
    window.queue_model.add_items([item])
    assert window.queue_model.setData(
        window.queue_model.index(0, int(Column.CLEANED_TITLE)),
        "Hand edited title",
        Qt.ItemDataRole.EditRole,
    )
    window._update_queue_summary()
    model_before = window.queue_model
    item_before = model_before.items[0]
    record_before = item_before.to_record()

    assert window.tabs.tabText(window.analyze_tab_index) == translator.tr("tab.analyze")
    assert window.settings_button.text() == translator.tr("action.settings")
    assert model_before.headerData(
        int(Column.ORIGINAL_TITLE),
        Qt.Orientation.Horizontal,
        Qt.ItemDataRole.DisplayRole,
    ) == translator.tr("table.original_title")

    assert translator.set_language(LanguagePreference.RUSSIAN)
    window._retranslate_ui()

    assert window.tabs.tabText(window.analyze_tab_index) == translator.tr("tab.analyze")
    assert window.tabs.tabText(window.review_tab_index) == translator.tr(
        "tab.review_count", count=1
    )
    assert window.tabs.tabText(window.download_tab_index) == translator.tr(
        "tab.download_count", count=1
    )
    assert window.settings_button.text() == translator.tr("action.settings")
    assert window.metadata_group.title() == translator.tr("group.metadata_rules")
    assert model_before.headerData(
        int(Column.ORIGINAL_TITLE),
        Qt.Orientation.Horizontal,
        Qt.ItemDataRole.DisplayRole,
    ) == translator.tr("table.original_title")
    settings_dialog = SettingsDialog(
        window.settings,
        window.ffmpeg_service,
        window,
        translator=translator,
    )
    assert settings_dialog.windowTitle() == translator.tr("settings.title")
    assert settings_dialog.tabs.tabText(0) == translator.tr("settings.tab.interface")
    settings_dialog.close()

    assert window.queue_model is model_before
    assert window.table.queue_model is model_before
    assert window.download_table.queue_model is model_before
    assert window.queue_model.items[0] is item_before
    assert window.queue_model.items[0].to_record() == record_before

    _close(window, database)
    del app


def test_tab_switching_and_destination_change_preserve_manual_edits(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    item = window.queue_manager.add(_ready_item(tmp_path / "old-output"))
    window.queue_model.add_items([item])

    assert window.queue_model.setData(
        window.queue_model.index(0, int(Column.CLEANED_TITLE)),
        "Hand edited title",
        Qt.ItemDataRole.EditRole,
    )
    assert window.queue_model.setData(
        window.queue_model.index(0, int(Column.ARTIST)),
        "Hand edited artist",
        Qt.ItemDataRole.EditRole,
    )
    assert window.queue_model.setData(
        window.queue_model.index(0, int(Column.SELECTED)),
        Qt.CheckState.Unchecked,
        Qt.ItemDataRole.CheckStateRole,
    )

    for tab_index in (window.download_tab_index, window.analyze_tab_index, window.review_tab_index):
        window.tabs.setCurrentIndex(tab_index)
        QApplication.processEvents()

    edited = window.queue_model.items[0]
    assert edited.cleaned_title == "Hand edited title"
    assert edited.artist == "Hand edited artist"
    assert not edited.selected
    assert edited.title_manually_edited
    assert edited.artist_manually_edited

    new_destination = tmp_path / "new-output"
    window.destination.setText(str(new_destination))
    window._destination_changed()

    updated = window.queue_model.items[0]
    assert Path(updated.final_media_path or "").parent == new_destination
    assert updated.cleaned_title == "Hand edited title"
    assert updated.artist == "Hand edited artist"
    assert not updated.selected
    assert updated.title_manually_edited
    assert updated.artist_manually_edited

    persisted = window.queue_manager.get(updated.id)
    assert persisted is not None
    assert persisted.cleaned_title == "Hand edited title"
    assert persisted.artist == "Hand edited artist"
    assert not persisted.selected
    assert persisted.title_manually_edited
    assert persisted.artist_manually_edited

    _close(window, database)
    del app


def test_mode_change_only_affects_future_analysis(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    item = window.queue_manager.add(_ready_item(tmp_path / "output"))
    window.queue_model.add_items([item])
    before = item.to_record()

    video_index = window.mode_combo.findData(DownloadMode.VIDEO.value)
    window.mode_combo.setCurrentIndex(video_index)
    QApplication.processEvents()

    assert window.queue_model.items[0].to_record() == before
    assert window.queue_manager.get(item.id).to_record() == before  # type: ignore[union-attr]

    _close(window, database)
    del app


def test_terminal_worker_updates_replace_cancelled_rows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    completed_item = window.queue_manager.add(_ready_item(tmp_path / "completed"))
    failed_item = window.queue_manager.add(
        DownloadItem.new(
            "https://example.com/watch?v=failed-after-cancel",
            video_id="failed-after-cancel",
            original_title="Failed after cancel",
            cleaned_title="Failed after cancel",
            final_media_path=str(tmp_path / "failed" / "Failed after cancel.m4a"),
            status=DownloadStatus.READY,
        )
    )
    window.queue_model.add_items([completed_item, failed_item])

    cancelled_completed = window.queue_manager.cancel(completed_item.id)
    cancelled_failed = window.queue_manager.cancel(failed_item.id)
    assert cancelled_completed is not None
    assert cancelled_failed is not None
    window.queue_model.update_item(cancelled_completed)
    window.queue_model.update_item(cancelled_failed)

    completed_snapshot = deepcopy(cancelled_completed)
    completed_snapshot.status = DownloadStatus.COMPLETED
    completed_snapshot.progress_percentage = 100.0
    completed_snapshot.current_phase = "Completed"
    window._on_item_updated(completed_snapshot)

    failed_snapshot = deepcopy(cancelled_failed)
    failed_snapshot.status = DownloadStatus.FAILED
    failed_snapshot.error_category = "network_error"
    failed_snapshot.error_message = "HTTP 403: Forbidden"
    failed_snapshot.technical_error = "fixture failure"
    failed_snapshot.current_phase = "Failed"
    window._on_item_updated(failed_snapshot)

    displayed_completed = window.queue_model.item_by_id(completed_item.id)
    displayed_failed = window.queue_model.item_by_id(failed_item.id)
    assert displayed_completed is not None
    assert displayed_completed.status is DownloadStatus.COMPLETED
    assert displayed_completed.progress_percentage == 100.0
    assert displayed_failed is not None
    assert displayed_failed.status is DownloadStatus.FAILED
    failed_row = window.queue_model.items.index(displayed_failed)
    assert (
        window.queue_model.data(
            window.queue_model.index(failed_row, int(Column.PROGRESS)),
            Qt.ItemDataRole.DisplayRole,
        )
        == "HTTP 403: Forbidden"
    )

    persisted_completed = window.queue_manager.get(completed_item.id)
    persisted_failed = window.queue_manager.get(failed_item.id)
    assert persisted_completed is not None
    assert persisted_completed.status is DownloadStatus.COMPLETED
    assert persisted_failed is not None
    assert persisted_failed.status is DownloadStatus.FAILED
    assert persisted_failed.error_message == "HTTP 403: Forbidden"

    _close(window, database)
    del app


def test_active_tab_and_url_draft_restore_between_windows(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    first = _window(paths, database)
    draft = "https://example.com/video/one\nhttps://example.com/playlist/two"
    first.url_input.setPlainText(draft)
    first.tabs.setCurrentIndex(first.review_tab_index)
    first.close()
    QApplication.processEvents()

    restored = _window(paths, database)

    assert restored.tabs.currentIndex() == restored.review_tab_index
    assert restored.url_input.toPlainText() == draft

    _close(restored, database)
    del app


def test_disabled_last_tab_restore_always_opens_analyze(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    settings_repository = SettingsRepository(database)
    settings_repository.save(
        AppSettings(
            appearance=AppearanceSettings(
                theme=ThemePreference.DARK,
                language=LanguagePreference.ENGLISH,
                remember_last_tab=False,
            )
        )
    )
    window_state = WindowStateRepository(database)
    window_state.set("main.active_tab", b"2")

    first = _window(paths, database)
    assert first.tabs.currentIndex() == first.analyze_tab_index
    first.tabs.setCurrentIndex(first.download_tab_index)
    first.close()
    QApplication.processEvents()

    restored = _window(paths, database)
    assert restored.tabs.currentIndex() == restored.analyze_tab_index

    _close(restored, database)
    del app


def test_recalculate_metadata_preserves_all_manually_edited_fields(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    item = DownloadItem.new(
        "https://example.com/watch?v=manual-fields",
        video_id="manual-fields",
        playlist_title="Generated album",
        playlist_index=9,
        original_title="Channel - Generated title (Official Video)",
        cleaned_title="Manual title",
        channel="Channel",
        artist="Manual artist",
        album="Manual album",
        track_number=42,
        final_media_path=str(tmp_path / "output" / "Manual title.m4a"),
        status=DownloadStatus.READY,
        selected=True,
        title_manually_edited=True,
        artist_manually_edited=True,
        album_manually_edited=True,
        track_manually_edited=True,
    )
    item = window.queue_manager.add(item)
    window.queue_model.add_items([item])

    window._recalculate_metadata()

    recalculated = window.queue_model.items[0]
    assert recalculated.cleaned_title == "Manual title"
    assert recalculated.artist == "Manual artist"
    assert recalculated.album == "Manual album"
    assert recalculated.track_number == 42
    assert recalculated.title_manually_edited
    assert recalculated.artist_manually_edited
    assert recalculated.album_manually_edited
    assert recalculated.track_manually_edited

    persisted = window.queue_manager.get(item.id)
    assert persisted is not None
    assert persisted.cleaned_title == "Manual title"
    assert persisted.artist == "Manual artist"
    assert persisted.album == "Manual album"
    assert persisted.track_number == 42
    assert persisted.title_manually_edited
    assert persisted.artist_manually_edited
    assert persisted.album_manually_edited
    assert persisted.track_manually_edited

    _close(window, database)
    del app


def test_incremental_analysis_batch_uses_start_snapshot(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    window = _window(paths, database)
    snapshot_destination = tmp_path / "snapshot-output"
    changed_destination = tmp_path / "changed-output"
    window._analysis_mode = DownloadMode.VIDEO
    window._analysis_metadata = MetadataSettings(
        use_playlist_index_as_track_number=True,
        use_cleaned_title_as_filename=False,
    )
    window._analysis_destination = str(snapshot_destination)

    window.settings.metadata = MetadataSettings(
        use_playlist_index_as_track_number=False,
        use_cleaned_title_as_filename=True,
    )
    window.destination.setText(str(changed_destination))
    audio_index = window.mode_combo.findData(DownloadMode.AUDIO.value)
    window.mode_combo.setCurrentIndex(audio_index)

    entry = AnalyzedEntry(
        source_url="https://example.com/watch?v=snapshot",
        video_id="snapshot",
        playlist_id="playlist",
        playlist_title="Playlist",
        playlist_index=7,
        playlist_count=10,
        original_title="Original snapshot title",
        cleaned_title="Cleaned current title",
        channel="Channel",
        artist="Artist",
    )
    window._on_analysis_batch([(entry, "")])

    assert window.queue_model.rowCount() == 1
    analyzed = window.queue_model.items[0]
    assert analyzed.download_mode is DownloadMode.VIDEO
    assert analyzed.track_number == 7
    assert Path(analyzed.final_media_path or "") == (
        snapshot_destination / "Original snapshot title.mp4"
    )

    persisted = window.queue_manager.get(analyzed.id)
    assert persisted is not None
    assert persisted.download_mode is DownloadMode.VIDEO
    assert persisted.track_number == 7
    assert Path(persisted.final_media_path or "").parent == snapshot_destination

    _close(window, database)
    del app
