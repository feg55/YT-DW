from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableView

from openmediadl.application import ApplicationPaths
from openmediadl.core.analyzer import AnalyzedEntry
from openmediadl.core.ffmpeg_service import FFmpegService
from openmediadl.core.queue_manager import QueueManager
from openmediadl.core.thumbnail_service import ThumbnailService
from openmediadl.database.connection import Database
from openmediadl.database.repositories import SettingsRepository, WindowStateRepository
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import (
    AppearanceSettings,
    AppSettings,
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
) -> MainWindow:
    return MainWindow(
        paths,
        QueueManager(database),
        SettingsRepository(database),
        WindowStateRepository(database),
        FFmpegService(paths.bundled_tools_dir),
        ThumbnailService(paths.cache_dir / "thumbnails"),
        translator=translator,
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

    assert window.windowTitle() == "OpenMediaDL"
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


def test_settings_dialog_defaults_and_persists_appearance(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    paths = _paths(tmp_path)
    database = Database(paths.database_file)
    repository = SettingsRepository(database)
    settings = AppSettings()
    dialog = SettingsDialog(
        settings,
        FFmpegService(paths.bundled_tools_dir),
        translator=Translator(LanguagePreference.ENGLISH),
    )

    assert dialog.theme.currentData() == ThemePreference.DARK.value
    assert dialog.language.currentData() == LanguagePreference.SYSTEM.value
    assert dialog.remember_last_tab.isChecked()

    dialog.theme.setCurrentIndex(dialog.theme.findData(ThemePreference.LIGHT.value))
    dialog.language.setCurrentIndex(dialog.language.findData(LanguagePreference.RUSSIAN.value))
    dialog.remember_last_tab.setChecked(False)
    dialog.apply_to(settings)
    repository.save(settings)

    restored = repository.load()
    assert restored.appearance is not None
    assert restored.appearance.theme is ThemePreference.LIGHT
    assert restored.appearance.language is LanguagePreference.RUSSIAN
    assert not restored.appearance.remember_last_tab

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
