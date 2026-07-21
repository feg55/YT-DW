"""Bounded concurrent queue worker for responsive Qt downloads."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from openmediadl.core.downloader import Downloader, DownloadPipelineError, DownloadResult
from openmediadl.core.error_mapper import map_error
from openmediadl.core.filename_service import ensure_unique_path, sanitize_filename
from openmediadl.core.progress import ProgressSnapshot
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import DownloadMode, DownloadStatus
from openmediadl.domain.settings import DownloadSettings, MetadataSettings

LOGGER = logging.getLogger(__name__)


class DownloadQueueWorker(QThread):
    item_updated = Signal(object)
    phase_changed = Signal(str, str)
    log_message = Signal(str)
    overall_progress = Signal(int, int)
    queue_finished = Signal(bool)

    def __init__(
        self,
        items: list[DownloadItem],
        download_settings: DownloadSettings,
        metadata_settings: MetadataSettings,
        archive_path: Path,
        *,
        js_runtime_path: str | None = None,
    ) -> None:
        super().__init__()
        # Workers own their mutable queue state. The table model keeps the GUI
        # thread's instances and receives snapshots only through Qt signals.
        self._items = [deepcopy(item) for item in items]
        self._download_settings = deepcopy(download_settings)
        self._metadata_settings = deepcopy(metadata_settings)
        self._archive_path = archive_path
        self._js_runtime_path = js_runtime_path
        self._cancel_all = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._item_cancel: dict[str, threading.Event] = {}
        self._cancelled_ids: set[str] = set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
            self.log_message.emit("Queue paused; active operations may finish.")
        else:
            self._paused.clear()
            self.log_message.emit("Queue resumed.")

    def cancel_item(self, item_id: str) -> None:
        with self._lock:
            self._cancelled_ids.add(item_id)
            event = self._item_cancel.get(item_id)
            if event:
                event.set()

    def cancel_all(self) -> None:
        self._cancel_all.set()
        with self._lock:
            for event in self._item_cancel.values():
                event.set()

    def run(self) -> None:
        pending = self._prepare_items(self._items)
        total = len(pending)
        completed = 0
        futures: dict[Future[DownloadResult], DownloadItem] = {}
        max_workers = min(3, max(1, self._download_settings.maximum_concurrent_downloads))
        next_start = 0.0
        try:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="yt-dw") as pool:
                while pending or futures:
                    if self._cancel_all.is_set():
                        for item in pending:
                            self._mark_cancelled(item)
                        pending.clear()

                    with self._lock:
                        cancelled_ids = set(self._cancelled_ids)
                    if cancelled_ids and pending:
                        remaining: list[DownloadItem] = []
                        for item in pending:
                            if item.id in cancelled_ids:
                                self._mark_cancelled(item)
                                completed += 1
                                self.overall_progress.emit(completed, total)
                            else:
                                remaining.append(item)
                        pending = remaining

                    now = time.monotonic()
                    while (
                        pending
                        and not self._paused.is_set()
                        and not self._cancel_all.is_set()
                        and len(futures) < max_workers
                        and now >= next_start
                    ):
                        item = pending.pop(0)
                        with self._lock:
                            cancelled = item.id in self._cancelled_ids
                        if cancelled:
                            self._mark_cancelled(item)
                            completed += 1
                            self.overall_progress.emit(completed, total)
                            continue
                        event = threading.Event()
                        with self._lock:
                            self._item_cancel[item.id] = event
                        future = pool.submit(self._download_one, item, event)
                        futures[future] = item
                        next_start = now + self._download_settings.delay_between_items
                        now = time.monotonic()

                    if not futures:
                        if pending:
                            time.sleep(0.1)
                            continue
                        break

                    done, _ = wait(tuple(futures), timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        item = futures.pop(future)
                        with self._lock:
                            self._item_cancel.pop(item.id, None)
                        try:
                            future.result()
                        except DownloadPipelineError:
                            # _download_one already mapped, persisted, and logged it.
                            pass
                        except Exception:
                            LOGGER.exception("Unhandled download task failure for %s", item.id)
                        completed += 1
                        self.overall_progress.emit(completed, total)
        finally:
            self.queue_finished.emit(self._cancel_all.is_set())

    def _download_one(self, item: DownloadItem, cancel_event: threading.Event) -> DownloadResult:
        item.error_category = None
        item.error_message = None
        item.technical_error = None
        item.status = DownloadStatus.DOWNLOADING
        self._emit_item(item)
        downloader = Downloader(
            self._download_settings,
            self._metadata_settings,
            self._archive_path,
            cancel_event,
            self._on_progress,
            self._on_phase,
            js_runtime_path=self._js_runtime_path,
        )
        try:
            result = downloader.download(item)
            self._emit_item(item)
            if result.skipped:
                self.log_message.emit(f"Skipped: {item.original_title or item.source_url}")
            else:
                self.log_message.emit(f"Completed: {item.final_media_path}")
            if result.warning:
                self.log_message.emit(f"Warning: {result.warning}")
            return result
        except DownloadPipelineError as error:
            item.error_category = error.mapped.category.value
            item.technical_error = error.mapped.technical
            item.error_message = error.mapped.message
            item.status = (
                DownloadStatus.CANCELLED
                if error.mapped.category.value == "cancelled"
                else DownloadStatus.FAILED
            )
            if error.output_path:
                item.final_media_path = str(error.output_path)
            item.touch()
            self._emit_item(item)
            self.log_message.emit(
                f"{error.mapped.message} — {item.original_title or item.source_url}"
            )
            LOGGER.error("Download failed for %s: %s", item.source_url, error.mapped.technical)
            raise
        except Exception as error:
            mapped = map_error(error)
            item.error_category = mapped.category.value
            item.error_message = mapped.message
            item.technical_error = mapped.technical
            item.status = DownloadStatus.FAILED
            item.touch()
            self._emit_item(item)
            self.log_message.emit(f"{mapped.message} — {item.original_title or item.source_url}")
            LOGGER.exception("Download setup failed for %s", item.source_url)
            raise DownloadPipelineError(mapped) from error

    def _on_progress(self, item: DownloadItem, snapshot: ProgressSnapshot) -> None:
        item.progress_percentage = snapshot.percentage
        item.downloaded_bytes = snapshot.downloaded_bytes
        item.total_bytes = snapshot.total_bytes
        item.speed = snapshot.speed
        item.eta = float(snapshot.eta) if snapshot.eta is not None else None
        item.current_phase = snapshot.phase
        item.touch()
        self._emit_item(item)
        self.phase_changed.emit(item.id, snapshot.phase)

    def _on_phase(self, item: DownloadItem, phase: str) -> None:
        if phase not in {"Completed", "Downloading audio", "Downloading video"}:
            item.status = DownloadStatus.PROCESSING
        self.phase_changed.emit(item.id, phase)
        if phase != "Completed":
            self._emit_item(item)

    def _mark_cancelled(self, item: DownloadItem) -> None:
        item.status = DownloadStatus.CANCELLED
        item.touch()
        self._emit_item(item)

    def _emit_item(self, item: DownloadItem) -> None:
        # Queued Qt signals carry Python objects by reference. Snapshot mutable
        # queue state so a later hook cannot change an earlier GUI update.
        self.item_updated.emit(deepcopy(item))

    def _prepare_items(self, items: list[DownloadItem]) -> list[DownloadItem]:
        runnable = [
            item
            for item in items
            if item.selected
            and item.status not in {DownloadStatus.COMPLETED, DownloadStatus.SKIPPED}
        ]
        reserved: set[str] = set()
        for item in runnable:
            if item.final_media_path:
                proposed = Path(item.final_media_path)
            else:
                extension = ".m4a" if item.download_mode is DownloadMode.AUDIO else ".mp4"
                title = item.cleaned_title or item.original_title or item.video_id or "untitled"
                proposed = (
                    Path(self._download_settings.destination_directory)
                    / f"{sanitize_filename(title)}{extension}"
                )
            metadata_retry = item.error_category in {
                "metadata_writing_failed",
                "thumbnail_conversion_failed",
            }
            reuse_downloaded_audio = (
                item.download_mode is DownloadMode.AUDIO
                and proposed.is_file()
                and (metadata_retry or item.progress_percentage >= 99.9)
            )
            candidate = (
                proposed
                if reuse_downloaded_audio
                else ensure_unique_path(
                    proposed,
                    preferred_suffix=item.playlist_index or item.video_id,
                    exists=lambda path: path.exists() or str(path.resolve()).casefold() in reserved,
                )
            )
            item.final_media_path = str(candidate)
            reserved.add(str(candidate.resolve()).casefold())
        return runnable
