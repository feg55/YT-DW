"""Configured queue table view."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from openmediadl.ui.delegates import ProgressDelegate, TwoLineTextDelegate
from openmediadl.ui.models import Column, DownloadTableModel


class DownloadTable(QTableView):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        queue_model: DownloadTableModel | None = None,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.queue_model = queue_model if queue_model is not None else DownloadTableModel(self)
        self.setModel(self.queue_model)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
            if read_only
            else (
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
                | QAbstractItemView.EditTrigger.SelectedClicked
            )
        )
        self.verticalHeader().setDefaultSectionSize(72)
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(32)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in {
            Column.SELECTED: 38,
            Column.THUMBNAIL: 120,
            Column.ORIGINAL_TITLE: 300,
            Column.CLEANED_TITLE: 300,
            Column.ARTIST: 160,
            Column.ALBUM: 170,
            Column.TRACK: 60,
            Column.DURATION: 75,
            Column.STATUS: 95,
            Column.PROGRESS: 200,
            Column.ERROR: 200,
            Column.FINAL_PATH: 300,
        }.items():
            self.setColumnWidth(int(column), width)
        header.setSectionResizeMode(int(Column.SELECTED), QHeaderView.ResizeMode.Fixed)
        self.setColumnHidden(int(Column.ALBUM), True)
        self.setColumnHidden(int(Column.ERROR), True)
        if read_only:
            self.setColumnHidden(int(Column.SELECTED), True)
            self.setColumnHidden(int(Column.ORIGINAL_TITLE), True)
            header.setSectionResizeMode(int(Column.FINAL_PATH), QHeaderView.ResizeMode.Stretch)
        title_delegate = TwoLineTextDelegate(self)
        self.setItemDelegateForColumn(int(Column.ORIGINAL_TITLE), title_delegate)
        self.setItemDelegateForColumn(int(Column.CLEANED_TITLE), title_delegate)
        self.setItemDelegateForColumn(int(Column.PROGRESS), ProgressDelegate(self))
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSortingEnabled(False)
        self.setCornerButtonEnabled(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def selected_rows(self) -> list[int]:
        return [index.row() for index in self.selectionModel().selectedRows()]
