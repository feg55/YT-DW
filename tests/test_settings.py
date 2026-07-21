from __future__ import annotations

from openmediadl.domain.settings import (
    AppearanceSettings,
    AppSettings,
    CookieBrowser,
    DownloadSettings,
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

    original = AppearanceSettings.from_dict({"theme": ThemePreference.ORIGINAL.value})
    assert original.theme is ThemePreference.ORIGINAL
    assert original.to_dict()["theme"] == ThemePreference.ORIGINAL.value


def test_new_browser_cookie_settings_default_to_system() -> None:
    assert DownloadSettings().cookie_browser is CookieBrowser.SYSTEM


def test_persisted_missing_null_and_invalid_cookie_browsers_use_system() -> None:
    persisted_values = (
        {},
        {"cookie_browser": None},
        {"cookie_browser": "removed-browser"},
    )

    for values in persisted_values:
        assert DownloadSettings.from_dict(values).cookie_browser is CookieBrowser.SYSTEM

    malformed_document = AppSettings.from_dict({"downloads": None})
    assert malformed_document.downloads is not None
    assert malformed_document.downloads.cookie_browser is CookieBrowser.SYSTEM


def test_disabled_and_explicit_cookie_browser_round_trip() -> None:
    disabled = DownloadSettings(cookie_browser=CookieBrowser.DISABLED)
    chrome = DownloadSettings(cookie_browser=CookieBrowser.CHROME, cookie_profile="Profile 2")

    assert DownloadSettings.from_dict(disabled.to_dict()).cookie_browser is CookieBrowser.DISABLED
    restored = DownloadSettings.from_dict(chrome.to_dict())
    assert restored.cookie_browser is CookieBrowser.CHROME
    assert restored.cookie_profile == "Profile 2"

    # The current UI may transiently assign None. Serialize it as an explicit
    # opt-out rather than recreating the legacy JSON null value.
    assert DownloadSettings(cookie_browser=None).to_dict()["cookie_browser"] == "none"
