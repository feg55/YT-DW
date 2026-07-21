"""Small table delegates."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
)

from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadStatus


def two_line_text(text: str, font: QFont, width: int) -> tuple[str, ...]:
    """Wrap text into at most two lines and elide any remaining content."""

    normalized = " ".join(text.split())
    if not normalized or width <= 0:
        return ()
    layout = QTextLayout(normalized, font)
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(text_option)
    lines: list[tuple[int, int]] = []
    layout.beginLayout()
    for _ in range(2):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(float(width))
        lines.append((line.textStart(), line.textLength()))
    layout.endLayout()
    if not lines:
        return ()

    result = [normalized[start : start + length].strip() for start, length in lines]
    final_start, final_length = lines[-1]
    if final_start + final_length < len(normalized):
        remaining = normalized[final_start:].strip()
        result[-1] = QFontMetrics(font).elidedText(
            remaining,
            Qt.TextElideMode.ElideRight,
            width,
        )
    return tuple(result)


class TwoLineTextDelegate(QStyledItemDelegate):
    """Paint compact title cells with a strict two-line limit."""

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        display_option = QStyleOptionViewItem(option)
        self.initStyleOption(display_option, index)
        text = display_option.text
        display_option.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            display_option,
            painter,
            option.widget,
        )
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            display_option,
            option.widget,
        ).adjusted(4, 2, -4, -2)
        self._paint_text(painter, display_option, text_rect, text)

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), option.fontMetrics.lineSpacing() * 2 + 10))

    @staticmethod
    def _paint_text(
        painter: Any,
        option: QStyleOptionViewItem,
        rect: QRect,
        text: str,
    ) -> None:
        lines = two_line_text(text, option.font, rect.width())
        if not lines:
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        group = QPalette.ColorGroup.Normal if enabled else QPalette.ColorGroup.Disabled
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        line_height = option.fontMetrics.lineSpacing()
        top = rect.top() + max(0, (rect.height() - line_height * len(lines)) // 2)
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(option.palette.color(group, role))
        for offset, line in enumerate(lines):
            line_rect = QRect(
                rect.left(),
                top + offset * line_height,
                rect.width(),
                line_height,
            )
            painter.drawText(
                line_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                line,
            )
        painter.restore()


class ProgressDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        item = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(item, DownloadItem) and item.status is DownloadStatus.FAILED:
            error_option = QStyleOptionViewItem(option)
            self.initStyleOption(error_option, index)
            error_option.text = ""
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(
                QStyle.ControlElement.CE_ItemViewItem,
                error_option,
                painter,
                option.widget,
            )
            text_rect = style.subElementRect(
                QStyle.SubElement.SE_ItemViewItemText,
                error_option,
                option.widget,
            ).adjusted(4, 2, -4, -2)
            message = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            message = f"⚠ {message}"
            TwoLineTextDelegate._paint_text(painter, error_option, text_rect, message)
            return
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "0").rstrip("%")
        try:
            value = int(float(text))
        except ValueError:
            value = 0
        progress = QStyleOptionProgressBar()
        progress.rect = option.rect.adjusted(3, 6, -3, -6)
        progress.minimum = 0
        progress.maximum = 100
        progress.progress = min(100, max(0, value))
        progress.text = f"{value}%"
        progress.textVisible = True
        progress.textAlignment = Qt.AlignmentFlag.AlignCenter
        progress.palette = option.palette
        progress.direction = option.direction
        progress.state = option.state
        progress_style = option.widget.style() if option.widget else QApplication.style()
        progress_style.drawControl(
            QStyle.ControlElement.CE_ProgressBar, progress, painter, option.widget
        )
