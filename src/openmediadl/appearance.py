"""Runtime application palette selection without widget-level coupling."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from openmediadl.domain.settings import ThemePreference

ORIGINAL_STYLESHEET = """
QPushButton:default, QPushButton:focus {
    border: 1px solid #e51b46;
}
QPushButton:hover {
    border-color: #ff496d;
}
QPushButton:pressed {
    background-color: #5b0010;
}
QTabBar::tab:selected {
    color: #ffffff;
    border-bottom: 2px solid #e51b46;
}
QTableView {
    background-color: #101114;
    alternate-background-color: #56000a;
    gridline-color: #34363c;
    selection-background-color: #d71945;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #28292d;
    color: #f2f2f4;
    border: 0;
    border-right: 1px solid #3c3e43;
    border-bottom: 1px solid #3c3e43;
    padding: 4px;
}
QProgressBar {
    background-color: #101114;
    border: 1px solid #3c3e43;
    color: #f2f2f4;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #d71942;
}
""".strip()


def dark_palette() -> QPalette:
    """Build the application dark palette used by the default theme."""

    palette = QPalette()
    role = QPalette.ColorRole
    disabled = QPalette.ColorGroup.Disabled

    palette.setColor(role.Window, QColor(30, 31, 34))
    palette.setColor(role.WindowText, QColor(232, 234, 237))
    palette.setColor(role.Base, QColor(22, 23, 26))
    palette.setColor(role.AlternateBase, QColor(38, 40, 44))
    palette.setColor(role.ToolTipBase, QColor(45, 47, 52))
    palette.setColor(role.ToolTipText, QColor(245, 246, 247))
    palette.setColor(role.Text, QColor(232, 234, 237))
    palette.setColor(role.Button, QColor(45, 47, 52))
    palette.setColor(role.ButtonText, QColor(232, 234, 237))
    palette.setColor(role.BrightText, QColor(255, 99, 99))
    palette.setColor(role.Link, QColor(100, 181, 246))
    palette.setColor(role.Highlight, QColor(44, 121, 191))
    palette.setColor(role.HighlightedText, QColor(255, 255, 255))
    palette.setColor(role.PlaceholderText, QColor(155, 158, 164))

    palette.setColor(disabled, role.WindowText, QColor(128, 131, 137))
    palette.setColor(disabled, role.Text, QColor(128, 131, 137))
    palette.setColor(disabled, role.ButtonText, QColor(128, 131, 137))
    palette.setColor(disabled, role.Highlight, QColor(68, 72, 78))
    palette.setColor(disabled, role.HighlightedText, QColor(170, 173, 179))
    return palette


def original_palette() -> QPalette:
    """Build the optional red-and-black palette inspired by the original UI."""

    palette = QPalette()
    role = QPalette.ColorRole
    disabled = QPalette.ColorGroup.Disabled

    palette.setColor(role.Window, QColor(23, 24, 27))
    palette.setColor(role.WindowText, QColor(244, 244, 245))
    palette.setColor(role.Base, QColor(16, 17, 20))
    palette.setColor(role.AlternateBase, QColor(36, 38, 43))
    palette.setColor(role.ToolTipBase, QColor(36, 38, 43))
    palette.setColor(role.ToolTipText, QColor(250, 250, 251))
    palette.setColor(role.Text, QColor(244, 244, 245))
    palette.setColor(role.Button, QColor(40, 42, 47))
    palette.setColor(role.ButtonText, QColor(244, 244, 245))
    palette.setColor(role.BrightText, QColor(255, 73, 105))
    palette.setColor(role.Link, QColor(255, 90, 118))
    palette.setColor(role.Highlight, QColor(215, 25, 69))
    palette.setColor(role.HighlightedText, QColor(255, 255, 255))
    palette.setColor(role.PlaceholderText, QColor(154, 157, 164))

    palette.setColor(disabled, role.WindowText, QColor(136, 139, 146))
    palette.setColor(disabled, role.Text, QColor(136, 139, 146))
    palette.setColor(disabled, role.ButtonText, QColor(136, 139, 146))
    palette.setColor(disabled, role.Highlight, QColor(90, 36, 48))
    palette.setColor(disabled, role.HighlightedText, QColor(184, 186, 192))
    return palette


def _call_color_scheme_method(method: Callable[..., Any], *args: object) -> bool:
    try:
        method(*args)
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def _request_color_scheme(
    application: QApplication,
    scheme: Qt.ColorScheme | None,
) -> None:
    """Request a native color scheme when supported by the active Qt build."""

    try:
        style_hints = application.styleHints()
    except (AttributeError, RuntimeError):
        return

    if scheme is None:
        unsetter = getattr(style_hints, "unsetColorScheme", None)
        if callable(unsetter) and _call_color_scheme_method(unsetter):
            return
        scheme = Qt.ColorScheme.Unknown

    setter = getattr(style_hints, "setColorScheme", None)
    if callable(setter):
        _call_color_scheme_method(setter, scheme)


def apply_theme(
    application: QApplication,
    preference: ThemePreference = ThemePreference.DARK,
) -> None:
    """Apply a theme immediately to a running ``QApplication``.

    Dark and Original modes use deterministic custom palettes. Light mode asks
    Qt for the native light scheme and applies the current style's standard
    palette. System mode releases any explicit scheme request before restoring
    that standard palette.
    """

    if preference is ThemePreference.DARK:
        _request_color_scheme(application, Qt.ColorScheme.Dark)
        application.setPalette(dark_palette())
        return

    if preference is ThemePreference.ORIGINAL:
        _request_color_scheme(application, Qt.ColorScheme.Dark)
        application.setPalette(original_palette())
        return

    if preference is ThemePreference.LIGHT:
        _request_color_scheme(application, Qt.ColorScheme.Light)
        application.setPalette(application.style().standardPalette())
        return

    if preference is ThemePreference.SYSTEM:
        _request_color_scheme(application, None)
        application.setPalette(application.style().standardPalette())
        return

    raise ValueError(f"Unsupported theme preference: {preference!r}")


apply_application_theme = apply_theme


class AppearanceController:
    """Apply themes while retaining the platform style needed by System mode."""

    def __init__(self, application: QApplication) -> None:
        self.application = application
        self._native_style_name = application.style().objectName()
        self._native_stylesheet = application.styleSheet()
        self._preference = ThemePreference.SYSTEM
        try:
            application.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        except (AttributeError, RuntimeError):
            pass

    @property
    def preference(self) -> ThemePreference:
        return self._preference

    def apply(self, preference: ThemePreference = ThemePreference.DARK) -> None:
        self._preference = preference
        self.application.setStyleSheet(self._native_stylesheet)
        if preference is ThemePreference.SYSTEM:
            if self._native_style_name:
                self.application.setStyle(self._native_style_name)
            _request_color_scheme(self.application, None)
            self.application.setPalette(QPalette())
            return

        self.application.setStyle("Fusion")
        apply_theme(self.application, preference)
        if preference is ThemePreference.ORIGINAL:
            self.application.setStyleSheet(
                "\n\n".join(
                    part for part in (self._native_stylesheet.strip(), ORIGINAL_STYLESHEET) if part
                )
            )

    def _system_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._preference is ThemePreference.SYSTEM:
            self.application.setPalette(QPalette())
