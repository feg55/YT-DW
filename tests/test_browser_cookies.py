from __future__ import annotations

from unittest.mock import patch

import pytest
from yt_dlp.cookies import CookieLoadError

from openmediadl.core.browser_cookies import (
    _browser_from_identifier,
    _platform_default_browser_identifier,
    cookie_specs_for_retry,
    detect_default_browser,
    detected_cookie_spec,
    explicit_cookie_spec,
    is_authentication_error,
    is_cookie_load_error,
    normalize_browser_choice,
)
from openmediadl.domain.settings import CookieBrowser


def test_cookie_specs_distinguish_system_explicit_and_disabled() -> None:
    assert normalize_browser_choice("system") is CookieBrowser.SYSTEM
    assert normalize_browser_choice(None) is CookieBrowser.DISABLED
    assert normalize_browser_choice("removed-browser") is CookieBrowser.DISABLED
    assert explicit_cookie_spec(CookieBrowser.SYSTEM, None) is None
    assert explicit_cookie_spec(CookieBrowser.DISABLED, None) is None
    assert explicit_cookie_spec(CookieBrowser.CHROME, " Profile 2 ") == (
        "chrome",
        "Profile 2",
    )


def test_detected_cookie_spec_maps_windows_default_browser_identifier() -> None:
    with patch(
        "openmediadl.core.browser_cookies._platform_default_browser_identifier",
        return_value="MSEdgeHTM",
    ):
        assert detect_default_browser() is CookieBrowser.EDGE
        assert detected_cookie_spec() == ("edge",)


def test_system_cookie_candidates_try_default_then_existing_profiles() -> None:
    existing = {
        CookieBrowser.CHROME,
        CookieBrowser.FIREFOX,
        CookieBrowser.EDGE,
        CookieBrowser.OPERA,
    }
    with (
        patch(
            "openmediadl.core.browser_cookies.detect_default_browser",
            return_value=CookieBrowser.EDGE,
        ),
        patch(
            "openmediadl.core.browser_cookies.browser_cookie_store_exists",
            side_effect=lambda browser: browser in existing,
        ),
        patch("openmediadl.core.browser_cookies.platform.system", return_value="Windows"),
    ):
        assert cookie_specs_for_retry(CookieBrowser.SYSTEM, None) == (
            ("edge",),
            ("chrome",),
            ("firefox",),
            ("opera",),
        )


def test_explicit_cookie_candidate_keeps_profile_then_falls_back_without_duplicate() -> None:
    existing = {CookieBrowser.CHROME, CookieBrowser.FIREFOX, CookieBrowser.EDGE}
    with (
        patch(
            "openmediadl.core.browser_cookies.browser_cookie_store_exists",
            side_effect=lambda browser: browser in existing,
        ),
        patch("openmediadl.core.browser_cookies.platform.system", return_value="Windows"),
    ):
        assert cookie_specs_for_retry(CookieBrowser.FIREFOX, " Profile 2 ") == (
            ("firefox", "Profile 2"),
            ("chrome",),
            ("edge",),
        )


def test_system_candidates_without_default_follow_popularity_order() -> None:
    existing = {
        CookieBrowser.CHROME,
        CookieBrowser.FIREFOX,
        CookieBrowser.EDGE,
        CookieBrowser.OPERA,
    }
    with (
        patch(
            "openmediadl.core.browser_cookies.detect_default_browser",
            return_value=None,
        ),
        patch(
            "openmediadl.core.browser_cookies.browser_cookie_store_exists",
            side_effect=lambda browser: browser in existing,
        ),
        patch("openmediadl.core.browser_cookies.platform.system", return_value="Windows"),
    ):
        assert cookie_specs_for_retry(CookieBrowser.SYSTEM, None) == (
            ("chrome",),
            ("firefox",),
            ("edge",),
            ("opera",),
        )


def test_disabled_cookie_candidate_list_is_empty() -> None:
    assert cookie_specs_for_retry(CookieBrowser.DISABLED, None) == ()


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("ChromeHTML", CookieBrowser.CHROME),
        ("ChromiumHTM", CookieBrowser.CHROMIUM),
        ("MSEdgeHTM", CookieBrowser.EDGE),
        ("FirefoxURL", CookieBrowser.FIREFOX),
        ("BraveHTML", CookieBrowser.BRAVE),
        ("OperaStable", CookieBrowser.OPERA),
        ("VivaldiHTM", CookieBrowser.VIVALDI),
        ("WhaleHTM", CookieBrowser.WHALE),
    ],
)
def test_supported_default_browser_identifiers(
    identifier: str,
    expected: CookieBrowser,
) -> None:
    assert _browser_from_identifier(identifier) is expected


def test_detection_ignores_browser_environment_and_has_no_fallback() -> None:
    with (
        patch.dict("os.environ", {"BROWSER": "google-chrome"}),
        patch(
            "openmediadl.core.browser_cookies._platform_default_browser_identifier",
            return_value=None,
        ),
    ):
        assert detect_default_browser() is None
        assert detected_cookie_spec() is None


def test_macos_does_not_guess_default_browser_from_launch_services() -> None:
    with (
        patch("openmediadl.core.browser_cookies.platform.system", return_value="Darwin"),
        patch("openmediadl.core.browser_cookies._command_output") as command_output,
    ):
        assert _platform_default_browser_identifier() is None
        command_output.assert_not_called()


def test_authentication_and_cookie_load_error_detection_follows_causes() -> None:
    authentication = RuntimeError("Sign in to confirm your age; use --cookies-from-browser")
    wrapped_authentication = RuntimeError("extractor failed")
    wrapped_authentication.__cause__ = authentication

    cookie_error = CookieLoadError("failed to load cookies")
    wrapped_cookie_error = RuntimeError("request setup failed")
    wrapped_cookie_error.__cause__ = cookie_error

    assert is_authentication_error(wrapped_authentication)
    assert not is_authentication_error(RuntimeError("HTTP Error 403: Forbidden"))
    assert is_cookie_load_error(wrapped_cookie_error)
    assert not is_cookie_load_error(RuntimeError("network timeout"))
