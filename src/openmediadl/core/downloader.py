"""Local yt-dlp download pipeline with deterministic M4A finalization."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadCancelled

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
from openmediadl.domain.settings import DownloadSettings, MetadataSettings, VideoQuality

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[[DownloadItem, ProgressSnapshot], None]
PhaseCallback = Callable[[DownloadItem, str], None]


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
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(item.source_url, download=True)
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
        if self.download_settings.cookie_browser:
            cookie: tuple[str, ...] = (self.download_settings.cookie_browser.value,)
            if self.download_settings.cookie_profile:
                cookie += (self.download_settings.cookie_profile,)
            options["cookiesfrombrowser"] = cookie
        if item.download_mode is DownloadMode.AUDIO:
            options["format"] = "m4a/bestaudio[ext=m4a]/bestaudio"
            options["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
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
