from __future__ import annotations

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from openmediadl.appearance import (
    ORIGINAL_STYLESHEET,
    AppearanceController,
    apply_theme,
    dark_palette,
    original_palette,
)
from openmediadl.domain.settings import ThemePreference


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dark_is_default_and_applies_distinct_readable_palette(
    application: QApplication,
) -> None:
    apply_theme(application)
    palette = application.palette()
    role = QPalette.ColorRole

    assert palette.color(role.Window) == dark_palette().color(role.Window)
    assert palette.color(role.Window).lightness() < palette.color(role.WindowText).lightness()
    assert palette.color(role.Base).lightness() < palette.color(role.Text).lightness()


def test_light_restores_current_style_standard_palette(application: QApplication) -> None:
    apply_theme(application, ThemePreference.DARK)
    apply_theme(application, ThemePreference.LIGHT)

    assert application.palette() == application.style().standardPalette()


def test_original_theme_uses_readable_red_and_black_palette(
    application: QApplication,
) -> None:
    apply_theme(application, ThemePreference.ORIGINAL)
    palette = application.palette()
    role = QPalette.ColorRole

    assert palette.color(role.Window) == original_palette().color(role.Window)
    assert palette.color(role.Window).lightness() < palette.color(role.WindowText).lightness()
    assert palette.color(role.Base).lightness() < palette.color(role.Text).lightness()
    assert palette.color(role.AlternateBase).lightness() < palette.color(role.Text).lightness()
    assert palette.color(role.Highlight).red() > palette.color(role.Highlight).blue()
    assert "alternate-background-color: #56000a;" in ORIGINAL_STYLESHEET


class _FakeHints:
    def __init__(self) -> None:
        self.unset_calls = 0

    def unsetColorScheme(self) -> None:  # noqa: N802 - mirrors Qt API
        self.unset_calls += 1


class _FakeStyle:
    def __init__(self, standard_palette: QPalette) -> None:
        self._standard_palette = standard_palette

    def standardPalette(self) -> QPalette:  # noqa: N802 - mirrors Qt API
        return self._standard_palette


class _FakeApplication:
    def __init__(self) -> None:
        self.hints = _FakeHints()
        self.standard_palette = QPalette()
        self.applied_palette: QPalette | None = None

    def styleHints(self) -> _FakeHints:  # noqa: N802 - mirrors Qt API
        return self.hints

    def style(self) -> _FakeStyle:
        return _FakeStyle(self.standard_palette)

    def setPalette(self, palette: QPalette) -> None:  # noqa: N802 - mirrors Qt API
        self.applied_palette = palette


def test_system_theme_releases_override_when_qt_supports_it() -> None:
    fake = _FakeApplication()

    apply_theme(cast(QApplication, fake), ThemePreference.SYSTEM)

    assert fake.hints.unset_calls == 1
    assert fake.applied_palette == fake.standard_palette


def test_controller_switches_dark_light_and_system_at_runtime(
    application: QApplication,
) -> None:
    controller = AppearanceController(application)
    native_stylesheet = application.styleSheet()
    role = QPalette.ColorRole

    controller.apply(ThemePreference.ORIGINAL)
    assert controller.preference is ThemePreference.ORIGINAL
    assert ORIGINAL_STYLESHEET in application.styleSheet()
    assert (
        application.palette().color(role.Highlight).red()
        > application.palette().color(role.Highlight).blue()
    )

    controller.apply(ThemePreference.DARK)
    dark = application.palette()
    assert controller.preference is ThemePreference.DARK
    assert application.styleSheet() == native_stylesheet
    assert dark.color(role.Window).lightness() < dark.color(role.WindowText).lightness()
    assert dark.color(role.Base).lightness() < dark.color(role.Text).lightness()

    controller.apply(ThemePreference.LIGHT)
    assert controller.preference is ThemePreference.LIGHT
    assert application.palette() == application.style().standardPalette()

    controller.apply(ThemePreference.SYSTEM)
    assert controller.preference is ThemePreference.SYSTEM


def test_controller_restores_base_stylesheet_without_original_theme_duplicates(
    application: QApplication,
) -> None:
    previous_stylesheet = application.styleSheet()
    sentinel_stylesheet = "QLabel#theme-sentinel { color: #123456; }"
    application.setStyleSheet(sentinel_stylesheet)
    controller = AppearanceController(application)

    try:
        controller.apply(ThemePreference.ORIGINAL)
        controller.apply(ThemePreference.ORIGINAL)
        assert application.styleSheet().count(ORIGINAL_STYLESHEET) == 1
        assert sentinel_stylesheet in application.styleSheet()

        for preference in (
            ThemePreference.DARK,
            ThemePreference.LIGHT,
            ThemePreference.SYSTEM,
        ):
            controller.apply(ThemePreference.ORIGINAL)
            controller.apply(preference)
            assert application.styleSheet() == sentinel_stylesheet
    finally:
        application.setStyleSheet(previous_stylesheet)
