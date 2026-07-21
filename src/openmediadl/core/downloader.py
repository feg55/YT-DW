"""Local yt-dlp download pipeline with deterministic M4A finalization."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.downloader import external as ytdlp_external
from yt_dlp.utils import DownloadCancelled
from yt_dlp.utils import Popen as YtdlpPopen

from openmediadl.core.browser_cookies import (
    BrowserCookiesUnavailableError,
    cookie_specs_for_retry,
    is_authentication_error,
    is_cookie_load_error,
    normalize_browser_choice,
)
from openmediadl.core.error_mapper import ErrorCategory, MappedError, map_error
from openmediadl.core.filename_service import ensure_unique_path
from openmediadl.core.metadata_writer import MetadataTags, MetadataWriter
from openmediadl.core.progress import ProgressSnapshot, parse_progress_hook
from openmediadl.core.thumbnail_service import (
    ThumbnailProcessingError,
    ThumbnailService,
    prepare_cover_image,
)
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import (
    CookieBrowser,
    DownloadSettings,
    MetadataSettings,
    VideoQuality,
)

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[DownloadItem, ProgressSnapshot], None]
PhaseCallback = Callable[[DownloadItem, str], None]

_FFMPEG_CANCEL_EVENT: ContextVar[threading.Event | None] = ContextVar(
    "openmediadl_ffmpeg_cancel_event",
    default=None,
)


class _CancellableFFmpegPopen(YtdlpPopen):
    """Poll only YT-DW's active fallback FFmpeg for cancellation."""

    _POLL_INTERVAL = 0.1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cancelled_by_openmediadl = False

    def wait(self, timeout: float | None = None) -> int:
        cancel_event = _FFMPEG_CANCEL_EVENT.get()
        if cancel_event is None or self._cancelled_by_openmediadl:
            return super().wait(timeout=timeout)

        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            return_code = self.poll()
            if return_code is not None:
                return return_code
            if cancel_event.is_set():
                self._cancelled_by_openmediadl = True
                # This process handle belongs to the current fallback. Never
                # enumerate or terminate other ffmpeg processes by name.
                try:
                    YtdlpPopen.kill(self, timeout=0)
                except ProcessLookupError:
                    pass
                subprocess.Popen.wait(self)
                raise DownloadCancelled()

            wait_for = self._POLL_INTERVAL
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(
                        self.args,
                        timeout if timeout is not None else 0.0,
                    )
                wait_for = min(wait_for, remaining)
            try:
                return super().wait(timeout=wait_for)
            except subprocess.TimeoutExpired:
                continue

    def kill(self, *, timeout: float | None = 0) -> None:
        """Keep yt-dlp's cleanup idempotent after cancellation reaped the child."""

        if self.poll() is None:
            super().kill(timeout=timeout)
        elif timeout != 0:
            subprocess.Popen.wait(self, timeout=timeout)


def _enable_cancellable_external_ffmpeg() -> None:
    """Install a thread-scoped wait adapter for yt-dlp's external downloaders."""

    if ytdlp_external.Popen is not _CancellableFFmpegPopen:
        ytdlp_external.Popen = _CancellableFFmpegPopen


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path | None
    skipped: bool = False
    warning: str = ""


class DownloadPipelineError(RuntimeError):
    def __init__(self, mapped: MappedError, output_path: Path | None = None) -> None:
        super().__init__(mapped.message)
        self.mapped = mapped
        self.output_path = output_path


class _YtdlpLogger:
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
        LOGGER.error("yt-dlp: %s", message)


class Downloader:
    """Download one item. Call from a worker thread, never the Qt GUI thread."""

    def __init__(
        self,
        download_settings: DownloadSettings,
        metadata_settings: MetadataSettings,
        archive_path: Path,
        cancel_event: threading.Event | None = None,
        progress_callback: ProgressCallback | None = None,
        phase_callback: PhaseCallback | None = None,
        thumbnail_service: ThumbnailService | None = None,
    ) -> None:
        self.download_settings = download_settings
        self.metadata_settings = metadata_settings
        self.archive_path = archive_path
        self.cancel_event = cancel_event or threading.Event()
        self.progress_callback = progress_callback
        self.phase_callback = phase_callback
        self.thumbnail_service = thumbnail_service or ThumbnailService(
            archive_path.parent / "thumbnail-cache"
        )
        self._last_progress_emit = 0.0
        self._active_item: DownloadItem | None = None

    def download(self, item: DownloadItem) -> DownloadResult:
        if self.cancel_event.is_set():
            raise DownloadPipelineError(map_error("The download was cancelled"))
        destination = Path(self.download_settings.destination_directory).expanduser()
        destination.mkdir(parents=True, exist_ok=True)
        existing = Path(item.final_media_path) if item.final_media_path else None
        if (
            item.download_mode is DownloadMode.AUDIO
            and existing is not None
            and existing.is_file()
            and existing.suffix.casefold() == ".m4a"
        ):
            # Retry a metadata step that failed after the playable media was
            # downloaded; do not redownload or discard the successful file.
            self._active_item = item
            item.status = DownloadStatus.PROCESSING
            self._finalize_audio(item, existing)
            item.status = DownloadStatus.COMPLETED
            item.progress_percentage = 100.0
            item.touch()
            self._phase(item, "Completed")
            return DownloadResult(path=existing)
        final = self._allocate_output(item, destination)
        item.final_media_path = str(final)
        self._active_item = item
        item.status = DownloadStatus.DOWNLOADING
        item.touch()
        self._phase(
            item,
            "Downloading audio"
            if item.download_mode is DownloadMode.AUDIO
            else "Downloading video",
        )

        options = self._options(item, final)
        output: Path | None = None
        try:
            info = self._download_info(item, options)
            if self.cancel_event.is_set():
                raise DownloadCancelled()
            output = self._locate_output(final, info)
            if output is None:
                raise FileNotFoundError(f"yt-dlp did not produce the expected output near {final}")
            item.final_media_path = str(output)
            if item.download_mode is DownloadMode.AUDIO:
                # Persisted processing snapshots can safely resume metadata-only
                # work after a crash without redownloading the finished media.
                item.progress_percentage = 100.0
                item.touch()
                self._finalize_audio(item, output)
            item.status = DownloadStatus.COMPLETED
            item.progress_percentage = 100.0
            item.touch()
            self._phase(item, "Completed")
            warning = (
                _quality_warning(info, self.download_settings.video_quality)
                if item.download_mode is DownloadMode.VIDEO
                else ""
            )
            return DownloadResult(path=output, warning=warning)
        except DownloadPipelineError:
            raise
        except DownloadCancelled as error:
            raise DownloadPipelineError(map_error(error), output) from error
        except ThumbnailProcessingError as error:
            mapped = MappedError(
                ErrorCategory.THUMBNAIL_CONVERSION_FAILED,
                "The media downloaded, but its thumbnail could not be prepared.",
                str(error),
            )
            raise DownloadPipelineError(mapped, output) from error
        except Exception as error:
            raise DownloadPipelineError(map_error(error), output) from error

    def _download_info(self, item: DownloadItem, options: dict[str, Any]) -> Any:
        """Download with cookie recovery around the normal transport fallback."""

        browser = normalize_browser_choice(self.download_settings.cookie_browser)
        public_options = dict(options)
        public_options.pop("cookiesfrombrowser", None)
        public_options.pop("cookiefile", None)
        try:
            return self._download_with_transport_fallback(item, public_options)
        except Exception as error:
            if not is_authentication_error(error) or browser is CookieBrowser.DISABLED:
                raise
            authentication_error = error

        if self.cancel_event.is_set():
            raise DownloadCancelled() from authentication_error
        attempts: list[str] = []
        last_error: BaseException = authentication_error
        for cookies in cookie_specs_for_retry(browser, self.download_settings.cookie_profile):
            if self.cancel_event.is_set():
                raise DownloadCancelled() from last_error
            attempts.append(cookies[0])
            LOGGER.info(
                "Authentication is required; retrying download with cookies from %s",
                cookies[0],
            )
            authenticated_options = dict(public_options)
            authenticated_options["cookiesfrombrowser"] = cookies
            try:
                return self._download_with_transport_fallback(item, authenticated_options)
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

    def _download_with_transport_fallback(
        self,
        item: DownloadItem,
        options: dict[str, Any],
    ) -> Any:
        """Download once natively, retrying audio HTTP 403s through FFmpeg."""

        try:
            return self._extract_info(item.source_url, options)
        except Exception as error:
            if item.download_mode is not DownloadMode.AUDIO or not _is_http_forbidden(error):
                raise
            if self.cancel_event.is_set():
                raise DownloadCancelled() from error

            # The native downloader provides useful byte-level progress, so it
            # remains the normal path. FFmpeg is only used for the YouTube media
            # URL variant that rejected that first request with HTTP 403.
            LOGGER.warning(
                "Native audio download returned HTTP 403 for %s; retrying through FFmpeg",
                item.source_url,
            )
            self._reset_for_ffmpeg_fallback(item)
            fallback_options = dict(options)
            fallback_options["external_downloader"] = {"default": "ffmpeg"}
            _enable_cancellable_external_ffmpeg()
            token = _FFMPEG_CANCEL_EVENT.set(self.cancel_event)
            try:
                return self._extract_info(item.source_url, fallback_options)
            finally:
                _FFMPEG_CANCEL_EVENT.reset(token)

    def _reset_for_ffmpeg_fallback(self, item: DownloadItem) -> None:
        """Clear stale native-download telemetry before the fallback starts."""

        item.progress_percentage = 0.0
        item.downloaded_bytes = 0
        item.total_bytes = None
        item.speed = None
        item.eta = None
        item.current_phase = "Retrying with FFmpeg"
        item.touch()
        self._last_progress_emit = 0.0
        if self.progress_callback:
            self.progress_callback(
                item,
                ProgressSnapshot(
                    status="downloading",
                    phase=item.current_phase,
                    percentage=0.0,
                    downloaded_bytes=0,
                    total_bytes=None,
                    speed=None,
                    eta=None,
                    filename=None,
                ),
            )

    @staticmethod
    def _extract_info(source_url: str, options: dict[str, Any]) -> Any:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(source_url, download=True)

    def _options(self, item: DownloadItem, final: Path) -> dict[str, Any]:
        base = final.with_suffix("")
        options: dict[str, Any] = {
            "logger": _YtdlpLogger(),
            "quiet": True,
            "no_warnings": False,
            "noplaylist": True,
            # yt-dlp templates use %-formatting; escape literal percent signs
            # that came from the user-visible title/path.
            "outtmpl": str(base).replace("%", "%%") + ".%(ext)s",
            "continuedl": self.download_settings.continue_partial_downloads,
            "nopart": not self.download_settings.continue_partial_downloads,
            "overwrites": False,
            "retries": self.download_settings.retry_count,
            "fragment_retries": self.download_settings.fragment_retry_count,
            "socket_timeout": self.download_settings.socket_timeout,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
        }
        if self.download_settings.ffmpeg_directory:
            options["ffmpeg_location"] = self.download_settings.ffmpeg_directory
        if self.download_settings.bandwidth_limit:
            options["ratelimit"] = self.download_settings.bandwidth_limit
        if item.download_mode is DownloadMode.AUDIO:
            # Match `yt-dlp -x --audio-format m4a`: select the best source
            # audio first, then let FFmpeg produce the requested M4A container.
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "5",
                },
            ]
        else:
            options["format"] = _video_format(self.download_settings.video_quality)
            options["merge_output_format"] = "mp4"
            options["writethumbnail"] = True
            options["postprocessors"] = [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
        return options

    def _progress_hook(self, data: Mapping[str, Any]) -> None:
        if self.cancel_event.is_set():
            raise DownloadCancelled()
        now = time.monotonic()
        status = str(data.get("status") or "")
        if status == "downloading" and now - self._last_progress_emit < 0.15:
            return
        self._last_progress_emit = now
        snapshot = parse_progress_hook(data)
        if self._active_item is not None and snapshot.status == "downloading":
            snapshot = replace(
                snapshot,
                phase=(
                    "Downloading audio"
                    if self._active_item.download_mode is DownloadMode.AUDIO
                    else "Downloading video"
                ),
            )
        if self.progress_callback:
            if self._active_item is not None:
                self.progress_callback(self._active_item, snapshot)

    def _postprocessor_hook(self, data: Mapping[str, Any]) -> None:
        if self.cancel_event.is_set():
            raise DownloadCancelled()
        item = self._active_item
        if item is None:
            return
        name = str(data.get("postprocessor") or "Processing")
        self._phase(item, _postprocessor_phase(name))

    def _finalize_audio(self, item: DownloadItem, output: Path) -> None:
        item.status = DownloadStatus.PROCESSING
        self._phase(item, "Writing metadata")
        temporary_cover: Path | None = None
        cover_to_embed: Path | None = None
        try:
            needs_cover_file = (
                self.metadata_settings.embed_thumbnail_as_cover
                or self.metadata_settings.save_cover_as_separate_jpeg
            )
            cached = Path(item.cached_thumbnail_path) if item.cached_thumbnail_path else None
            if needs_cover_file and item.thumbnail_url:
                self._phase(item, "Downloading thumbnail")
                try:
                    cached = self.thumbnail_service.download(
                        item.thumbnail_url,
                        f"{item.source_url}|{item.thumbnail_url}|cover",
                        min_size=600,
                        max_size=1_400,
                    ).path
                    item.cached_thumbnail_path = str(cached)
                except ThumbnailProcessingError:
                    # A previously cached preview is still a valid last-resort
                    # source; Pillow will prepare the bounded cover variant.
                    if cached is None or not cached.is_file():
                        raise
            if needs_cover_file and (cached is None or not cached.is_file()):
                if not item.thumbnail_url:
                    raise ThumbnailProcessingError(
                        "No source thumbnail is available for the requested cover options"
                    )
                raise ThumbnailProcessingError("The source thumbnail could not be cached")
            if needs_cover_file and cached and cached.is_file():
                self._phase(item, "Embedding cover")
                temporary_cover = output.with_name(f".{output.stem}.cover.jpg")
                cover_to_embed = prepare_cover_image(
                    cached,
                    temporary_cover,
                    crop_square=self.metadata_settings.crop_cover_to_square,
                )

            track_number = item.track_number
            if track_number is None and self.metadata_settings.use_playlist_index_as_track_number:
                track_number = item.playlist_index
            year = (
                item.upload_date[:4]
                if self.metadata_settings.store_upload_year and item.upload_date
                else None
            )
            source = (
                item.source_url if self.metadata_settings.store_original_url_in_comment else None
            )
            artist = (
                item.artist.strip()
                or item.channel.strip()
                or item.uploader.strip()
                or "Unknown artist"
            )
            album_artist = item.album_artist.strip() or item.channel.strip() or artist
            MetadataWriter().write(
                output,
                MetadataTags(
                    title=item.cleaned_title.strip() or item.original_title.strip() or output.stem,
                    artist=artist,
                    album_artist=album_artist
                    if self.metadata_settings.use_channel_name_as_album_artist
                    else artist,
                    album=item.album or None,
                    track_number=track_number,
                    track_total=item.playlist_count if track_number is not None else None,
                    year=year,
                    source_url=source,
                ),
                cover_to_embed if self.metadata_settings.embed_thumbnail_as_cover else None,
                verify=True,
            )
            self._phase(item, "Verifying output")
            MetadataWriter().verify(
                output,
                require_cover=self.metadata_settings.embed_thumbnail_as_cover,
            )
            if self.metadata_settings.save_cover_as_separate_jpeg and cover_to_embed:
                separate = ensure_unique_path(output.with_suffix(".jpg"))
                shutil.copy2(cover_to_embed, separate)
        except ThumbnailProcessingError as error:
            mapped = MappedError(
                ErrorCategory.THUMBNAIL_CONVERSION_FAILED,
                "The media downloaded, but its thumbnail could not be prepared.",
                str(error),
            )
            raise DownloadPipelineError(mapped, output) from error
        except Exception as error:
            mapped = map_error(error)
            if mapped.category is ErrorCategory.UNKNOWN:
                mapped = MappedError(
                    ErrorCategory.METADATA_WRITING_FAILED,
                    "The media downloaded, but M4A metadata could not be finalized.",
                    str(error),
                )
            raise DownloadPipelineError(mapped, output) from error
        finally:
            if temporary_cover:
                temporary_cover.unlink(missing_ok=True)

    def _allocate_output(self, item: DownloadItem, destination: Path) -> Path:
        extension = ".m4a" if item.download_mode is DownloadMode.AUDIO else ".mp4"
        if item.final_media_path:
            proposed = Path(item.final_media_path)
            if proposed.parent != destination:
                proposed = destination / proposed.name
        else:
            title = item.cleaned_title or item.original_title or item.video_id or "untitled"
            from openmediadl.core.filename_service import sanitize_filename

            proposed = destination / f"{sanitize_filename(title)}{extension}"
        preferred = item.playlist_index or item.video_id
        return ensure_unique_path(proposed.with_suffix(extension), preferred_suffix=preferred)

    @staticmethod
    def _locate_output(final: Path, info: object) -> Path | None:
        expected = final.with_suffix(final.suffix)
        if expected.is_file():
            return expected
        if isinstance(info, Mapping):
            for key in ("filepath", "_filename"):
                value = info.get(key)
                if value and Path(str(value)).is_file():
                    return Path(str(value))
            requested = info.get("requested_downloads")
            if isinstance(requested, list):
                for entry in requested:
                    if (
                        isinstance(entry, Mapping)
                        and entry.get("filepath")
                        and Path(str(entry["filepath"])).is_file()
                    ):
                        candidate = Path(str(entry["filepath"]))
                        converted = candidate.with_suffix(final.suffix)
                        return converted if converted.is_file() else candidate
        candidates = sorted(
            (
                path
                for path in final.parent.glob(f"{final.stem}.*")
                if path.suffix.casefold()
                in {
                    ".m4a",
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".mov",
                    ".avi",
                    ".aac",
                    ".opus",
                    ".ogg",
                    ".flac",
                }
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return next((path for path in candidates if path.is_file()), None)

    def _phase(self, item: DownloadItem, phase: str) -> None:
        item.current_phase = phase
        if self.phase_callback:
            self.phase_callback(item, phase)


def _video_format(quality: VideoQuality) -> str:
    if quality is VideoQuality.BEST:
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
    height = int(quality.value.removesuffix("p"))
    return (
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
    )


def _postprocessor_phase(name: str) -> str:
    lowered = name.casefold()
    if "extractaudio" in lowered or "convert" in lowered:
        return "Converting"
    if "merger" in lowered or "merge" in lowered:
        return "Merging"
    if "metadata" in lowered:
        return "Writing metadata"
    if "thumbnail" in lowered:
        return "Embedding cover"
    return "Processing"


def _is_http_forbidden(error: BaseException) -> bool:
    """Recognize yt-dlp's wrapped and direct HTTP 403 failures."""

    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        status = getattr(current, "code", None) or getattr(current, "status", None)
        text = str(current).casefold()
        if status == 403 or "http error 403" in text or ("403" in text and "forbidden" in text):
            return True

        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            original = exc_info[1]
            if isinstance(original, BaseException):
                pending.append(original)
    return False


def _quality_warning(info: object, quality: VideoQuality) -> str:
    if quality is VideoQuality.BEST or not isinstance(info, Mapping):
        return ""
    requested = int(quality.value.removesuffix("p"))
    actual = info.get("height")
    try:
        actual_height = int(actual) if actual is not None else None
    except (TypeError, ValueError):
        actual_height = None
    if actual_height is not None and actual_height < requested:
        return f"Requested {requested}p was unavailable; downloaded {actual_height}p."
    return ""
