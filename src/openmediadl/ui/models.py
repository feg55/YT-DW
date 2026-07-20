"""Scalable editable table model for analyzed media."""

from __future__ import annotations

from collections import OrderedDict
from enum import IntEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QPixmap

from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadStatus
from openmediadl.i18n import Translator


class Column(IntEnum):
    SELECTED = 0
    THUMBNAIL = 1
    ORIGINAL_TITLE = 2
    CLEANED_TITLE = 3
    ARTIST = 4
    ALBUM = 5
    TRACK = 6
    DURATION = 7
    STATUS = 8
    PROGRESS = 9
    ERROR = 10
    FINAL_PATH = 11


HEADER_KEYS: tuple[str, ...] = (
    "table.selected",
    "table.cover",
    "table.original_title",
    "table.cleaned_title",
    "table.artist",
    "table.album",
    "table.track",
    "table.duration",
    "table.status",
    "table.progress",
    "table.error",
    "table.final_file",
)


class DownloadTableModel(QAbstractTableModel):
    item_edited = Signal(object, int)
    selection_changed = Signal(object)

    def __init__(self, parent: Any = None, *, translator: Translator | None = None) -> None:
        super().__init__(parent)
        self.translator = translator or Translator()
        self.items: list[DownloadItem] = []
        self._by_id: dict[str, int] = {}
        self._by_source_url: dict[str, int] = {}
        self._locked_ids: set[str] = set()
        self._pixmaps: OrderedDict[str, QPixmap] = OrderedDict()
        self._pixmap_limit = 72

    def rowCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(self.items)

    def columnCount(  # noqa: N802
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(HEADER_KEYS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return (
                    self.translator.tr(HEADER_KEYS[section])
                    if 0 <= section < len(HEADER_KEYS)
                    else None
                )
            if role == Qt.ItemDataRole.ToolTipRole and section == int(Column.SELECTED):
                return self.translator.tr("table.include_download")
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return section + 1
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        item = self.items[index.row()]
        column = Column(index.column())

        if role == Qt.ItemDataRole.CheckStateRole and column is Column.SELECTED:
            return Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole and column is Column.THUMBNAIL:
            return self._thumbnail(item.cached_thumbnail_path)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in {
            Column.SELECTED,
            Column.TRACK,
            Column.DURATION,
            Column.STATUS,
            Column.PROGRESS,
        }:
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            if column is Column.PROGRESS and item.status is DownloadStatus.FAILED:
                return item.technical_error or _error_label(item, self.translator)
            if column is Column.ERROR:
                return item.technical_error or ""
            if column is Column.FINAL_PATH:
                return item.final_media_path or ""
            if column is Column.ORIGINAL_TITLE:
                return item.original_title
        if role == Qt.ItemDataRole.UserRole:
            return item
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return None

        values: dict[Column, Any] = {
            Column.SELECTED: "",
            Column.THUMBNAIL: "",
            Column.ORIGINAL_TITLE: item.original_title,
            Column.CLEANED_TITLE: item.cleaned_title,
            Column.ARTIST: item.artist,
            Column.ALBUM: item.album,
            Column.TRACK: item.track_number or "",
            Column.DURATION: _format_duration(item.duration),
            Column.STATUS: self.translator.tr(f"download_status.{item.status.value}"),
            Column.PROGRESS: (
                _error_label(item, self.translator)
                if item.status is DownloadStatus.FAILED
                else f"{item.progress_percentage:.1f}%"
            ),
            Column.ERROR: getattr(item, "error_message", None)
            or (item.error_category or "").replace("_", " ").title(),
            Column.FINAL_PATH: item.final_media_path or "",
        }
        return values[column]

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        column = Column(index.column())
        item = self.items[index.row()]
        selectable_status = item.status not in {
            DownloadStatus.COMPLETED,
            DownloadStatus.SKIPPED,
        }
        if column is Column.SELECTED and item.id not in self._locked_ids and selectable_status:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        editable_status = item.status in {
            DownloadStatus.PENDING,
            DownloadStatus.READY,
            DownloadStatus.FAILED,
            DownloadStatus.CANCELLED,
        }
        if (
            editable_status
            and item.id not in self._locked_ids
            and column
            in {
                Column.CLEANED_TITLE,
                Column.ARTIST,
                Column.ALBUM,
                Column.TRACK,
            }
        ):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(  # noqa: N802
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return False
        item = self.items[index.row()]
        column = Column(index.column())
        changed = False
        if column is Column.SELECTED and role == Qt.ItemDataRole.CheckStateRole:
            item.selected = value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked
            changed = True
        elif role == Qt.ItemDataRole.EditRole:
            text = str(value).strip()
            if column is Column.CLEANED_TITLE and text:
                item.cleaned_title = text
                item.title_manually_edited = True
                changed = True
            elif column is Column.ARTIST:
                item.artist = text
                item.artist_manually_edited = True
                changed = True
            elif column is Column.ALBUM:
                item.album = text
                item.album_manually_edited = True
                changed = True
            elif column is Column.TRACK:
                if not text:
                    item.track_number = None
                else:
                    try:
                        item.track_number = max(1, int(text))
                    except ValueError:
                        return False
                item.track_manually_edited = True
                changed = True
        if changed:
            item.touch()
            self.dataChanged.emit(index, index, [role, Qt.ItemDataRole.DisplayRole])
            if column is Column.SELECTED:
                self.selection_changed.emit(item)
            else:
                self.item_edited.emit(item, int(column))
        return changed

    def add_items(self, values: list[DownloadItem]) -> None:
        known_ids = set(self._by_id)
        new_values: list[DownloadItem] = []
        for item in values:
            if item.id in known_ids:
                continue
            known_ids.add(item.id)
            new_values.append(item)
        if not new_values:
            return
        first = len(self.items)
        self.beginInsertRows(QModelIndex(), first, first + len(new_values) - 1)
        self.items.extend(new_values)
        self.endInsertRows()
        self._reindex()

    def replace_items(self, values: list[DownloadItem]) -> None:
        self.beginResetModel()
        self.items = list(values)
        self._pixmaps.clear()
        self._reindex()
        self.endResetModel()

    def update_item(self, item: DownloadItem) -> None:
        row = self._by_id.get(item.id)
        if row is None:
            self.add_items([item])
            return
        self.items[row] = item
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(HEADER_KEYS) - 1))

    def selected_items(self) -> list[DownloadItem]:
        return [item for item in self.items if item.selected]

    def item_by_id(self, item_id: str) -> DownloadItem | None:
        row = self._by_id.get(item_id)
        return self.items[row] if row is not None else None

    def item_by_source_url(self, source_url: str) -> DownloadItem | None:
        row = self._by_source_url.get(source_url)
        return self.items[row] if row is not None else None

    def set_locked_items(self, item_ids: set[str]) -> None:
        self._locked_ids = set(item_ids)
        if self.items:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self.items) - 1, len(HEADER_KEYS) - 1),
            )

    def retranslate(self) -> None:
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(HEADER_KEYS) - 1)
        if self.items:
            self.dataChanged.emit(
                self.index(0, int(Column.STATUS)),
                self.index(len(self.items) - 1, int(Column.PROGRESS)),
            )

    def items_at_rows(self, rows: list[int]) -> list[DownloadItem]:
        return [self.items[row] for row in sorted(set(rows)) if 0 <= row < len(self.items)]

    def remove_completed(self) -> list[DownloadItem]:
        removed = [item for item in self.items if item.status is DownloadStatus.COMPLETED]
        if removed:
            self.replace_items(
                [item for item in self.items if item.status is not DownloadStatus.COMPLETED]
            )
        return removed

    def _reindex(self) -> None:
        self._by_id = {item.id: row for row, item in enumerate(self.items)}
        self._by_source_url = {item.source_url: row for row, item in enumerate(self.items)}

    def _thumbnail(self, path_value: str | None) -> QPixmap | None:
        if not path_value or not Path(path_value).is_file():
            return None
        if path_value in self._pixmaps:
            pixmap = self._pixmaps.pop(path_value)
            self._pixmaps[path_value] = pixmap
            return pixmap
        pixmap = QPixmap(path_value)
        if pixmap.isNull():
            return None
        pixmap = pixmap.scaled(
            QSize(112, 64),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._pixmaps[path_value] = pixmap
        while len(self._pixmaps) > self._pixmap_limit:
            self._pixmaps.popitem(last=False)
        return pixmap


def _format_duration(value: float | None) -> str:
    if value is None:
        return ""
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def _error_label(item: DownloadItem, translator: Translator) -> str:
    return (
        item.error_message
        or (item.error_category or "").replace("_", " ").title()
        or translator.tr("download_status.failed")
    )
