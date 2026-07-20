from __future__ import annotations

import pytest

from openmediadl.domain.settings import LanguagePreference
from openmediadl.i18n import TranslationCatalog, resolve_language


@pytest.mark.parametrize("locale_name", ["ru", "ru_RU", "ru-RU", "RU_ru.UTF-8"])
def test_system_russian_locale_resolves_to_russian(locale_name: str) -> None:
    assert (
        resolve_language(
            LanguagePreference.SYSTEM,
            locale_name=locale_name,
        )
        is LanguagePreference.RUSSIAN
    )


@pytest.mark.parametrize("locale_name", ["en_US", "de-DE", "uk_UA", "C", ""])
def test_non_russian_system_locale_resolves_to_english(locale_name: str) -> None:
    assert (
        resolve_language(
            LanguagePreference.SYSTEM,
            locale_name=locale_name,
        )
        is LanguagePreference.ENGLISH
    )


def test_explicit_language_does_not_depend_on_system_locale() -> None:
    assert (
        resolve_language(
            LanguagePreference.RUSSIAN,
            locale_name="en_US",
        )
        is LanguagePreference.RUSSIAN
    )
    assert (
        resolve_language(
            LanguagePreference.ENGLISH,
            locale_name="ru_RU",
        )
        is LanguagePreference.ENGLISH
    )


def test_catalog_switches_language_at_runtime() -> None:
    catalog = TranslationCatalog(LanguagePreference.ENGLISH)
    assert catalog.tr("action.analyze") == "Analyze"

    assert catalog.set_language(LanguagePreference.RUSSIAN)
    assert catalog.tr("action.analyze") == "Анализировать"
    assert catalog.language is LanguagePreference.RUSSIAN


def test_catalog_formats_values_and_falls_back_to_key() -> None:
    catalog = TranslationCatalog(LanguagePreference.RUSSIAN)

    assert catalog.tr("status.items_found", count=12) == "Найдено элементов: 12"
    assert catalog.tr("missing.translation") == "missing.translation"


def test_setting_same_resolved_language_reports_no_change() -> None:
    catalog = TranslationCatalog(LanguagePreference.SYSTEM, locale_name="ru_RU")

    assert not catalog.set_language(LanguagePreference.RUSSIAN)
    assert catalog.preference is LanguagePreference.RUSSIAN
