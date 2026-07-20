from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from openmediadl.core.analyzer import AnalysisOptions, AnalyzedEntry, Analyzer, parse_urls
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
