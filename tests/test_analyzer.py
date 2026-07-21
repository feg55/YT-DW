from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

import pytest
from yt_dlp.cookies import CookieLoadError

from openmediadl.core.analyzer import AnalysisOptions, AnalyzedEntry, Analyzer, parse_urls
from openmediadl.core.browser_cookies import BrowserCookiesUnavailableError
from openmediadl.domain.settings import CookieBrowser
from openmediadl.workers.analysis_worker import AnalysisWorker


def test_parse_urls_deduplicates_and_preserves_order() -> None:
    assert parse_urls(
        "https://example.test/a\n\n https://example.test/b\nhttps://example.test/a"
    ) == [
        "https://example.test/a",
        "https://example.test/b",
    ]


def test_parse_urls_rejects_non_http_input() -> None:
    with pytest.raises(ValueError, match="valid HTTP URL"):
        parse_urls("not a url")


def test_analysis_worker_continues_after_one_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def analyze(self: Analyzer, urls: list[str]) -> Iterator[AnalyzedEntry]:
        source = urls[0]
        if source.endswith("/bad"):
            raise ValueError("unsupported URL")
        yield AnalyzedEntry(source_url=source, video_id=source.rsplit("/", 1)[-1])

    monkeypatch.setattr(Analyzer, "analyze", analyze)
    worker = AnalysisWorker(
        ["https://example.test/first", "https://example.test/bad", "https://example.test/last"],
        AnalysisOptions(),
        batch_size=1,
    )
    batches: list[list[tuple[AnalyzedEntry, str]]] = []
    errors: list[str] = []
    results: list[tuple[bool, int]] = []
    worker.batch_ready.connect(batches.append)
    worker.item_error.connect(lambda _category, message, _technical: errors.append(message))
    worker.analysis_finished.connect(lambda cancelled, count: results.append((cancelled, count)))

    worker.run()

    assert [batch[0][0].video_id for batch in batches] == ["first", "last"]
    assert len(errors) == 1
    assert results == [(False, 2)]


def test_analyzer_emits_more_than_100_entries_incrementally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options: dict[str, Any]) -> None:
            self.options = options

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> dict[str, Any]:
            assert download is False
            callback = self.options["match_filter"]
            for index in range(1, 176):
                entry = {
                    "id": f"video-{index}",
                    "title": f"Channel — Track {index}",
                    "channel": "Channel",
                    "playlist_id": "playlist",
                    "playlist_title": "Album",
                    "playlist_index": index,
                    "playlist_count": 175,
                    "webpage_url": f"https://example.test/watch/{index}",
                    "thumbnail": f"https://example.test/thumb/{index}.jpg",
                    "extractor_key": "Example",
                }
                callback(entry, incomplete=True)
                callback(entry, incomplete=False)
            return {"_type": "playlist", "entries": []}

    monkeypatch.setattr("openmediadl.core.analyzer.yt_dlp.YoutubeDL", FakeYoutubeDL)
    emitted: list[AnalyzedEntry] = []
    analyzer = Analyzer(
        AnalysisOptions(use_playlist_track=False),
        entry_callback=emitted.append,
    )

    returned = list(analyzer.analyze(["https://example.test/playlist"]))

    assert returned == []
    assert len(emitted) == 175
    assert emitted[0].cleaned_title == "Track 1"
    assert emitted[-1].playlist_index == 175
    assert emitted[-1].artist == "Channel"


def test_auto_browser_retries_authentication_error_with_detected_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    outcomes: Iterator[object] = iter(
        [
            RuntimeError("Sign in to confirm your age; use --cookies-from-browser"),
            {
                "id": "video",
                "title": "Track",
                "webpage_url": "https://example.test/video",
            },
        ]
    )

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, Any]) -> None:
            calls.append(options)

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> dict[str, Any]:
            assert download is False
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            assert isinstance(outcome, dict)
            return outcome

    monkeypatch.setattr("openmediadl.core.analyzer.yt_dlp.YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        "openmediadl.core.analyzer.cookie_specs_for_retry",
        lambda *_args: (("edge",),),
    )

    entries = list(Analyzer(AnalysisOptions()).analyze(["https://example.test/video"]))

    assert [entry.video_id for entry in entries] == ["video"]
    assert "cookiesfrombrowser" not in calls[0]
    assert calls[1]["cookiesfrombrowser"] == ("edge",)


def test_explicit_browser_reads_cookies_only_after_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    outcomes: Iterator[object] = iter(
        [
            RuntimeError("Sign in to confirm your age; use --cookies-from-browser"),
            {
                "id": "private",
                "title": "Private track",
                "webpage_url": "https://example.test/private",
            },
        ]
    )

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, Any]) -> None:
            calls.append(options)

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> dict[str, Any]:
            assert download is False
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            assert isinstance(outcome, dict)
            return outcome

    monkeypatch.setattr("openmediadl.core.analyzer.yt_dlp.YoutubeDL", FakeYoutubeDL)
    options = AnalysisOptions(cookies_browser="chrome", cookies_profile="Profile 2")

    entries = list(Analyzer(options).analyze(["https://example.test/private"]))

    assert [entry.video_id for entry in entries] == ["private"]
    assert "cookiesfrombrowser" not in calls[0]
    assert calls[1]["cookiesfrombrowser"] == ("chrome", "Profile 2")


def test_auto_cookie_load_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes: Iterator[BaseException] = iter(
        [
            RuntimeError("Sign in to confirm your age; use --cookies-from-browser"),
            CookieLoadError("failed to load cookies"),
        ]
    )

    class FakeYoutubeDL:
        def __init__(self, _options: dict[str, Any]) -> None:
            pass

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> dict[str, Any]:
            assert download is False
            raise next(outcomes)

    monkeypatch.setattr("openmediadl.core.analyzer.yt_dlp.YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        "openmediadl.core.analyzer.cookie_specs_for_retry",
        lambda *_args: (("edge",),),
    )

    with pytest.raises(BrowserCookiesUnavailableError, match="edge"):
        list(Analyzer(AnalysisOptions()).analyze(["https://example.test/private"]))


def test_analysis_cookie_retries_continue_on_load_and_authentication_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: Iterator[object] = iter(
        [
            RuntimeError("Sign in to confirm your age"),
            CookieLoadError("failed to load cookies"),
            RuntimeError("Sign in to confirm your age"),
            AnalyzedEntry(source_url="https://example.test/private", video_id="private"),
        ]
    )
    calls: list[dict[str, Any]] = []

    def analyze_once(
        _self: Analyzer,
        _url: str,
        options: dict[str, Any],
    ) -> Iterator[AnalyzedEntry]:
        calls.append(options)
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, AnalyzedEntry)
        yield outcome

    monkeypatch.setattr(Analyzer, "_analyze_once", analyze_once)
    monkeypatch.setattr(
        "openmediadl.core.analyzer.cookie_specs_for_retry",
        lambda *_args: (("chrome",), ("firefox",), ("edge",)),
    )

    entries = list(Analyzer(AnalysisOptions()).analyze(["https://example.test/private"]))

    assert [entry.video_id for entry in entries] == ["private"]
    assert [call.get("cookiesfrombrowser") for call in calls] == [
        None,
        ("chrome",),
        ("firefox",),
        ("edge",),
    ]


def test_analysis_cookie_retries_stop_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = threading.Event()
    calls = 0

    def analyze_once(
        _self: Analyzer,
        _url: str,
        options: dict[str, Any],
    ) -> Iterator[AnalyzedEntry]:
        nonlocal calls
        calls += 1
        if "cookiesfrombrowser" in options:
            cancel_event.set()
            raise CookieLoadError("failed to load cookies")
        raise RuntimeError("Sign in to confirm your age")
        yield  # pragma: no cover - keeps this helper an iterator

    monkeypatch.setattr(Analyzer, "_analyze_once", analyze_once)
    monkeypatch.setattr(
        "openmediadl.core.analyzer.cookie_specs_for_retry",
        lambda *_args: (("chrome",), ("firefox",)),
    )

    assert list(Analyzer(AnalysisOptions(), cancel_event).analyze(["https://example.test"])) == []
    assert calls == 2


def test_disabled_browser_does_not_retry_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, Any]) -> None:
            nonlocal calls
            calls += 1
            assert "cookiesfrombrowser" not in options

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> dict[str, Any]:
            assert download is False
            raise RuntimeError("Sign in to confirm your age")

    monkeypatch.setattr("openmediadl.core.analyzer.yt_dlp.YoutubeDL", FakeYoutubeDL)

    with pytest.raises(RuntimeError, match="Sign in"):
        list(
            Analyzer(AnalysisOptions(cookies_browser=CookieBrowser.DISABLED.value)).analyze(
                ["https://example.test/private"]
            )
        )
    assert calls == 1


def test_partial_playlist_auth_error_retries_without_duplicate_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, Any]) -> None:
            nonlocal calls
            calls += 1
            self.options = options

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> None:
            assert download is False
            self.options["match_filter"](
                {
                    "id": "available",
                    "title": "Available track",
                    "webpage_url": "https://example.test/available",
                    "extractor_key": "Example",
                },
                incomplete=True,
            )
            if "cookiesfrombrowser" not in self.options:
                self.options["logger"].error(
                    "Sign in to confirm your age; use --cookies-from-browser"
                )
            else:
                self.options["match_filter"](
                    {
                        "id": "protected",
                        "title": "Protected track",
                        "webpage_url": "https://example.test/protected",
                        "extractor_key": "Example",
                    },
                    incomplete=True,
                )
            return None

    monkeypatch.setattr("openmediadl.core.analyzer.yt_dlp.YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        "openmediadl.core.analyzer.cookie_specs_for_retry",
        lambda *_args: (("edge",),),
    )
    emitted: list[AnalyzedEntry] = []
    analyzer = Analyzer(AnalysisOptions(), entry_callback=emitted.append)

    returned = list(analyzer.analyze(["https://example.test/playlist"]))

    assert returned == []
    assert [entry.video_id for entry in emitted] == ["available", "protected"]
    assert calls == 2
