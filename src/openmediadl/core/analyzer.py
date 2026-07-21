"""Incremental metadata extraction through yt-dlp's Python API."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlparse

import yt_dlp

from openmediadl.core.browser_cookies import (
    BrowserCookiesUnavailableError,
    cookie_specs_for_retry,
    is_authentication_error,
    is_cookie_load_error,
    normalize_browser_choice,
)
from openmediadl.core.metadata_cleaner import clean_track_title, normalize_text
from openmediadl.domain.settings import CookieBrowser

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    remove_channel: bool = True
    remove_labels: bool = True
    use_channel_artist: bool = True
    use_playlist_album: bool = True
    use_playlist_track: bool = True
    cookies_browser: str | None = CookieBrowser.SYSTEM.value
    cookies_profile: str | None = None
    socket_timeout: float = 30.0
    retries: int = 5
    js_runtime_path: str | None = None


@dataclass(slots=True)
class AnalyzedEntry:
    source_url: str
    video_id: str
    playlist_id: str = ""
    playlist_title: str = ""
    playlist_index: int | None = None
    playlist_count: int | None = None
    original_title: str = "Untitled media"
    cleaned_title: str = "Untitled media"
    channel: str = ""
    uploader: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    upload_date: str = ""
    duration: float | None = None
    thumbnail_url: str = ""
    webpage_url: str = ""
    extractor: str = ""


class _YtdlpLogger:
    """Route yt-dlp diagnostics into the application's rotating log."""

    def __init__(self) -> None:
        self.last_error = ""

    def debug(self, message: str) -> None:
        if message.startswith("[debug] "):
            LOGGER.debug("yt-dlp: %s", message.removeprefix("[debug] "))
        else:
            LOGGER.info("yt-dlp: %s", message)

    def info(self, message: str) -> None:
        LOGGER.info("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        LOGGER.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        self.last_error = message
        LOGGER.error("yt-dlp: %s", message)


class Analyzer:
    """Analyze URLs lazily; no playlist item cap is configured."""

    def __init__(
        self,
        options: AnalysisOptions,
        cancel_event: threading.Event | None = None,
        entry_callback: Callable[[AnalyzedEntry], None] | None = None,
    ) -> None:
        self.options = options
        self.cancel_event = cancel_event or threading.Event()
        self.entry_callback = entry_callback
        self._seen_callback_entries: set[tuple[str, str, int | None]] = set()
        self._callback_count = 0
        self._match_count = 0

    def analyze(self, urls: Iterable[str]) -> Iterator[AnalyzedEntry]:
        for url in urls:
            if self.cancel_event.is_set():
                return
            yield from self._analyze_url(url)

    def _analyze_url(self, url: str) -> Iterator[AnalyzedEntry]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "extract_flat": "in_playlist",
            "lazy_playlist": True,
            "ignoreerrors": True,
            "skip_download": True,
            "socket_timeout": self.options.socket_timeout,
            "retries": self.options.retries,
            "noplaylist": False,
        }
        if self.options.js_runtime_path:
            options["js_runtimes"] = {
                "deno": {"path": self.options.js_runtime_path},
            }
        if self.entry_callback is not None:
            options["match_filter"] = self._capture_entry
        browser = normalize_browser_choice(self.options.cookies_browser)
        try:
            yield from self._analyze_once(url, options)
            return
        except Exception as error:
            if not is_authentication_error(error) or browser is CookieBrowser.DISABLED:
                raise
            authentication_error = error

        if self.cancel_event.is_set():
            return
        attempts: list[str] = []
        last_error: BaseException = authentication_error
        for cookies in cookie_specs_for_retry(browser, self.options.cookies_profile):
            if self.cancel_event.is_set():
                return
            attempts.append(cookies[0])
            LOGGER.info(
                "Authentication is required; retrying analysis with cookies from %s",
                cookies[0],
            )
            authenticated_options = dict(options)
            authenticated_options["cookiesfrombrowser"] = cookies
            try:
                yield from self._analyze_once(url, authenticated_options)
                return
            except Exception as cookie_error:
                if not (
                    is_cookie_load_error(cookie_error) or is_authentication_error(cookie_error)
                ):
                    raise
                last_error = cookie_error
                LOGGER.warning(
                    "Browser cookie attempt with %s did not authenticate: %s",
                    cookies[0],
                    cookie_error,
                )
        raise BrowserCookiesUnavailableError(attempts) from last_error

    def _analyze_once(self, url: str, options: dict[str, Any]) -> Iterator[AnalyzedEntry]:
        logger = _YtdlpLogger()
        attempt_options = dict(options)
        attempt_options["logger"] = logger
        match_count = self._match_count

        with yt_dlp.YoutubeDL(attempt_options) as ydl:
            result = ydl.extract_info(url, download=False)
        if not result:
            if self._match_count > match_count and logger.last_error:
                if is_authentication_error(ValueError(logger.last_error)):
                    raise ValueError(logger.last_error)
                LOGGER.warning("Some playlist entries were skipped: %s", logger.last_error)
                return
            if self._match_count > match_count:
                return
            raise ValueError(logger.last_error or f"No media metadata was returned for {url}")
        if self.entry_callback is not None and self._match_count > match_count:
            if logger.last_error:
                if is_authentication_error(ValueError(logger.last_error)):
                    raise ValueError(logger.last_error)
                LOGGER.warning("Some playlist entries were skipped: %s", logger.last_error)
            return
        if _has_entries(result):
            playlist = result
            entries = playlist.get("entries") or ()
            for ordinal, raw_entry in enumerate(entries, start=1):
                if self.cancel_event.is_set():
                    return
                if not isinstance(raw_entry, Mapping):
                    LOGGER.warning("Skipping unavailable playlist entry %s from %s", ordinal, url)
                    continue
                yield self._to_entry(raw_entry, playlist, ordinal)
        else:
            yield self._to_entry(result, None, 1)

    def _capture_entry(self, raw: Mapping[str, Any], *, incomplete: bool = False) -> None:
        """Emit metadata as yt-dlp encounters each entry during extraction."""

        del incomplete
        if self.cancel_event.is_set():
            from yt_dlp.utils import DownloadCancelled

            raise DownloadCancelled()
        video_id = str(raw.get("id") or "")
        title = str(raw.get("title") or raw.get("fulltitle") or "")
        if not video_id or not title:
            return None
        self._match_count += 1
        extractor = str(raw.get("extractor_key") or raw.get("ie_key") or raw.get("extractor") or "")
        playlist_index = _optional_int(raw.get("playlist_index"))
        key = (extractor.casefold(), video_id, playlist_index)
        if key in self._seen_callback_entries:
            return None
        self._seen_callback_entries.add(key)
        self._callback_count += 1
        ordinal = playlist_index or self._callback_count
        assert self.entry_callback is not None
        self.entry_callback(self._to_entry(raw, None, ordinal))
        return None

    def _to_entry(
        self,
        raw: Mapping[str, Any],
        playlist: Mapping[str, Any] | None,
        ordinal: int,
    ) -> AnalyzedEntry:
        original_title = normalize_text(
            str(raw.get("title") or raw.get("fulltitle") or "Untitled media")
        )
        channel = normalize_text(
            str(raw.get("channel") or raw.get("uploader") or raw.get("creator") or "")
        )
        uploader = normalize_text(str(raw.get("uploader") or ""))
        cleaned = (
            clean_track_title(original_title, channel, remove_labels=self.options.remove_labels)
            if self.options.remove_channel
            else clean_track_title(original_title, "", remove_labels=self.options.remove_labels)
        )
        playlist_title = normalize_text(
            str(
                raw.get("playlist_title")
                or raw.get("playlist")
                or (playlist or {}).get("title")
                or ""
            )
        )
        playlist_id = str(raw.get("playlist_id") or (playlist or {}).get("id") or "")
        playlist_index = _optional_int(raw.get("playlist_index")) or (ordinal if playlist else None)
        playlist_count = (
            _optional_int(raw.get("playlist_count"))
            or _optional_int(raw.get("n_entries"))
            or _optional_int((playlist or {}).get("playlist_count"))
            or _optional_int((playlist or {}).get("n_entries"))
        )
        source_url = _source_url(raw)
        thumbnails = raw.get("thumbnails")
        thumbnail_url = _best_thumbnail(thumbnails) or str(raw.get("thumbnail") or "")
        artist = (
            channel
            if self.options.use_channel_artist
            else normalize_text(str(raw.get("artist") or channel))
        )
        album = playlist_title if self.options.use_playlist_album else ""
        return AnalyzedEntry(
            source_url=source_url,
            webpage_url=str(raw.get("webpage_url") or source_url),
            video_id=str(raw.get("id") or ""),
            playlist_id=playlist_id,
            playlist_title=playlist_title,
            playlist_index=playlist_index,
            playlist_count=playlist_count,
            original_title=original_title,
            cleaned_title=cleaned,
            channel=channel,
            uploader=uploader,
            artist=artist,
            album_artist=channel,
            album=album,
            upload_date=str(raw.get("upload_date") or ""),
            duration=_optional_float(raw.get("duration")),
            thumbnail_url=thumbnail_url,
            extractor=str(raw.get("extractor_key") or raw.get("extractor") or ""),
        )


def parse_urls(text: str) -> list[str]:
    """Extract whitespace-separated URLs, preserving order and removing duplicates."""

    seen: set[str] = set()
    urls: list[str] = []
    for token in text.split():
        value = token.strip()
        if not value or value in seen:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Not a valid HTTP URL: {value}")
        seen.add(value)
        urls.append(value)
    return urls


def _has_entries(result: Mapping[str, Any]) -> bool:
    return result.get("_type") in {"playlist", "multi_video"} or result.get("entries") is not None


def _source_url(raw: Mapping[str, Any]) -> str:
    for key in ("webpage_url", "original_url"):
        value = raw.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    raw_url = str(raw.get("url") or "")
    if raw_url.startswith(("http://", "https://")):
        return raw_url
    video_id = str(raw.get("id") or raw_url)
    extractor = str(raw.get("extractor_key") or raw.get("ie_key") or "").casefold()
    if video_id and "youtube" in extractor:
        return f"https://www.youtube.com/watch?v={video_id}"
    return raw_url


def _best_thumbnail(value: object) -> str:
    if not isinstance(value, list):
        return ""
    candidates: list[tuple[int, str]] = []
    for thumbnail in value:
        if not isinstance(thumbnail, Mapping) or not thumbnail.get("url"):
            continue
        width = _optional_int(thumbnail.get("width")) or 0
        height = _optional_int(thumbnail.get("height")) or 0
        area = width * height
        # Prefer a useful but bounded source; the thumbnail service applies a hard resize.
        penalty = 10**12 if width > 2560 or height > 2560 else 0
        candidates.append((area - penalty, str(thumbnail["url"])))
    return max(candidates, default=(0, ""), key=lambda pair: pair[0])[1]


def _optional_int(value: object) -> int | None:
    try:
        return int(cast(Any, value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
