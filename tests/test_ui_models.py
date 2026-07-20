from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadStatus
from openmediadl.domain.settings import MetadataSettings
from openmediadl.ui.delegates import two_line_text
from openmediadl.ui.models import Column, DownloadTableModel

_APP = QApplication.instance() or QApplication([])


def test_progress_column_replaces_failed_percentage_with_error() -> None:
    model = DownloadTableModel()
    failed = DownloadItem.new(
        "https://example.test/failed",
        status=DownloadStatus.FAILED,
        progress_percentage=42,
        error_category="network_timeout",
        error_message="The network request timed out.",
        technical_error="socket timeout after 30 seconds",
    )
    model.add_items([failed])
    index = model.index(0, int(Column.PROGRESS))

    assert index.data() == "The network request timed out."
    assert index.data(Qt.ItemDataRole.ToolTipRole) == "socket timeout after 30 seconds"


def test_progress_column_keeps_percentage_for_non_failed_item() -> None:
    model = DownloadTableModel()
    model.add_items(
        [
            DownloadItem.new(
                "https://example.test/ready",
                status=DownloadStatus.READY,
                progress_percentage=12.5,
            )
        ]
    )

    assert model.index(0, int(Column.PROGRESS)).data() == "12.5%"


def test_title_layout_is_limited_to_two_lines() -> None:
    lines = two_line_text(
        "A very long original video title that needs more than two lines in a narrow table cell",
        QFont(),
        120,
    )

    assert len(lines) == 2
    assert lines[-1].endswith("…")


def test_playlist_album_setting_defaults_off_but_explicit_choice_round_trips() -> None:
    defaults = MetadataSettings()
    assert defaults.use_playlist_title_as_album is False

    restored = MetadataSettings.from_dict(
        {**defaults.to_dict(), "use_playlist_title_as_album": True}
    )
    assert restored.use_playlist_title_as_album is True
