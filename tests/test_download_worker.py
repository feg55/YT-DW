from __future__ import annotations

from pathlib import Path

from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import DownloadSettings, MetadataSettings
from openmediadl.workers.download_worker import DownloadQueueWorker


def _worker(tmp_path: Path) -> DownloadQueueWorker:
    return DownloadQueueWorker(
        [],
        DownloadSettings(destination_directory=str(tmp_path)),
        MetadataSettings(),
        tmp_path / "unused-archive.txt",
    )


def test_metadata_retry_keeps_existing_media_path_with_stale_progress(tmp_path: Path) -> None:
    media = tmp_path / "downloaded.m4a"
    media.write_bytes(b"downloaded media")
    item = DownloadItem.new(
        "https://example.test/track",
        video_id="track",
        cleaned_title="Track",
        download_mode=DownloadMode.AUDIO,
        status=DownloadStatus.READY,
        progress_percentage=30,
        final_media_path=str(media),
        error_category="metadata_writing_failed",
    )

    prepared = _worker(tmp_path)._prepare_items([item])

    assert prepared == [item]
    assert item.final_media_path == str(media)


def test_non_metadata_retry_does_not_reuse_an_unrelated_existing_path(tmp_path: Path) -> None:
    media = tmp_path / "track.m4a"
    media.write_bytes(b"existing")
    item = DownloadItem.new(
        "https://example.test/track",
        video_id="track",
        cleaned_title="Track",
        download_mode=DownloadMode.AUDIO,
        status=DownloadStatus.READY,
        progress_percentage=30,
        final_media_path=str(media),
        error_category="network_timeout",
    )

    _worker(tmp_path)._prepare_items([item])

    assert item.final_media_path != str(media)
    assert Path(item.final_media_path or "").name == "track (track).m4a"
