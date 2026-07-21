"""Appearance, download, network, authentication, and FFmpeg settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from openmediadl.core.ffmpeg_service import FFmpegService
from openmediadl.domain.settings import (
    AppearanceSettings,
    AppSettings,
    CookieBrowser,
    DownloadSettings,
    LanguagePreference,
    ThemePreference,
)
from openmediadl.i18n import Translator


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: AppSettings,
        ffmpeg_service: FFmpegService,
        parent: QWidget | None = None,
        *,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        del ffmpeg_service
        appearance = settings.appearance or AppearanceSettings()
        downloads = settings.downloads or DownloadSettings()
        self.translator = translator or Translator(appearance.language)
        self._tr = self.translator.tr
        self.setWindowTitle(self._tr("settings.title"))
        self.setMinimumSize(660, 520)

        self.theme = QComboBox()
        for theme_preference, key in (
            (ThemePreference.DARK, "theme.dark"),
            (ThemePreference.ORIGINAL, "theme.original"),
            (ThemePreference.LIGHT, "theme.light"),
            (ThemePreference.SYSTEM, "theme.system"),
        ):
            self.theme.addItem(self._tr(key), theme_preference.value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(appearance.theme.value)))

        self.language = QComboBox()
        system_language = QLocale.system().nativeLanguageName().strip()
        system_label = self._tr("language.system")
        if system_language:
            system_label = f"{system_label} ({system_language})"
        for language_preference, label in (
            (LanguagePreference.SYSTEM, system_label),
            (LanguagePreference.RUSSIAN, self._tr("language.ru")),
            (LanguagePreference.ENGLISH, self._tr("language.en")),
        ):
            self.language.addItem(label, language_preference.value)
        self.language.setCurrentIndex(max(0, self.language.findData(appearance.language.value)))

        self.remember_last_tab = QCheckBox(self._tr("settings.remember_last_tab"))
        self.remember_last_tab.setChecked(appearance.remember_last_tab)
        interface_note = QLabel(self._tr("settings.appearance_note"))
        interface_note.setWordWrap(True)
        interface_form = QFormLayout()
        interface_form.addRow(self._tr("label.theme"), self.theme)
        interface_form.addRow(self._tr("label.language"), self.language)
        interface_form.addRow("", self.remember_last_tab)
        interface_page = QWidget()
        interface_layout = QVBoxLayout(interface_page)
        interface_layout.addLayout(interface_form)
        interface_layout.addWidget(interface_note)
        interface_layout.addStretch(1)

        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 3)
        self.concurrent.setValue(downloads.maximum_concurrent_downloads)
        self.retries = QSpinBox()
        self.retries.setRange(0, 50)
        self.retries.setValue(downloads.retry_count)
        self.fragment_retries = QSpinBox()
        self.fragment_retries.setRange(0, 50)
        self.fragment_retries.setValue(downloads.fragment_retry_count)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 600)
        self.delay.setDecimals(1)
        self.delay.setSuffix(self._tr("unit.seconds"))
        self.delay.setValue(downloads.delay_between_items)
        self.continue_parts = QCheckBox(self._tr("settings.continue_parts"))
        self.continue_parts.setChecked(downloads.continue_partial_downloads)
        self.archive = QCheckBox(self._tr("settings.skip_archive"))
        self.archive.setChecked(downloads.skip_download_archive)
        downloads_form = QFormLayout()
        downloads_form.addRow(self._tr("settings.parallel_downloads"), self.concurrent)
        downloads_form.addRow(self._tr("settings.retries"), self.retries)
        downloads_form.addRow(self._tr("settings.fragment_retries"), self.fragment_retries)
        downloads_form.addRow(self._tr("settings.delay"), self.delay)
        downloads_form.addRow("", self.continue_parts)
        downloads_form.addRow("", self.archive)
        downloads_page = QWidget()
        downloads_layout = QVBoxLayout(downloads_page)
        downloads_layout.addLayout(downloads_form)
        downloads_layout.addStretch(1)

        self.browser = QComboBox()
        self.browser.addItem(
            self._tr("settings.cookies_system"),
            CookieBrowser.SYSTEM.value,
        )
        self.browser.addItem(
            self._tr("settings.cookies_disabled"),
            CookieBrowser.DISABLED.value,
        )
        for browser in CookieBrowser:
            if browser in {CookieBrowser.SYSTEM, CookieBrowser.DISABLED}:
                continue
            self.browser.addItem(browser.value.title(), browser.value)
        selected_browser = downloads.cookie_browser or CookieBrowser.SYSTEM
        index = self.browser.findData(selected_browser.value)
        self.browser.setCurrentIndex(max(0, index))
        self.profile = QLineEdit(downloads.cookie_profile or "")
        self.profile.setPlaceholderText(self._tr("settings.browser_profile_placeholder"))
        self.browser.currentIndexChanged.connect(self._update_cookie_profile_state)
        self._update_cookie_profile_state()
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(1, 600)
        self.timeout.setSuffix(self._tr("unit.seconds"))
        self.timeout.setValue(downloads.socket_timeout)
        self.bandwidth = QSpinBox()
        self.bandwidth.setRange(0, 10_000_000)
        self.bandwidth.setSuffix(self._tr("unit.kib_unlimited"))
        self.bandwidth.setValue((downloads.bandwidth_limit or 0) // 1024)
        self.ffmpeg_directory = QLineEdit(downloads.ffmpeg_directory or "")
        self.ffmpeg_browse = QPushButton(self._tr("action.browse"))
        self.ffmpeg_browse.clicked.connect(self._browse_ffmpeg)
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.setContentsMargins(0, 0, 0, 0)
        ffmpeg_row.addWidget(self.ffmpeg_directory, 1)
        ffmpeg_row.addWidget(self.ffmpeg_browse)
        ffmpeg_widget = QWidget()
        ffmpeg_widget.setLayout(ffmpeg_row)
        self.ffmpeg_status = QLabel(self._tr("settings.ffmpeg_check_after_save"))
        self.ffmpeg_status.setWordWrap(True)
        network_form = QFormLayout()
        network_form.addRow(self._tr("settings.cookies_browser"), self.browser)
        network_form.addRow(self._tr("settings.browser_profile"), self.profile)
        network_form.addRow(self._tr("settings.socket_timeout"), self.timeout)
        network_form.addRow(self._tr("settings.bandwidth"), self.bandwidth)
        network_form.addRow(self._tr("settings.ffmpeg_directory"), ffmpeg_widget)
        network_form.addRow(self._tr("settings.ffmpeg_status"), self.ffmpeg_status)
        privacy_notice = QLabel(self._tr("settings.cookies_notice"))
        privacy_notice.setWordWrap(True)
        privacy_notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        network_page = QWidget()
        network_layout = QVBoxLayout(network_page)
        network_layout.addLayout(network_form)
        network_layout.addWidget(privacy_notice)
        network_layout.addStretch(1)

        self.tabs = QTabWidget()
        self.tabs.addTab(interface_page, self._tr("settings.tab.interface"))
        self.tabs.addTab(downloads_page, self._tr("settings.tab.downloads"))
        self.tabs.addTab(network_page, self._tr("settings.tab.network_tools"))

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText(self._tr("action.save"))
        if cancel_button is not None:
            cancel_button.setText(self._tr("action.cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.buttons)

    def apply_to(self, settings: AppSettings) -> None:
        appearance = settings.appearance or AppearanceSettings()
        appearance.theme = ThemePreference(str(self.theme.currentData()))
        appearance.language = LanguagePreference(str(self.language.currentData()))
        appearance.remember_last_tab = self.remember_last_tab.isChecked()
        settings.appearance = appearance

        downloads = settings.downloads or DownloadSettings()
        browser = CookieBrowser(str(self.browser.currentData()))
        downloads.cookie_browser = browser
        downloads.cookie_profile = (
            self.profile.text().strip() or None
            if self._is_explicit_cookie_browser(browser)
            else None
        )
        downloads.maximum_concurrent_downloads = self.concurrent.value()
        downloads.retry_count = self.retries.value()
        downloads.fragment_retry_count = self.fragment_retries.value()
        downloads.delay_between_items = self.delay.value()
        downloads.socket_timeout = self.timeout.value()
        downloads.bandwidth_limit = self.bandwidth.value() * 1024 or None
        downloads.continue_partial_downloads = self.continue_parts.isChecked()
        downloads.skip_download_archive = self.archive.isChecked()
        downloads.ffmpeg_directory = self.ffmpeg_directory.text().strip() or None
        settings.downloads = downloads

    def _update_cookie_profile_state(self, _index: int = -1) -> None:
        try:
            browser = CookieBrowser(str(self.browser.currentData()))
        except ValueError:
            browser = CookieBrowser.SYSTEM
        self.profile.setEnabled(self._is_explicit_cookie_browser(browser))

    @staticmethod
    def _is_explicit_cookie_browser(browser: CookieBrowser) -> bool:
        return browser not in {CookieBrowser.SYSTEM, CookieBrowser.DISABLED}

    def _browse_ffmpeg(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, self._tr("settings.select_ffmpeg_directory")
        )
        if selected:
            self.ffmpeg_directory.setText(str(Path(selected)))
            self.ffmpeg_status.setText(self._tr("settings.ffmpeg_selected_check_after_save"))
