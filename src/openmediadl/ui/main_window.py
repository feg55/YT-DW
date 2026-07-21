"""State-preserving tabbed desktop interface for YT-DW."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QStandardPaths, Qt, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from openmediadl.appearance import AppearanceController
from openmediadl.core.analyzer import AnalysisOptions, AnalyzedEntry, parse_urls
from openmediadl.core.ffmpeg_service import FFmpegInstallation, FFmpegService
from openmediadl.core.filename_service import ensure_unique_path, sanitize_filename
from openmediadl.core.metadata_cleaner import clean_track_title
from openmediadl.core.queue_manager import QueueBusyError, QueueManager
from openmediadl.core.runtime_tools import RuntimeToolsService, RuntimeToolsStatus
from openmediadl.core.thumbnail_service import ThumbnailService
from openmediadl.database.repositories import SettingsRepository, WindowStateRepository
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import (
    AppearanceSettings,
    DownloadSettings,
    MetadataSettings,
    VideoQuality,
)
from openmediadl.i18n import Translator
from openmediadl.ui.download_table import DownloadTable
from openmediadl.ui.models import Column, DownloadTableModel
from openmediadl.ui.settings_dialog import SettingsDialog
from openmediadl.workers.analysis_worker import AnalysisWorker
from openmediadl.workers.download_worker import DownloadQueueWorker
from openmediadl.workers.ffmpeg_worker import FFmpegCheckWorker
from openmediadl.workers.runtime_tools_worker import RuntimeToolsWorker

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from openmediadl.application import ApplicationPaths


class MainWindow(QMainWindow):
    def __init__(
        self,
        paths: ApplicationPaths,
        queue_manager: QueueManager,
        settings_repository: SettingsRepository,
        window_state_repository: WindowStateRepository,
        ffmpeg_service: FFmpegService,
        thumbnail_service: ThumbnailService,
        *,
        translator: Translator | None = None,
        appearance_controller: AppearanceController | None = None,
        runtime_tools_service: RuntimeToolsService | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.queue_manager = queue_manager
        self.settings_repository = settings_repository
        self.window_state_repository = window_state_repository
        self.ffmpeg_service = ffmpeg_service
        self.thumbnail_service = thumbnail_service
        self.runtime_tools_service = runtime_tools_service
        self.settings = settings_repository.load()
        appearance = self.settings.appearance or AppearanceSettings()
        self.translator = translator or Translator(appearance.language)
        self._tr = self.translator.tr
        application = QApplication.instance()
        self.appearance_controller = appearance_controller
        if self.appearance_controller is None and isinstance(application, QApplication):
            self.appearance_controller = AppearanceController(application)
        if self.appearance_controller is not None:
            self.appearance_controller.apply(appearance.theme)
        self._analysis_worker: AnalysisWorker | None = None
        self._download_worker: DownloadQueueWorker | None = None
        self._ffmpeg_check_worker: FFmpegCheckWorker | RuntimeToolsWorker | None = None
        self._ffmpeg_installation: FFmpegInstallation | None = None
        self._ffmpeg_checked_directory: str | None = None
        self._ffmpeg_recheck_requested = False
        self._js_runtime_path: str | None = None
        self._closing = False
        self._last_persist: dict[str, tuple[float, DownloadStatus]] = {}
        self._pending_thumbnails: dict[str, str] = {}
        self._analysis_mode: DownloadMode | None = None
        self._analysis_metadata: MetadataSettings | None = None
        self._analysis_destination: str | None = None
        self._applied_destination = ""
        self._build_ui()
        self._load_settings_into_ui()
        self.queue_model.replace_items(self.queue_manager.list())
        self._restore_window_state()
        self._update_queue_summary()
        self._check_ffmpeg()

    def _build_ui(self) -> None:
        self.setWindowTitle(self._tr("app.title"))
        self.resize(1480, 900)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 10)

        self.legal_notice = QLabel(self._tr("notice.legal_full"))
        self.legal_notice.setWordWrap(True)
        self.legal_notice.setStyleSheet(
            "padding: 6px; background: palette(alternate-base); border-radius: 4px;"
        )
        layout.addWidget(self.legal_notice)
        self.queue_model = DownloadTableModel(self, translator=self.translator)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.analyze_tab_index = self.tabs.addTab(
            self._build_analyze_tab(), self._tr("tab.analyze")
        )
        self.review_tab_index = self.tabs.addTab(self._build_review_tab(), self._tr("tab.review"))
        self.download_tab_index = self.tabs.addTab(
            self._build_download_tab(), self._tr("tab.download")
        )
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)
        self.runtime_tools_progress = QProgressBar()
        self.runtime_tools_progress.setRange(0, 1000)
        self.runtime_tools_progress.setValue(0)
        self.runtime_tools_progress.setFixedWidth(190)
        self.runtime_tools_progress.setTextVisible(False)
        self.runtime_tools_progress.hide()
        self.runtime_tools_retry_button = QPushButton(self._tr("action.retry_tools"))
        self.runtime_tools_retry_button.clicked.connect(self._check_ffmpeg)
        self.runtime_tools_retry_button.hide()
        self.statusBar().addPermanentWidget(self.runtime_tools_progress)
        self.statusBar().addPermanentWidget(self.runtime_tools_retry_button)

    def _build_analyze_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        self.analyze_intro = QLabel(self._tr("source.intro"))
        self.analyze_intro.setWordWrap(True)
        layout.addWidget(self.analyze_intro)

        self.input_group = QGroupBox(self._tr("group.media_urls"))
        input_layout = QVBoxLayout(self.input_group)
        self.url_input = QTextEdit()
        self.url_input.setPlaceholderText(self._tr("placeholder.media_urls"))
        self.url_input.setMinimumHeight(150)
        input_layout.addWidget(self.url_input)
        layout.addWidget(self.input_group)

        self.output_group = QGroupBox(self._tr("group.output"))
        output_layout = QGridLayout(self.output_group)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self._tr("mode.audio"), DownloadMode.AUDIO.value)
        self.mode_combo.addItem(self._tr("mode.video"), DownloadMode.VIDEO.value)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.quality_combo = QComboBox()
        for quality in VideoQuality:
            self.quality_combo.addItem(
                quality.value if quality is not VideoQuality.BEST else self._tr("quality.best"),
                quality.value,
            )
        self.quality_combo.setEnabled(False)
        self.settings_button = QPushButton(self._tr("action.settings"))
        self.settings_button.clicked.connect(self._show_settings)
        self.destination = QLineEdit()
        self.destination.editingFinished.connect(self._destination_changed)
        self.destination_browse_button = QPushButton(self._tr("action.browse"))
        self.destination_browse_button.clicked.connect(self._browse_destination)
        self.mode_label = QLabel(self._tr("label.mode"))
        self.quality_label = QLabel(self._tr("label.video_quality"))
        self.destination_label = QLabel(self._tr("label.destination"))
        output_layout.addWidget(self.mode_label, 0, 0)
        output_layout.addWidget(self.mode_combo, 0, 1)
        output_layout.addWidget(self.quality_label, 0, 2)
        output_layout.addWidget(self.quality_combo, 0, 3)
        output_layout.addWidget(self.settings_button, 0, 4)
        output_layout.addWidget(self.destination_label, 1, 0)
        output_layout.addWidget(self.destination, 1, 1, 1, 3)
        output_layout.addWidget(self.destination_browse_button, 1, 4)
        output_layout.setColumnStretch(3, 1)
        layout.addWidget(self.output_group)

        actions = QHBoxLayout()
        self.analysis_phase_label = QLabel(self._tr("status.ready_analyze"))
        self.analysis_phase_label.setWordWrap(True)
        self.cancel_analysis_button = QPushButton(self._tr("action.cancel_analysis"))
        self.cancel_analysis_button.setEnabled(False)
        self.cancel_analysis_button.clicked.connect(self._cancel_analysis)
        self.analyze_button = QPushButton(self._tr("action.analyze"))
        self.analyze_button.setDefault(True)
        self.analyze_button.clicked.connect(self._start_analysis)
        actions.addWidget(self.analysis_phase_label, 1)
        actions.addWidget(self.cancel_analysis_button)
        actions.addWidget(self.analyze_button)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def _build_review_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.review_intro = QLabel(self._tr("review.intro"))
        self.review_intro.setWordWrap(True)
        layout.addWidget(self.review_intro)
        self.metadata_group = QGroupBox(self._tr("group.metadata_rules"))
        metadata_layout = QGridLayout(self.metadata_group)
        definitions = (
            ("use_channel_name_as_artist", "metadata.channel_artist"),
            ("remove_channel_name_from_title", "metadata.remove_channel_title"),
            ("remove_labels", "metadata.remove_labels"),
            ("use_playlist_title_as_album", "metadata.playlist_album"),
            ("use_playlist_index_as_track_number", "metadata.playlist_track"),
            ("use_cleaned_title_as_filename", "metadata.cleaned_filename"),
            ("embed_thumbnail_as_cover", "metadata.embed_cover"),
            ("crop_cover_to_square", "metadata.crop_cover"),
            ("save_cover_as_separate_jpeg", "metadata.separate_jpeg"),
            ("store_original_url_in_comment", "metadata.url_comment"),
            ("store_upload_year", "metadata.upload_year"),
            ("use_channel_name_as_album_artist", "metadata.channel_album_artist"),
        )
        self.metadata_label_keys = dict(definitions)
        self.metadata_checks: dict[str, QCheckBox] = {}
        for index, (name, label_key) in enumerate(definitions):
            checkbox = QCheckBox(self._tr(label_key))
            checkbox.toggled.connect(self._metadata_setting_changed)
            self.metadata_checks[name] = checkbox
            metadata_layout.addWidget(checkbox, index // 4, index % 4)
        self.recalculate_button = QPushButton(self._tr("action.recalculate_metadata"))
        self.recalculate_button.clicked.connect(self._recalculate_metadata)
        metadata_layout.addWidget(self.recalculate_button, 3, 0, 1, 2)
        layout.addWidget(self.metadata_group)

        self.table = DownloadTable(queue_model=self.queue_model)
        self.queue_model.item_edited.connect(self._manual_item_edited)
        self.queue_model.selection_changed.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        review_actions = QHBoxLayout()
        self.review_summary_label = QLabel(self._tr("status.no_analyzed_items"))
        self.to_downloads_button = QPushButton(self._tr("action.continue_downloads"))
        self.to_downloads_button.clicked.connect(self._show_download_tab)
        review_actions.addWidget(self.review_summary_label, 1)
        review_actions.addWidget(self.to_downloads_button)
        layout.addLayout(review_actions)
        return page

    def _build_download_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.queue_summary_label = QLabel(self._tr("status.no_queue_items"))
        self.queue_summary_label.setWordWrap(True)
        layout.addWidget(self.queue_summary_label)
        self.download_table = DownloadTable(
            queue_model=self.queue_model,
            read_only=True,
        )
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.download_table)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 4, 0, 0)
        queue_controls = QHBoxLayout()
        self.download_button = QPushButton(self._tr("action.download_selected"))
        self.download_button.clicked.connect(self._start_downloads)
        self.pause_button = QPushButton(self._tr("action.pause_queue"))
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.cancel_selected_button = QPushButton(self._tr("action.cancel_selected"))
        self.cancel_selected_button.clicked.connect(self._cancel_selected)
        self.cancel_pending_button = QPushButton(self._tr("action.cancel_all_pending"))
        self.cancel_pending_button.clicked.connect(self._cancel_all_pending)
        self.retry_button = QPushButton(self._tr("action.retry_failed"))
        self.retry_button.clicked.connect(self._retry_failed)
        self.remove_completed_button = QPushButton(self._tr("action.remove_completed"))
        self.remove_completed_button.clicked.connect(self._remove_completed)
        self.clear_all_button = QPushButton(self._tr("action.clear_all"))
        self.clear_all_button.clicked.connect(self._clear_all)
        self.open_output_button = QPushButton(self._tr("action.open_output"))
        self.open_output_button.clicked.connect(self._open_output_directory)
        self.open_logs_button = QPushButton(self._tr("action.open_logs"))
        self.open_logs_button.clicked.connect(self._open_log_directory)
        for widget in (
            self.download_button,
            self.pause_button,
            self.cancel_selected_button,
            self.cancel_pending_button,
            self.retry_button,
            self.remove_completed_button,
            self.open_output_button,
            self.open_logs_button,
        ):
            queue_controls.addWidget(widget)
        queue_controls.addStretch(1)
        queue_controls.addWidget(self.clear_all_button)
        lower_layout.addLayout(queue_controls)

        progress_row = QHBoxLayout()
        self.phase_label = QLabel(self._tr("status.ready"))
        self.phase_label.setMinimumWidth(230)
        self.current_progress = QProgressBar()
        self.current_progress.setRange(0, 1000)
        self.current_progress.setValue(0)
        self.progress_details = QLabel("")
        progress_row.addWidget(self.phase_label)
        progress_row.addWidget(self.current_progress, 1)
        progress_row.addWidget(self.progress_details)
        lower_layout.addLayout(progress_row)
        self.log_panel = QTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumHeight(125)
        self.log_panel.setPlaceholderText(self._tr("placeholder.activity_log"))
        lower_layout.addWidget(self.log_panel)
        splitter.addWidget(lower)
        splitter.setSizes([610, 210])
        layout.addWidget(splitter, 1)
        return page

    def _load_settings_into_ui(self) -> None:
        metadata = self.settings.metadata or MetadataSettings()
        downloads = self.settings.downloads or DownloadSettings()
        for name, checkbox in self.metadata_checks.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(getattr(metadata, name)))
            checkbox.blockSignals(False)
        default_downloads = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        if not downloads.destination_directory:
            downloads.destination_directory = default_downloads or str(Path.home() / "Downloads")
        self.destination.setText(downloads.destination_directory)
        self._applied_destination = downloads.destination_directory
        quality_index = self.quality_combo.findData(downloads.video_quality.value)
        self.quality_combo.setCurrentIndex(max(0, quality_index))
        self.settings.downloads = downloads

    def _sync_settings_from_ui(self) -> None:
        metadata = self.settings.metadata or MetadataSettings()
        for name, checkbox in self.metadata_checks.items():
            setattr(metadata, name, checkbox.isChecked())
        downloads = self.settings.downloads or DownloadSettings()
        downloads.destination_directory = self.destination.text().strip()
        downloads.video_quality = VideoQuality(str(self.quality_combo.currentData()))
        self.settings.metadata = metadata
        self.settings.downloads = downloads

    def _save_settings(self) -> None:
        self._sync_settings_from_ui()
        self.settings_repository.save(self.settings)

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(self._tr("app.title"))
        self.legal_notice.setText(self._tr("notice.legal_full"))
        self.tabs.setTabText(self.analyze_tab_index, self._tr("tab.analyze"))
        self.analyze_intro.setText(self._tr("source.intro"))
        self.input_group.setTitle(self._tr("group.media_urls"))
        self.url_input.setPlaceholderText(self._tr("placeholder.media_urls"))
        self.output_group.setTitle(self._tr("group.output"))
        self.mode_label.setText(self._tr("label.mode"))
        self.quality_label.setText(self._tr("label.video_quality"))
        self.destination_label.setText(self._tr("label.destination"))
        audio_index = self.mode_combo.findData(DownloadMode.AUDIO.value)
        video_index = self.mode_combo.findData(DownloadMode.VIDEO.value)
        if audio_index >= 0:
            self.mode_combo.setItemText(audio_index, self._tr("mode.audio"))
        if video_index >= 0:
            self.mode_combo.setItemText(video_index, self._tr("mode.video"))
        best_index = self.quality_combo.findData(VideoQuality.BEST.value)
        if best_index >= 0:
            self.quality_combo.setItemText(best_index, self._tr("quality.best"))
        self.settings_button.setText(self._tr("action.settings"))
        self.destination_browse_button.setText(self._tr("action.browse"))
        self.cancel_analysis_button.setText(self._tr("action.cancel_analysis"))
        self.analyze_button.setText(self._tr("action.analyze"))
        self.review_intro.setText(self._tr("review.intro"))
        self.metadata_group.setTitle(self._tr("group.metadata_rules"))
        for name, checkbox in self.metadata_checks.items():
            checkbox.setText(self._tr(self.metadata_label_keys[name]))
        self.recalculate_button.setText(self._tr("action.recalculate_metadata"))
        self.to_downloads_button.setText(self._tr("action.continue_downloads"))
        self.download_button.setText(self._tr("action.download_selected"))
        pause_key = (
            "action.resume_queue"
            if self._download_worker is not None and self._download_worker.paused
            else "action.pause_queue"
        )
        self.pause_button.setText(self._tr(pause_key))
        self.cancel_selected_button.setText(self._tr("action.cancel_selected"))
        self.cancel_pending_button.setText(self._tr("action.cancel_all_pending"))
        self.retry_button.setText(self._tr("action.retry_failed"))
        self.remove_completed_button.setText(self._tr("action.remove_completed"))
        self.clear_all_button.setText(self._tr("action.clear_all"))
        self.runtime_tools_retry_button.setText(self._tr("action.retry_tools"))
        self.open_output_button.setText(self._tr("action.open_output"))
        self.open_logs_button.setText(self._tr("action.open_logs"))
        self.log_panel.setPlaceholderText(self._tr("placeholder.activity_log"))
        analysis_idle = not self._analysis_worker or not self._analysis_worker.isRunning()
        download_idle = not self._download_worker or not self._download_worker.isRunning()
        if analysis_idle:
            self.analysis_phase_label.setText(self._tr("status.ready_analyze"))
        if download_idle:
            self.phase_label.setText(self._tr("status.ready"))
        self.queue_model.retranslate()
        self._update_queue_summary()

    def _persist_workspace_state(self) -> None:
        self.window_state_repository.set(
            "main.active_tab", str(self.tabs.currentIndex()).encode("ascii")
        )
        self.window_state_repository.set(
            "analysis.url_draft", self.url_input.toPlainText().encode("utf-8")
        )
        mode = str(self.mode_combo.currentData() or DownloadMode.AUDIO.value)
        self.window_state_repository.set("analysis.mode", mode.encode("ascii"))

    @Slot()
    def _show_download_tab(self) -> None:
        self.tabs.setCurrentIndex(self.download_tab_index)

    @Slot(int)
    def _tab_changed(self, _index: int) -> None:
        if self.destination.text().strip() != self._applied_destination:
            self._destination_changed()
        else:
            self._save_settings()
        self._persist_workspace_state()
        self._update_queue_summary()

    @Slot(str)
    def _on_analysis_phase(self, phase: str) -> None:
        translated = self._translated_phase(phase)
        self.analysis_phase_label.setText(translated)
        self.statusBar().showMessage(translated)

    def _translated_phase(self, phase: str) -> str:
        keys = {
            "Analyzing": "phase.analyzing",
            "Completed": "phase.completed",
            "Converting": "phase.converting",
            "Downloading audio": "phase.downloading_audio",
            "Downloading media": "phase.downloading_media",
            "Downloading thumbnail": "phase.downloading_thumbnail",
            "Downloading video": "phase.downloading_video",
            "Embedding cover": "phase.embedding_cover",
            "Failed": "phase.failed",
            "Finishing thumbnails": "phase.finishing_thumbnails",
            "Merging": "phase.merging",
            "Processing": "phase.processing",
            "Retrying with FFmpeg": "phase.retrying_ffmpeg",
            "Verifying output": "phase.verifying_output",
            "Writing metadata": "phase.writing_metadata",
        }
        key = keys.get(phase)
        if key is not None:
            return self._tr(key)
        prefix = "Analyzing — "
        suffix = " found"
        if phase.startswith(prefix) and phase.endswith(suffix):
            count = phase[len(prefix) : -len(suffix)]
            return self._tr("phase.analyzing_found", count=count)
        return phase

    def _set_source_options_enabled(self, enabled: bool) -> None:
        self.mode_combo.setEnabled(enabled)
        mode = DownloadMode(str(self.mode_combo.currentData()))
        self.quality_combo.setEnabled(enabled and mode is DownloadMode.VIDEO)
        self.destination.setEnabled(enabled)
        self.destination_browse_button.setEnabled(enabled)
        self.metadata_group.setEnabled(enabled)
        self.settings_button.setEnabled(enabled)

    def _update_queue_summary(self) -> None:
        items = self.queue_model.items
        if not items:
            self.review_summary_label.setText(self._tr("status.no_analyzed_items"))
            self.queue_summary_label.setText(self._tr("status.no_queue_items"))
            self.tabs.setTabText(self.review_tab_index, self._tr("tab.review"))
            self.tabs.setTabText(self.download_tab_index, self._tr("tab.download"))
            return
        selected = sum(item.selected for item in items)
        active = sum(item.status.is_active for item in items)
        completed = sum(item.status is DownloadStatus.COMPLETED for item in items)
        failed = sum(item.status is DownloadStatus.FAILED for item in items)
        summary = self._tr("queue.summary.base", total=len(items), selected=selected)
        if active:
            summary += self._tr("queue.summary.active", count=active)
        if completed:
            summary += self._tr("queue.summary.completed", count=completed)
        if failed:
            summary += self._tr("queue.summary.failed", count=failed)
        self.review_summary_label.setText(summary)
        self.queue_summary_label.setText(summary)
        self.tabs.setTabText(self.review_tab_index, self._tr("tab.review_count", count=len(items)))
        self.tabs.setTabText(
            self.download_tab_index, self._tr("tab.download_count", count=selected)
        )

    @Slot()
    def _start_analysis(self) -> None:
        if self._analysis_worker and self._analysis_worker.isRunning():
            return
        if self._download_worker and self._download_worker.isRunning():
            return
        try:
            urls = parse_urls(self.url_input.toPlainText())
        except ValueError as error:
            QMessageBox.warning(self, self._tr("dialog.invalid_url.title"), str(error))
            return
        if not urls:
            QMessageBox.information(
                self,
                self._tr("dialog.no_urls.title"),
                self._tr("dialog.no_urls.message"),
            )
            return
        self._save_settings()
        metadata = replace(self.settings.metadata or MetadataSettings())
        downloads = self.settings.downloads or DownloadSettings()
        mode = DownloadMode(str(self.mode_combo.currentData()))
        self._analysis_mode = mode
        self._analysis_metadata = metadata
        self._analysis_destination = downloads.destination_directory
        options = AnalysisOptions(
            remove_channel=metadata.remove_channel_name_from_title,
            remove_labels=metadata.remove_labels,
            use_channel_artist=metadata.use_channel_name_as_artist,
            use_playlist_album=metadata.use_playlist_title_as_album,
            use_playlist_track=metadata.use_playlist_index_as_track_number,
            cookies_browser=downloads.cookie_browser.value if downloads.cookie_browser else None,
            cookies_profile=downloads.cookie_profile,
            socket_timeout=downloads.socket_timeout,
            retries=downloads.retry_count,
            js_runtime_path=self._js_runtime_path,
        )

        def load_thumbnail(url: str, video_id: str) -> Path | None:
            result = self.thumbnail_service.download(
                url, video_id or url, min_size=180, max_size=480
            )
            return result.path

        self._analysis_worker = AnalysisWorker(urls, options, load_thumbnail)
        self._analysis_worker.batch_ready.connect(self._on_analysis_batch)
        self._analysis_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._analysis_worker.item_error.connect(self._on_analysis_error)
        self._analysis_worker.phase_changed.connect(self._on_analysis_phase)
        self._analysis_worker.analysis_finished.connect(self._on_analysis_result)
        self._analysis_worker.finished.connect(self._analysis_thread_done)
        self.analyze_button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.download_button.setEnabled(False)
        self.clear_all_button.setEnabled(False)
        self._set_source_options_enabled(False)
        self.analysis_phase_label.setText(self._tr("status.starting_analysis"))
        self._append_log(self._tr("log.analyzing_urls", count=len(urls)))
        self._analysis_worker.start()
        self.tabs.setCurrentIndex(self.review_tab_index)

    @Slot(object)
    def _on_analysis_batch(self, batch: object) -> None:
        if not isinstance(batch, list):
            return
        items: list[DownloadItem] = []
        mode = self._analysis_mode or DownloadMode(str(self.mode_combo.currentData()))
        metadata = self._analysis_metadata or self.settings.metadata or MetadataSettings()
        reserved_paths = {
            str(Path(existing.final_media_path).resolve()).casefold()
            for existing in self.queue_model.items
            if existing.final_media_path
        }
        for value in batch:
            if (
                not isinstance(value, tuple)
                or len(value) != 2
                or not isinstance(value[0], AnalyzedEntry)
            ):
                continue
            entry, thumbnail_path = value
            thumbnail_path = thumbnail_path or self._pending_thumbnails.pop(entry.source_url, "")
            track = entry.playlist_index if metadata.use_playlist_index_as_track_number else None
            item = DownloadItem.new(
                entry.source_url,
                video_id=entry.video_id or None,
                playlist_id=entry.playlist_id or None,
                playlist_title=entry.playlist_title or None,
                playlist_index=entry.playlist_index,
                playlist_count=entry.playlist_count,
                original_title=entry.original_title,
                cleaned_title=entry.cleaned_title,
                channel=entry.channel,
                uploader=entry.uploader,
                artist=entry.artist,
                album_artist=entry.album_artist,
                album=entry.album,
                track_number=track,
                upload_date=entry.upload_date or None,
                duration=entry.duration,
                thumbnail_url=entry.thumbnail_url or None,
                cached_thumbnail_path=thumbnail_path or None,
                download_mode=mode,
                status=DownloadStatus.READY,
            )
            proposed_path = self._proposed_path(
                item,
                reserved_paths,
                destination=self._analysis_destination,
                metadata=metadata,
            )
            item.final_media_path = str(proposed_path)
            reserved_paths.add(str(proposed_path.resolve()).casefold())
            persisted = self.queue_manager.add(
                item,
                skip_if_archived=False,
            )
            items.append(persisted)
        self.queue_model.add_items(items)
        if items:
            last = items[-1]
            current = last.playlist_index or len(self.queue_model.items)
            count = last.playlist_count
            self.analysis_phase_label.setText(
                self._tr("status.analyzed_total", current=current, total=count)
                if count
                else self._tr("status.analyzed", current=current)
            )
            self._update_queue_summary()

    @Slot(str, str)
    def _on_thumbnail_ready(self, source_url: str, thumbnail_path: str) -> None:
        item = self.queue_model.item_by_source_url(source_url)
        if item is None:
            self._pending_thumbnails[source_url] = thumbnail_path
            return
        item.cached_thumbnail_path = thumbnail_path
        item.touch()
        self.queue_manager.save(item)
        self.queue_model.update_item(item)

    @Slot(str, str, str)
    def _on_analysis_error(self, category: str, message: str, technical: str) -> None:
        self.analysis_phase_label.setText(self._tr("status.analysis_error", message=message))
        self._append_log(self._tr("log.analysis_error", message=message))
        LOGGER.error("Analysis error [%s]: %s", category, technical)

    @Slot(bool, int)
    def _on_analysis_result(self, cancelled: bool, count: int) -> None:
        self.analyze_button.setEnabled(True)
        self.cancel_analysis_button.setEnabled(False)
        self.analysis_phase_label.setText(
            self._tr("status.analysis_cancelled")
            if cancelled
            else self._tr("status.analysis_complete", count=count)
        )
        self._update_queue_summary()
        result_key = "log.analysis_cancelled" if cancelled else "log.analysis_completed"
        self._append_log(self._tr(result_key, count=count))

    @Slot()
    def _analysis_thread_done(self) -> None:
        if self._analysis_worker:
            self._analysis_worker.deleteLater()
        self._analysis_worker = None
        self._analysis_mode = None
        self._analysis_metadata = None
        self._analysis_destination = None
        self.analyze_button.setEnabled(True)
        self.cancel_analysis_button.setEnabled(False)
        if not self._download_worker or not self._download_worker.isRunning():
            self._set_source_options_enabled(True)
            self.download_button.setEnabled(
                bool(self._ffmpeg_installation and self._ffmpeg_installation.available)
            )
            self.clear_all_button.setEnabled(True)

    def _cancel_analysis(self) -> None:
        if self._analysis_worker:
            self._analysis_worker.cancel()
            self.cancel_analysis_button.setEnabled(False)

    def _proposed_path(
        self,
        item: DownloadItem,
        reserved_paths: set[str] | None = None,
        *,
        destination: str | Path | None = None,
        metadata: MetadataSettings | None = None,
    ) -> Path:
        metadata = metadata or self.settings.metadata or MetadataSettings()
        destination_path = Path(destination or self.destination.text().strip())
        title = (
            item.cleaned_title if metadata.use_cleaned_title_as_filename else item.original_title
        )
        extension = ".m4a" if item.download_mode is DownloadMode.AUDIO else ".mp4"
        proposed = (
            destination_path
            / f"{sanitize_filename(title or item.video_id or 'untitled')}{extension}"
        )
        reserved = (
            reserved_paths
            if reserved_paths is not None
            else {
                str(Path(existing.final_media_path).resolve()).casefold()
                for existing in self.queue_model.items
                if existing.id != item.id and existing.final_media_path
            }
        )
        return ensure_unique_path(
            proposed,
            preferred_suffix=item.playlist_index or item.video_id,
            exists=lambda path: path.exists() or str(path.resolve()).casefold() in reserved,
        )

    def _selection_changed(self, item: DownloadItem) -> None:
        self.queue_manager.save(item)
        self._update_queue_summary()

    @Slot(object, int)
    def _manual_item_edited(self, item: DownloadItem, column: int) -> None:
        if Column(column) is Column.CLEANED_TITLE and not self._should_reuse_audio(item):
            item.final_media_path = str(self._proposed_path(item))
        self.queue_manager.save(item)
        self.queue_model.update_item(item)

    def _recalculate_metadata(self) -> None:
        rows = self.table.selected_rows()
        items = self.queue_model.items_at_rows(rows) if rows else self.queue_model.selected_items()
        items = [
            item
            for item in items
            if item.status
            in {
                DownloadStatus.PENDING,
                DownloadStatus.READY,
                DownloadStatus.FAILED,
                DownloadStatus.CANCELLED,
            }
        ]
        if not items:
            return
        metadata = self.settings.metadata or MetadataSettings()
        for item in items:
            if not item.title_manually_edited:
                item.cleaned_title = clean_track_title(
                    item.original_title,
                    item.channel if metadata.remove_channel_name_from_title else "",
                    remove_labels=metadata.remove_labels,
                )
            if not item.artist_manually_edited:
                item.artist = (
                    item.channel
                    if metadata.use_channel_name_as_artist
                    else (item.artist or item.channel)
                )
            if not item.album_manually_edited:
                item.album = (
                    (item.playlist_title or "") if metadata.use_playlist_title_as_album else ""
                )
            item.album_artist = (
                item.channel if metadata.use_channel_name_as_album_artist else item.artist
            )
            if not item.track_manually_edited:
                item.track_number = (
                    item.playlist_index if metadata.use_playlist_index_as_track_number else None
                )
            if not self._should_reuse_audio(item):
                item.final_media_path = str(self._proposed_path(item))
            self.queue_manager.save(item)
            self.queue_model.update_item(item)
        self._append_log(self._tr("log.recalculated", count=len(items)))

    @staticmethod
    def _should_reuse_audio(item: DownloadItem) -> bool:
        return (
            item.download_mode is DownloadMode.AUDIO
            and (
                item.progress_percentage >= 99.9
                or item.error_category in {"metadata_writing_failed", "thumbnail_conversion_failed"}
            )
            and bool(item.final_media_path)
            and Path(item.final_media_path or "").is_file()
        )

    def _start_downloads(self) -> None:
        if self._download_worker and self._download_worker.isRunning():
            return
        if self._analysis_worker and self._analysis_worker.isRunning():
            return
        self._save_settings()
        downloads = self.settings.downloads or DownloadSettings()
        metadata = self.settings.metadata or MetadataSettings()
        if not downloads.destination_directory:
            QMessageBox.warning(
                self,
                self._tr("dialog.destination_required.title"),
                self._tr("dialog.destination_required.message"),
            )
            return
        try:
            Path(downloads.destination_directory).mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QMessageBox.critical(self, self._tr("dialog.destination_unavailable.title"), str(error))
            return
        configured_directory = downloads.ffmpeg_directory or None
        installation = (
            self._ffmpeg_installation
            if self._ffmpeg_checked_directory == configured_directory
            else None
        )
        if installation is None:
            self._check_ffmpeg()
            QMessageBox.information(
                self,
                self._tr("dialog.checking_ffmpeg.title"),
                self._tr("dialog.checking_ffmpeg.message"),
            )
            return
        if not installation.available:
            QMessageBox.critical(
                self,
                self._tr("dialog.ffmpeg_required.title"),
                self._tr("dialog.ffmpeg_required.message"),
            )
            return
        effective_downloads = replace(downloads)
        if installation.directory is not None:
            ffmpeg_directory = self.ffmpeg_service.ensure_on_path(installation.directory)
            # Keep the resolved managed/bundled/PATH location runtime-only.
            # ``downloads.ffmpeg_directory`` remains an explicit user override.
            effective_downloads.ffmpeg_directory = str(ffmpeg_directory)
        candidates: list[DownloadItem] = []
        for item in self.queue_model.selected_items():
            prepared = self.queue_manager.prepare_for_download(
                item.id,
                skip_archived=downloads.skip_download_archive,
            )
            if prepared is None:
                continue
            self.queue_model.update_item(prepared)
            if prepared.status not in {DownloadStatus.COMPLETED, DownloadStatus.SKIPPED}:
                candidates.append(prepared)
        if not candidates:
            QMessageBox.information(
                self,
                self._tr("dialog.nothing_selected.title"),
                self._tr("dialog.nothing_selected.message"),
            )
            return
        self._download_worker = DownloadQueueWorker(
            candidates,
            effective_downloads,
            metadata,
            self.paths.archive_file,
            js_runtime_path=self._js_runtime_path,
        )
        self._download_worker.item_updated.connect(self._on_item_updated)
        self._download_worker.phase_changed.connect(self._on_download_phase)
        self._download_worker.log_message.connect(self._append_log)
        self._download_worker.overall_progress.connect(self._on_overall_progress)
        self._download_worker.queue_finished.connect(self._on_queue_result)
        self._download_worker.finished.connect(self._download_thread_done)
        self.download_button.setEnabled(False)
        self.clear_all_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.analyze_button.setEnabled(False)
        self.queue_model.set_locked_items({item.id for item in candidates})
        self._set_source_options_enabled(False)
        if self.queue_manager.is_paused:
            self._download_worker.set_paused(True)
            self.pause_button.setText(self._tr("action.resume_queue"))
        self._append_log(self._tr("log.starting_queue", count=len(candidates)))
        self._update_queue_summary()
        self._download_worker.start()

    @Slot(object)
    def _on_item_updated(self, value: object) -> None:
        if not isinstance(value, DownloadItem):
            return
        item = value
        current = self.queue_model.item_by_id(item.id)
        if (
            current is not None
            and current.status is DownloadStatus.CANCELLED
            and not item.status.is_terminal
        ):
            # Ignore nonterminal snapshots queued around a cancel request. A terminal
            # completion/failure is still the truthful outcome and is accepted.
            return
        self.queue_model.update_item(item)
        now = time.monotonic()
        previous = self._last_persist.get(item.id)
        terminal = item.status in {
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
            DownloadStatus.SKIPPED,
        }
        status_changed = previous is None or previous[1] is not item.status
        if terminal:
            persisted: DownloadItem | None
            if item.status is DownloadStatus.COMPLETED:
                self.queue_manager.save(item)
                persisted = self.queue_manager.mark_completed(
                    item.id, final_media_path=item.final_media_path
                )
            elif item.status is DownloadStatus.FAILED:
                self.queue_manager.save(item)
                persisted = self.queue_manager.mark_failed(
                    item.id,
                    error_category=item.error_category,
                    error_message=item.error_message,
                    technical_error=item.technical_error,
                )
            elif item.status is DownloadStatus.SKIPPED:
                persisted = self.queue_manager.mark_skipped(
                    item.id, item.error_message or "Already downloaded"
                )
            else:
                persisted = self.queue_manager.cancel(item.id)
            if persisted:
                self.queue_model.update_item(persisted)
            self._last_persist.pop(item.id, None)
        elif status_changed or (previous is not None and now - previous[0] >= 1.0):
            self.queue_manager.save(item)
            self._last_persist[item.id] = (now, item.status)

        self.current_progress.setValue(round(item.progress_percentage * 10))
        self.progress_details.setText(_progress_details(item))
        if terminal:
            self._update_queue_summary()

    @Slot(str, str)
    def _on_download_phase(self, item_id: str, phase: str) -> None:
        item = self.queue_model.item_by_id(item_id)
        title = item.cleaned_title if item else ""
        translated = self._translated_phase(phase)
        self.phase_label.setText(f"{translated}: {title}" if title else translated)

    @Slot(int, int)
    def _on_overall_progress(self, completed: int, total: int) -> None:
        self.statusBar().showMessage(self._tr("status.queue", completed=completed, total=total))

    @Slot(bool)
    def _on_queue_result(self, cancelled: bool) -> None:
        self.download_button.setEnabled(
            bool(self._ffmpeg_installation and self._ffmpeg_installation.available)
        )
        self.pause_button.setEnabled(False)
        self.pause_button.setText(self._tr("action.pause_queue"))
        self.queue_model.set_locked_items(set())
        self.analyze_button.setEnabled(True)
        self._set_source_options_enabled(True)
        self.phase_label.setText(
            self._tr("status.queue_cancelled") if cancelled else self._tr("status.queue_finished")
        )
        self._append_log(
            self._tr("log.queue_cancelled") if cancelled else self._tr("log.queue_finished")
        )
        self._update_queue_summary()

    @Slot()
    def _download_thread_done(self) -> None:
        if self._download_worker:
            self._download_worker.deleteLater()
        self._download_worker = None
        if not self._analysis_worker or not self._analysis_worker.isRunning():
            self.clear_all_button.setEnabled(True)

    def _toggle_pause(self) -> None:
        if not self._download_worker:
            return
        paused = not self._download_worker.paused
        self._download_worker.set_paused(paused)
        if paused:
            self.queue_manager.pause()
        else:
            self.queue_manager.resume()
        self.pause_button.setText(
            self._tr("action.resume_queue") if paused else self._tr("action.pause_queue")
        )

    def _cancel_selected(self) -> None:
        rows = self.download_table.selected_rows()
        items = self.queue_model.items_at_rows(rows)
        for item in items:
            if self._download_worker:
                self._download_worker.cancel_item(item.id)
            persisted = self.queue_manager.cancel(item.id)
            if persisted:
                self.queue_model.update_item(persisted)
        self._update_queue_summary()

    def _cancel_all_pending(self) -> None:
        answer = QMessageBox.question(
            self,
            self._tr("dialog.cancel_queue.title"),
            self._tr("dialog.cancel_queue.message"),
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        if self._download_worker:
            self._download_worker.cancel_all()
        self.queue_manager.cancel_all_pending()
        for item in self.queue_model.items:
            if item.status not in {
                DownloadStatus.COMPLETED,
                DownloadStatus.SKIPPED,
                DownloadStatus.FAILED,
            }:
                persisted = self.queue_manager.cancel(item.id)
                if persisted:
                    self.queue_model.update_item(persisted)
        self._update_queue_summary()

    def _retry_failed(self) -> None:
        retried = self.queue_manager.retry_failed()
        for item in retried:
            self.queue_model.update_item(item)
        self._append_log(self._tr("log.retry_ready", count=len(retried)))
        self._update_queue_summary()

    def _remove_completed(self) -> None:
        completed = [
            item for item in self.queue_model.items if item.status is DownloadStatus.COMPLETED
        ]
        if not completed:
            return
        if len(completed) >= 5:
            answer = QMessageBox.question(
                self,
                self._tr("dialog.remove_completed.title"),
                self._tr("dialog.remove_completed.message", count=len(completed)),
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self.queue_manager.remove_completed()
        self.queue_model.remove_completed()
        self._update_queue_summary()

    def _clear_all(self) -> None:
        analysis_active = bool(self._analysis_worker and self._analysis_worker.isRunning())
        downloads_active = bool(self._download_worker and self._download_worker.isRunning())
        if analysis_active or downloads_active:
            QMessageBox.warning(
                self,
                self._tr("dialog.clear_all_active.title"),
                self._tr("dialog.clear_all_active.message"),
            )
            return
        answer = QMessageBox.question(
            self,
            self._tr("dialog.clear_all.title"),
            self._tr("dialog.clear_all.message"),
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            cleared = self.queue_manager.clear_all(self.paths.archive_file)
        except QueueBusyError:
            QMessageBox.warning(
                self,
                self._tr("dialog.clear_all_active.title"),
                self._tr("dialog.clear_all_active.message"),
            )
            return
        except Exception as error:
            LOGGER.exception("Could not clear download state")
            QMessageBox.critical(
                self,
                self._tr("dialog.clear_all_failed.title"),
                self._tr("dialog.clear_all_failed.message", message=str(error)),
            )
            return

        self.queue_model.set_locked_items(set())
        self.queue_model.replace_items([])
        self._last_persist.clear()
        self._pending_thumbnails.clear()
        self.current_progress.setValue(0)
        self.progress_details.clear()
        self.phase_label.setText(self._tr("status.ready"))
        self.analysis_phase_label.setText(self._tr("status.ready_analyze"))
        self.log_panel.clear()
        self._append_log(
            self._tr(
                "log.cleared_all",
                queue=cleared.queue_items,
                history=cleared.history_entries,
                archive=cleared.archive_entries,
            )
        )
        self._update_queue_summary()
        self.tabs.setCurrentIndex(self.analyze_tab_index)
        self.statusBar().showMessage(self._tr("status.cleared_all"), 5000)

    def _mode_changed(self) -> None:
        mode = DownloadMode(str(self.mode_combo.currentData()))
        self.quality_combo.setEnabled(mode is DownloadMode.VIDEO)
        self.window_state_repository.set("analysis.mode", mode.value.encode("ascii"))
        self.statusBar().showMessage(
            self._tr("status.format_next_analysis"),
            5000,
        )

    def _metadata_setting_changed(self) -> None:
        self._save_settings()

    def _destination_changed(self) -> None:
        self._save_settings()
        destination = self.destination.text().strip()
        if not destination or destination == self._applied_destination:
            return
        editable_statuses = {
            DownloadStatus.PENDING,
            DownloadStatus.READY,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        }
        targets = [
            item
            for item in self.queue_model.items
            if item.status in editable_statuses and not self._should_reuse_audio(item)
        ]
        target_ids = {item.id for item in targets}
        reserved_paths = {
            str(Path(item.final_media_path).resolve()).casefold()
            for item in self.queue_model.items
            if item.id not in target_ids and item.final_media_path
        }
        for item in targets:
            proposed = self._proposed_path(item, reserved_paths, destination=destination)
            item.final_media_path = str(proposed)
            reserved_paths.add(str(proposed.resolve()).casefold())
            self.queue_manager.save(item)
            self.queue_model.update_item(item)
        self._applied_destination = destination
        self.statusBar().showMessage(self._tr("status.destination_updated"), 5000)

    def _browse_destination(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, self._tr("dialog.choose_destination"), self.destination.text()
        )
        if selected:
            self.destination.setText(selected)
            self._destination_changed()

    def _show_settings(self) -> None:
        dialog = SettingsDialog(
            self.settings,
            self.ffmpeg_service,
            self,
            translator=self.translator,
        )
        if dialog.exec():
            dialog.apply_to(self.settings)
            appearance = self.settings.appearance or AppearanceSettings()
            self.translator.set_language(appearance.language)
            if self.appearance_controller is not None:
                self.appearance_controller.apply(appearance.theme)
            self._save_settings()
            self._retranslate_ui()
            self._check_ffmpeg()

    def _check_ffmpeg(self) -> None:
        if self._closing:
            return
        downloads = self.settings.downloads or DownloadSettings()
        configured_directory = downloads.ffmpeg_directory or None
        if self._ffmpeg_check_worker and self._ffmpeg_check_worker.isRunning():
            self._ffmpeg_recheck_requested = True
            status_key = (
                "status.preparing_tools"
                if self.runtime_tools_service is not None
                else "status.checking_ffmpeg"
            )
            self.statusBar().showMessage(self._tr(status_key), 0)
            return
        self._ffmpeg_recheck_requested = False
        self._ffmpeg_installation = None
        self._ffmpeg_checked_directory = None
        self.download_button.setEnabled(False)
        self.runtime_tools_retry_button.hide()
        if self.runtime_tools_service is not None:
            self.analyze_button.setEnabled(False)
            self.runtime_tools_progress.setValue(0)
            self.runtime_tools_progress.show()
            runtime_worker = RuntimeToolsWorker(
                self.runtime_tools_service,
                configured_directory,
            )
            runtime_worker.progress_changed.connect(self._on_runtime_tools_progress)
            runtime_worker.result_ready.connect(self._on_runtime_tools_ready)
            runtime_worker.setup_failed.connect(self._on_runtime_tools_failed)
            worker: FFmpegCheckWorker | RuntimeToolsWorker = runtime_worker
            self.statusBar().showMessage(self._tr("status.preparing_tools"), 0)
        else:
            worker = FFmpegCheckWorker(self.ffmpeg_service, configured_directory)
            worker.result_ready.connect(self._on_ffmpeg_checked)
            self.statusBar().showMessage(self._tr("status.checking_ffmpeg"), 0)
        worker.finished.connect(self._ffmpeg_check_done)
        self._ffmpeg_check_worker = worker
        worker.start()

    @Slot(str, int, int)
    def _on_runtime_tools_progress(self, tool: str, downloaded: int, total: int) -> None:
        bounded_total = max(1, total)
        fraction = min(1.0, max(0.0, downloaded / bounded_total))
        self.runtime_tools_progress.setValue(round(fraction * 1000))
        tool_label = "FFmpeg" if tool == "ffmpeg" else "Deno"
        if downloaded >= total > 0:
            message = self._tr("status.installing_tool", tool=tool_label)
        else:
            message = self._tr(
                "status.downloading_tool",
                tool=tool_label,
                percent=round(fraction * 100),
            )
        self.statusBar().showMessage(message, 0)

    @Slot(object)
    def _on_runtime_tools_ready(self, value: object) -> None:
        worker = self._ffmpeg_check_worker
        if not isinstance(value, RuntimeToolsStatus) or not isinstance(worker, RuntimeToolsWorker):
            return
        current_directory = (self.settings.downloads or DownloadSettings()).ffmpeg_directory or None
        if worker.manual_ffmpeg_directory != current_directory:
            self._ffmpeg_recheck_requested = True
            return
        installation = FFmpegInstallation(
            ffmpeg=value.ffmpeg,
            ffprobe=value.ffprobe,
            ffmpeg_version=value.ffmpeg_version,
            ffprobe_version=value.ffprobe_version,
        )
        self._js_runtime_path = str(value.deno) if value.deno else None
        self._apply_ffmpeg_result(installation, current_directory)
        self.runtime_tools_progress.hide()
        analysis_idle = not self._analysis_worker or not self._analysis_worker.isRunning()
        if value.available and analysis_idle:
            self.analyze_button.setEnabled(True)
        if value.available and not value.errors:
            self.runtime_tools_retry_button.hide()
            self.statusBar().showMessage(self._tr("status.tools_ready"), 4000)
            self._append_log(self._tr("log.tools_ready"))
            return
        self.analyze_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.runtime_tools_retry_button.show()
        self.statusBar().showMessage(self._tr("status.tools_partial"), 0)
        for message in value.errors:
            self._append_log(self._tr("log.tools_error", message=message))

    @Slot(str)
    def _on_runtime_tools_failed(self, message: str) -> None:
        self.runtime_tools_progress.hide()
        self.runtime_tools_retry_button.show()
        self.analyze_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.statusBar().showMessage(self._tr("status.tools_partial"), 0)
        self._append_log(self._tr("log.tools_error", message=message))

    @Slot(object)
    def _on_ffmpeg_checked(self, value: object) -> None:
        worker = self._ffmpeg_check_worker
        if not isinstance(value, FFmpegInstallation) or not isinstance(worker, FFmpegCheckWorker):
            return
        current_directory = (self.settings.downloads or DownloadSettings()).ffmpeg_directory or None
        if worker.configured_directory != current_directory:
            self._ffmpeg_recheck_requested = True
            return
        self._apply_ffmpeg_result(value, current_directory)

    def _apply_ffmpeg_result(
        self,
        installation: FFmpegInstallation,
        current_directory: str | None,
    ) -> None:
        self._ffmpeg_installation = installation
        self._ffmpeg_checked_directory = current_directory
        if installation.available:
            download_idle = not self._download_worker or not self._download_worker.isRunning()
            analysis_idle = not self._analysis_worker or not self._analysis_worker.isRunning()
            if download_idle and analysis_idle:
                self.download_button.setEnabled(True)
            self.statusBar().showMessage(self._tr("status.ffmpeg_available"), 4000)
        else:
            self.download_button.setEnabled(False)
            self.statusBar().showMessage(self._tr("status.ffmpeg_unavailable"), 0)

    @Slot()
    def _ffmpeg_check_done(self) -> None:
        worker = self._ffmpeg_check_worker
        recheck = self._ffmpeg_recheck_requested
        if worker is not None:
            worker.deleteLater()
        self._ffmpeg_check_worker = None
        self._ffmpeg_recheck_requested = False
        if recheck and not self._closing:
            self._check_ffmpeg()

    def _open_output_directory(self) -> None:
        path = Path(self.destination.text().strip())
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _open_log_directory(self) -> None:
        self.paths.log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.log_dir.resolve())))

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_panel.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _restore_window_state(self) -> None:
        geometry = self.window_state_repository.get("main.geometry")
        state = self.window_state_repository.get("main.state")
        active_tab = self.window_state_repository.get("main.active_tab")
        url_draft = self.window_state_repository.get("analysis.url_draft")
        mode_value = self.window_state_repository.get("analysis.mode")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        if url_draft:
            self.url_input.setPlainText(url_draft.decode("utf-8", errors="replace"))
        if mode_value:
            mode_index = self.mode_combo.findData(mode_value.decode("ascii", errors="ignore"))
            if mode_index >= 0:
                self.mode_combo.blockSignals(True)
                self.mode_combo.setCurrentIndex(mode_index)
                self.mode_combo.blockSignals(False)
                mode = DownloadMode(str(self.mode_combo.currentData()))
                self.quality_combo.setEnabled(mode is DownloadMode.VIDEO)
        appearance = self.settings.appearance or AppearanceSettings()
        if active_tab and appearance.remember_last_tab:
            try:
                tab_index = int(active_tab.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                tab_index = self.analyze_tab_index
            if 0 <= tab_index < self.tabs.count():
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(tab_index)
                self.tabs.blockSignals(False)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        active = (self._analysis_worker and self._analysis_worker.isRunning()) or (
            self._download_worker and self._download_worker.isRunning()
        )
        if active:
            answer = QMessageBox.question(
                self,
                self._tr("dialog.tasks_active.title"),
                self._tr("dialog.tasks_active.message"),
            )
            if answer is not QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._analysis_worker:
                self._analysis_worker.cancel()
            if self._download_worker:
                self._download_worker.cancel_all()
                self.queue_manager.cancel_all_pending()
                for item in self.queue_model.items:
                    if item.status in {
                        DownloadStatus.PENDING,
                        DownloadStatus.READY,
                        DownloadStatus.DOWNLOADING,
                        DownloadStatus.PROCESSING,
                    }:
                        persisted = self.queue_manager.cancel(item.id)
                        if persisted is not None:
                            self.queue_model.update_item(persisted)
        self._closing = True
        if active:
            workers = [
                worker for worker in (self._analysis_worker, self._download_worker) if worker
            ]
            if any(not worker.wait(5000) for worker in workers):
                QMessageBox.warning(
                    self,
                    self._tr("dialog.worker_stopping.title"),
                    self._tr("dialog.worker_stopping.message"),
                )
                self._closing = False
                event.ignore()
                return
        if self._ffmpeg_check_worker and self._ffmpeg_check_worker.isRunning():
            if isinstance(self._ffmpeg_check_worker, RuntimeToolsWorker):
                self._ffmpeg_check_worker.cancel()
            if not self._ffmpeg_check_worker.wait(11_000):
                QMessageBox.warning(
                    self,
                    self._tr("dialog.ffmpeg_stopping.title"),
                    self._tr("dialog.ffmpeg_stopping.message"),
                )
                self._closing = False
                event.ignore()
                return
        self._save_settings()
        self._persist_workspace_state()
        self.queue_manager.flush_progress()
        geometry = bytes(cast(Any, self.saveGeometry().data()))
        state = bytes(cast(Any, self.saveState().data()))
        self.window_state_repository.set("main.geometry", geometry)
        self.window_state_repository.set("main.state", state)
        super().closeEvent(event)


def _progress_details(item: DownloadItem) -> str:
    parts: list[str] = []
    if item.downloaded_bytes:
        parts.append(_human_bytes(item.downloaded_bytes))
    if item.total_bytes:
        parts.append(f"/ {_human_bytes(item.total_bytes)}")
    if item.speed:
        parts.append(f"· {_human_bytes(item.speed)}/s")
    if item.eta is not None:
        parts.append(f"· ETA {int(item.eta)}s")
    return " ".join(parts)


def _human_bytes(value: float | int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(amount) < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TiB"
