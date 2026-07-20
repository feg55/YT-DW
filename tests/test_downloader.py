from __future__ import annotations

import errno
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from yt_dlp.utils import DownloadCancelled

from openmediadl.core.downloader import (
    _FFMPEG_CANCEL_EVENT,
    Downloader,
    DownloadPipelineError,
    _CancellableFFmpegPopen,
    _is_http_forbidden,
    _quality_warning,
)
from openmediadl.core.error_mapper import ErrorCategory
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import DownloadSettings, MetadataSettings, VideoQuality


def _downloader(tmp_path: Path) -> Downloader:
    return Downloader(
        DownloadSettings(destination_directory=str(tmp_path)),
        MetadataSettings(),
        tmp_path / "archive.txt",
    )


def test_output_template_escapes_literal_percent_signs(tmp_path: Path) -> None:
    downloader = _downloader(tmp_path)
    item = DownloadItem.new(
        "https://example.test/video",
        cleaned_title="100% Hit",
        download_mode=DownloadMode.AUDIO,
    )

    options = downloader._options(item, tmp_path / "100% Hit.m4a")

    assert str(options["outtmpl"]).endswith("100%% Hit.%(ext)s")


def test_audio_options_match_cli_source_selection_without_forcing_ffmpeg(
    tmp_path: Path,
) -> None:
    downloader = _downloader(tmp_path)
    item = DownloadItem.new(
        "https://example.test/video",
        cleaned_title="Track",
        download_mode=DownloadMode.AUDIO,
    )

    options = downloader._options(item, tmp_path / "Track.m4a")

    assert options["format"] == "bestaudio/best"
    assert "external_downloader" not in options
    assert options["postprocessors"] == [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "5",
        }
    ]


def test_audio_http_403_retries_once_through_ffmpeg(tmp_path: Path) -> None:
    snapshots = []
    downloader = Downloader(
        DownloadSettings(destination_directory=str(tmp_path)),
        MetadataSettings(),
        tmp_path / "archive.txt",
        progress_callback=lambda _item, snapshot: snapshots.append(snapshot),
    )
    item = DownloadItem.new(
        "https://example.test/video",
        download_mode=DownloadMode.AUDIO,
        progress_percentage=37.0,
        downloaded_bytes=123,
        total_bytes=456,
        speed=78.0,
        eta=9.0,
    )
    options = downloader._options(item, tmp_path / "Track.m4a")
    downloaded = {"id": "video"}

    with patch.object(
        downloader,
        "_extract_info",
        side_effect=[RuntimeError("HTTP Error 403: Forbidden"), downloaded],
    ) as extract:
        result = downloader._download_info(item, options)

    assert result is downloaded
    assert extract.call_count == 2
    first_options = extract.call_args_list[0].args[1]
    fallback_options = extract.call_args_list[1].args[1]
    assert "external_downloader" not in first_options
    assert fallback_options["external_downloader"] == {"default": "ffmpeg"}
    assert "external_downloader" not in options
    assert item.progress_percentage == 0.0
    assert item.downloaded_bytes == 0
    assert item.total_bytes is None
    assert item.speed is None
    assert item.eta is None
    assert snapshots[-1].phase == "Retrying with FFmpeg"
    assert snapshots[-1].speed is None
    assert snapshots[-1].eta is None


def test_cancellable_ffmpeg_wait_terminates_its_exact_child() -> None:
    cancel_event = threading.Event()
    token = _FFMPEG_CANCEL_EVENT.set(cancel_event)
    process = _CancellableFFmpegPopen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    timer = threading.Timer(0.05, cancel_event.set)
    timer.start()
    try:
        with pytest.raises(DownloadCancelled):
            with process:
                try:
                    process.wait()
                except DownloadCancelled:
                    # FFmpegFD performs this second cleanup after wait raises.
                    process.kill(timeout=None)
                    raise
        assert process.poll() is not None
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill(timeout=None)
        _FFMPEG_CANCEL_EVENT.reset(token)


def test_non_403_and_video_failures_do_not_use_ffmpeg_fallback(tmp_path: Path) -> None:
    downloader = _downloader(tmp_path)
    audio = DownloadItem.new(
        "https://example.test/audio",
        download_mode=DownloadMode.AUDIO,
    )
    video = DownloadItem.new(
        "https://example.test/video",
        download_mode=DownloadMode.VIDEO,
    )

    for item, error in (
        (audio, RuntimeError("HTTP Error 429: Too Many Requests")),
        (video, RuntimeError("HTTP Error 403: Forbidden")),
    ):
        with (
            patch.object(downloader, "_extract_info", side_effect=error) as extract,
            pytest.raises(RuntimeError),
        ):
            downloader._download_info(item, {})
        extract.assert_called_once()


def test_http_403_detection_follows_yt_dlp_exc_info() -> None:
    original = RuntimeError("HTTP Error 403: Forbidden")
    wrapped = RuntimeError("unable to download video data")
    wrapped.exc_info = (RuntimeError, original, None)  # type: ignore[attr-defined]

    assert _is_http_forbidden(wrapped)
    assert not _is_http_forbidden(RuntimeError("HTTP Error 429: Too Many Requests"))


def test_metadata_retry_reuses_existing_m4a_without_redownload(tmp_path: Path) -> None:
    output = tmp_path / "track.m4a"
    output.write_bytes(b"already-downloaded")
    item = DownloadItem.new(
        "https://example.test/video",
        cleaned_title="Track",
        artist="Channel",
        final_media_path=str(output),
        download_mode=DownloadMode.AUDIO,
        status=DownloadStatus.FAILED,
    )
    downloader = _downloader(tmp_path)

    with (
        patch.object(downloader, "_finalize_audio") as finalize,
        patch("openmediadl.core.downloader.yt_dlp.YoutubeDL") as youtube_dl,
    ):
        result = downloader.download(item)

    finalize.assert_called_once_with(item, output)
    youtube_dl.assert_not_called()
    assert result.path == output
    assert item.status is DownloadStatus.COMPLETED


def test_requested_video_quality_warning() -> None:
    assert _quality_warning({"height": 720}, VideoQuality.FULL_HD_1080) == (
        "Requested 1080p was unavailable; downloaded 720p."
    )
    assert _quality_warning({"height": 2160}, VideoQuality.FULL_HD_1080) == ""


def test_metadata_finalization_preserves_disk_full_category(tmp_path: Path) -> None:
    output = tmp_path / "track.m4a"
    output.write_bytes(b"media")
    item = DownloadItem.new(
        "https://example.test/video",
        cleaned_title="Track",
        artist="Artist",
        download_mode=DownloadMode.AUDIO,
    )
    downloader = _downloader(tmp_path)
    downloader.metadata_settings.embed_thumbnail_as_cover = False
    downloader.metadata_settings.save_cover_as_separate_jpeg = False

    with (
        patch(
            "openmediadl.core.downloader.MetadataWriter.write",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        ),
        pytest.raises(DownloadPipelineError) as raised,
    ):
        downloader._finalize_audio(item, output)

    assert raised.value.mapped.category is ErrorCategory.DISK_FULL
