from __future__ import annotations

from openmediadl.domain.settings import (
    AppearanceSettings,
    AppSettings,
    LanguagePreference,
    ThemePreference,
)


def test_appearance_defaults_to_dark_theme_and_system_language() -> None:
    appearance = AppearanceSettings()

    assert appearance.theme is ThemePreference.DARK
    assert appearance.language is LanguagePreference.SYSTEM
    assert appearance.remember_last_tab is True


def test_appearance_round_trips_and_unknown_values_fall_back_safely() -> None:
    settings = AppSettings.from_dict(
        {
            "appearance": {
                "theme": ThemePreference.LIGHT.value,
                "language": LanguagePreference.RUSSIAN.value,
                "remember_last_tab": False,
            }
        }
    )

    assert settings.appearance is not None
    assert settings.appearance.theme is ThemePreference.LIGHT
    assert settings.appearance.language is LanguagePreference.RUSSIAN
    assert settings.appearance.remember_last_tab is False
    assert AppSettings.from_dict(settings.to_dict()).to_dict() == settings.to_dict()

    fallback = AppearanceSettings.from_dict(
        {"theme": "removed-theme", "language": "unsupported-locale"}
    )
    assert fallback.theme is ThemePreference.DARK
    assert fallback.language is LanguagePreference.SYSTEM
