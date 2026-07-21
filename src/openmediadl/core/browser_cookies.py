"""Lazy browser-cookie selection for yt-dlp without third-party readers."""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Iterator
from pathlib import Path

from yt_dlp.cookies import CookieLoadError

from openmediadl.domain.settings import CookieBrowser

BrowserCookieSpec = tuple[str, ...]

_FALLBACK_ORDER: tuple[CookieBrowser, ...] = (
    CookieBrowser.CHROME,
    CookieBrowser.FIREFOX,
    CookieBrowser.EDGE,
    CookieBrowser.OPERA,
    CookieBrowser.BRAVE,
    CookieBrowser.CHROMIUM,
    CookieBrowser.VIVALDI,
    CookieBrowser.WHALE,
)

_AUTHENTICATION_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("authentication", "required"),
    ("login required",),
    ("sign in",),
    ("cookies", "required"),
    ("cookies-from-browser",),
    ("confirm your age",),
    ("age-restricted",),
    ("age restricted",),
    ("private video",),
    ("members-only",),
    ("members only",),
    ("registered users",),
)


class BrowserCookiesUnavailableError(RuntimeError):
    """Authentication was required, but the selected cookie store was unreadable."""

    def __init__(self, browsers: str | tuple[str, ...] | list[str] | None = None) -> None:
        attempted: tuple[str, ...]
        if isinstance(browsers, str):
            attempted = (browsers,)
        else:
            attempted = tuple(dict.fromkeys(browsers or ()))
        label = ", ".join(attempted) if attempted else "none found"
        super().__init__(f"Browser cookies are unavailable. Tried browser stores: {label}.")
        self.attempted_browsers = attempted
        # Retained for callers that used the former single-browser diagnostic.
        self.browser = attempted[-1] if attempted else None


def normalize_browser_choice(value: CookieBrowser | str | None) -> CookieBrowser:
    """Normalize persisted/UI values while keeping ``None`` as explicit opt-out."""

    if isinstance(value, CookieBrowser):
        return value
    if value is None:
        return CookieBrowser.DISABLED
    try:
        return CookieBrowser(value.casefold().strip())
    except (AttributeError, ValueError):
        return CookieBrowser.DISABLED


def explicit_cookie_spec(
    browser: CookieBrowser | str | None,
    profile: str | None,
) -> BrowserCookieSpec | None:
    """Return a spec only for an explicitly selected concrete browser."""

    choice = normalize_browser_choice(browser)
    if choice in {CookieBrowser.SYSTEM, CookieBrowser.DISABLED}:
        return None
    clean_profile = profile.strip() if profile and profile.strip() else None
    return (choice.value, clean_profile) if clean_profile else (choice.value,)


def detected_cookie_spec() -> BrowserCookieSpec | None:
    """Return a profile-agnostic spec for the supported OS default browser."""

    browser = detect_default_browser()
    return (browser.value,) if browser is not None else None


def cookie_specs_for_retry(
    browser: CookieBrowser | str | None,
    profile: str | None,
) -> tuple[BrowserCookieSpec, ...]:
    """Return safe browser-cookie retries in deterministic order.

    The explicitly selected browser, or the supported OS default, is attempted
    first. Remaining candidates are limited to browsers with a local profile
    directory so an authentication failure does not spawn a series of pointless
    cookie extraction errors.
    """

    choice = normalize_browser_choice(browser)
    if choice is CookieBrowser.DISABLED:
        return ()

    specs: list[BrowserCookieSpec] = []
    seen: set[CookieBrowser] = set()
    if choice is CookieBrowser.SYSTEM:
        default = detect_default_browser()
        if default is not None and _is_platform_appropriate(default):
            specs.append((default.value,))
            seen.add(default)
    else:
        explicit = explicit_cookie_spec(choice, profile)
        if explicit is not None:
            specs.append(explicit)
            seen.add(choice)

    fallback_order = list(_FALLBACK_ORDER)
    if platform.system() == "Darwin":
        fallback_order.append(CookieBrowser.SAFARI)
    for candidate in fallback_order:
        if candidate in seen or not browser_cookie_store_exists(candidate):
            continue
        specs.append((candidate.value,))
        seen.add(candidate)
    return tuple(specs)


def detect_default_browser() -> CookieBrowser | None:
    """Return a supported browser only when it is the OS-configured default."""

    identifier = _platform_default_browser_identifier()
    return _browser_from_identifier(identifier or "")


def browser_cookie_store_exists(browser: CookieBrowser) -> bool:
    """Return whether this user has a profile directory for ``browser``."""

    return any(path.is_dir() for path in _browser_profile_roots(browser))


def _browser_profile_roots(browser: CookieBrowser) -> tuple[Path, ...]:
    user_home = Path.home()
    system = platform.system()
    if system == "Windows":
        local_value = os.environ.get("LOCALAPPDATA")
        roaming_value = os.environ.get("APPDATA")
        local = Path(local_value) if local_value else user_home / "AppData" / "Local"
        roaming = Path(roaming_value) if roaming_value else user_home / "AppData" / "Roaming"
        paths = {
            CookieBrowser.CHROME: (local / "Google/Chrome/User Data",),
            CookieBrowser.FIREFOX: (roaming / "Mozilla/Firefox/Profiles",),
            CookieBrowser.EDGE: (local / "Microsoft/Edge/User Data",),
            CookieBrowser.OPERA: (roaming / "Opera Software/Opera Stable",),
            CookieBrowser.BRAVE: (local / "BraveSoftware/Brave-Browser/User Data",),
            CookieBrowser.CHROMIUM: (local / "Chromium/User Data",),
            CookieBrowser.VIVALDI: (local / "Vivaldi/User Data",),
            CookieBrowser.WHALE: (local / "Naver/Naver Whale/User Data",),
        }
        return paths.get(browser, ())
    if system == "Darwin":
        support = user_home / "Library" / "Application Support"
        paths = {
            CookieBrowser.CHROME: (support / "Google/Chrome",),
            CookieBrowser.FIREFOX: (support / "Firefox/Profiles",),
            CookieBrowser.EDGE: (support / "Microsoft Edge",),
            CookieBrowser.OPERA: (support / "com.operasoftware.Opera",),
            CookieBrowser.BRAVE: (support / "BraveSoftware/Brave-Browser",),
            CookieBrowser.CHROMIUM: (support / "Chromium",),
            CookieBrowser.VIVALDI: (support / "Vivaldi",),
            CookieBrowser.WHALE: (support / "Naver/Whale",),
            CookieBrowser.SAFARI: (user_home / "Library/Cookies",),
        }
        return paths.get(browser, ())
    config = user_home / ".config"
    paths = {
        CookieBrowser.CHROME: (config / "google-chrome",),
        CookieBrowser.FIREFOX: (user_home / ".mozilla/firefox",),
        CookieBrowser.EDGE: (config / "microsoft-edge",),
        CookieBrowser.OPERA: (config / "opera",),
        CookieBrowser.BRAVE: (config / "BraveSoftware/Brave-Browser",),
        CookieBrowser.CHROMIUM: (config / "chromium",),
        CookieBrowser.VIVALDI: (config / "vivaldi",),
        CookieBrowser.WHALE: (config / "naver-whale",),
    }
    return paths.get(browser, ())


def _is_platform_appropriate(browser: CookieBrowser) -> bool:
    return browser is not CookieBrowser.SAFARI or platform.system() == "Darwin"


def is_authentication_error(error: BaseException) -> bool:
    """Recognize extractor messages that can reasonably be solved with cookies."""

    return any(
        all(fragment in text for fragment in required)
        for text in _error_texts(error)
        for required in _AUTHENTICATION_PATTERNS
    )


def is_cookie_load_error(error: BaseException) -> bool:
    """Recognize yt-dlp's cookie extraction wrapper and its common causes."""

    for current in _error_chain(error):
        if isinstance(current, CookieLoadError):
            return True
        text = str(current).casefold()
        if "failed to load cookies" in text or (
            "cookie" in text
            and any(
                fragment in text
                for fragment in (
                    "could not copy",
                    "could not find",
                    "database is locked",
                    "failed to decrypt",
                    "unable to decrypt",
                )
            )
        ):
            return True
    return False


def _error_texts(error: BaseException) -> Iterator[str]:
    for current in _error_chain(error):
        text = str(current).casefold().strip()
        if text:
            yield text


def _error_chain(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            original = exc_info[1]
            if isinstance(original, BaseException):
                pending.append(original)


def _browser_from_identifier(identifier: str) -> CookieBrowser | None:
    value = identifier.casefold()
    matches = (
        (("brave",), CookieBrowser.BRAVE),
        (("chromium",), CookieBrowser.CHROMIUM),
        (("chrome", "google-chrome"), CookieBrowser.CHROME),
        (("firefox", "mozilla"), CookieBrowser.FIREFOX),
        (("microsoft-edge", "msedge", "microsoftedge"), CookieBrowser.EDGE),
        (("opera",), CookieBrowser.OPERA),
        (("safari",), CookieBrowser.SAFARI),
        (("vivaldi",), CookieBrowser.VIVALDI),
        (("whale", "naver"), CookieBrowser.WHALE),
    )
    return next(
        (browser for identifiers, browser in matches if any(item in value for item in identifiers)),
        None,
    )


def _platform_default_browser_identifier() -> str | None:
    system = platform.system()
    if system == "Windows":
        return _windows_default_browser_identifier()
    if system == "Linux":
        return _command_output(
            ["xdg-settings", "get", "default-web-browser"],
        ) or _command_output(["xdg-mime", "query", "default", "x-scheme-handler/https"])
    if system == "Darwin":
        # LSHandlers is a history of associations, not an authoritative current
        # HTTPS default. Do not guess a browser from that privacy-sensitive list.
        return None
    return None


def _windows_default_browser_identifier() -> str | None:
    try:
        import winreg

        path = (
            r"Software\Microsoft\Windows\Shell\Associations"
            r"\UrlAssociations\https\UserChoice"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _kind = winreg.QueryValueEx(key, "ProgId")
        return str(value)
    except (ImportError, OSError):
        return None


def _command_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None
