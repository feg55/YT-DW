from __future__ import annotations

import errno
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from yt_dlp.cookies import CookieLoadError
from yt_dlp.utils import DownloadCancelled

from openmediadl.core.browser_cookies import BrowserCookiesUnavailableError
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
from openmediadl.domain.settings import (
    CookieBrowser,
    DownloadSettings,
    MetadataSettings,
    VideoQuality,
)


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


def test_download_options_use_managed_deno_without_remote_components(tmp_path: Path) -> None:
    deno = tmp_path / "managed tools" / "deno.exe"
    downloader = Downloader(
        DownloadSettings(destination_directory=str(tmp_path)),
        MetadataSettings(),
        tmp_path / "archive.txt",
        js_runtime_path=deno,
    )
    item = DownloadItem.new(
        "https://example.test/video",
        cleaned_title="Track",
        download_mode=DownloadMode.AUDIO,
    )

    options = downloader._options(item, tmp_path / "Track.m4a")

    assert options["js_runtimes"] == {"deno": {"path": str(deno)}}
    assert "remote_components" not in options


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


def test_auto_browser_retries_authentication_with_detected_cookies(tmp_path: Path) -> None:
    downloader = _downloader(tmp_path)
    item = DownloadItem.new(
        "https://example.test/private",
        download_mode=DownloadMode.AUDIO,
    )
    options = downloader._options(item, tmp_path / "Track.m4a")
    options["cookiesfrombrowser"] = ("system",)
    downloaded = {"id": "private"}

    with (
        patch(
            "openmediadl.core.downloader.cookie_specs_for_retry",
            return_value=(("edge",),),
        ),
        patch.object(
            downloader,
            "_extract_info",
            side_effect=[RuntimeError("Sign in to confirm your age"), downloaded],
        ) as extract,
    ):
        result = downloader._download_info(item, options)

    assert result is downloaded
    assert "cookiesfrombrowser" not in extract.call_args_list[0].args[1]
    assert extract.call_args_list[1].args[1]["cookiesfrombrowser"] == ("edge",)


def test_explicit_browser_reads_cookies_only_after_authentication_error(tmp_path: Path) -> None:
    settings = DownloadSettings(
        destination_directory=str(tmp_path),
        cookie_browser=CookieBrowser.CHROME,
        cookie_profile="Profile 2",
    )
    downloader = Downloader(settings, MetadataSettings(), tmp_path / "archive.txt")
    item = DownloadItem.new(
        "https://example.test/public",
        download_mode=DownloadMode.AUDIO,
    )
    options = downloader._options(item, tmp_path / "Track.m4a")
    downloaded = {"id": "public"}

    with patch.object(
        downloader,
        "_extract_info",
        side_effect=[RuntimeError("Sign in to confirm your age"), downloaded],
    ) as extract:
        result = downloader._download_info(item, options)

    assert result is downloaded
    assert "cookiesfrombrowser" not in extract.call_args_list[0].args[1]
    assert extract.call_args_list[1].args[1]["cookiesfrombrowser"] == (
        "chrome",
        "Profile 2",
    )


def test_auto_cookie_load_failure_is_actionable_for_download(tmp_path: Path) -> None:
    downloader = _downloader(tmp_path)
    item = DownloadItem.new(
        "https://example.test/private",
        download_mode=DownloadMode.AUDIO,
    )

    with (
        patch(
            "openmediadl.core.downloader.cookie_specs_for_retry",
            return_value=(("edge",),),
        ),
        patch.object(
            downloader,
            "_extract_info",
            side_effect=[
                RuntimeError("Sign in to confirm your age"),
                CookieLoadError("failed to load cookies"),
            ],
        ),
        pytest.raises(BrowserCookiesUnavailableError, match="edge"),
    ):
        downloader._download_info(item, {})


def test_download_cookie_retries_continue_on_load_and_authentication_errors(
    tmp_path: Path,
) -> None:
    downloader = _downloader(tmp_path)
    item = DownloadItem.new(
        "https://example.test/private",
        download_mode=DownloadMode.AUDIO,
    )
    downloaded = {"id": "private"}

    with (
        patch(
            "openmediadl.core.downloader.cookie_specs_for_retry",
            return_value=(("chrome",), ("firefox",), ("edge",)),
        ),
        patch.object(
            downloader,
            "_extract_info",
            side_effect=[
                RuntimeError("Sign in to confirm your age"),
                CookieLoadError("failed to load cookies"),
                RuntimeError("Sign in to confirm your age"),
                downloaded,
            ],
        ) as extract,
    ):
        result = downloader._download_info(item, {})

    assert result is downloaded
    assert [call.args[1].get("cookiesfrombrowser") for call in extract.call_args_list] == [
        None,
        ("chrome",),
        ("firefox",),
        ("edge",),
    ]


def test_download_cookie_retries_stop_when_cancelled(tmp_path: Path) -> None:
    cancel_event = threading.Event()
    downloader = Downloader(
        DownloadSettings(destination_directory=str(tmp_path)),
        MetadataSettings(),
        tmp_path / "archive.txt",
        cancel_event,
    )
    item = DownloadItem.new(
        "https://example.test/private",
        download_mode=DownloadMode.AUDIO,
    )
    calls = 0

    def extract(_url: str, options: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if "cookiesfrombrowser" in options:
            cancel_event.set()
            raise CookieLoadError("failed to load cookies")
        raise RuntimeError("Sign in to confirm your age")

    with (
        patch(
            "openmediadl.core.downloader.cookie_specs_for_retry",
            return_value=(("chrome",), ("firefox",)),
        ),
        patch.object(downloader, "_extract_info", side_effect=extract),
        pytest.raises(DownloadCancelled),
    ):
        downloader._download_info(item, {})
    assert calls == 2


def test_disabled_browser_does_not_retry_download_authentication(tmp_path: Path) -> None:
    settings = DownloadSettings(
        destination_directory=str(tmp_path),
        cookie_browser=CookieBrowser.DISABLED,
    )
    downloader = Downloader(settings, MetadataSettings(), tmp_path / "archive.txt")
    item = DownloadItem.new(
        "https://example.test/private",
        download_mode=DownloadMode.AUDIO,
    )

    with (
        patch.object(
            downloader,
            "_extract_info",
            side_effect=RuntimeError("Sign in to confirm your age"),
        ) as extract,
        pytest.raises(RuntimeError, match="Sign in"),
    ):
        downloader._download_info(item, {})
    extract.assert_called_once()


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
