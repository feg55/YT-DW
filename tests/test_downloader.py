from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from openmediadl.core.downloader import Downloader, DownloadPipelineError, _quality_warning
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
