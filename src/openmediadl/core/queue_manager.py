"""Thread-safe orchestration for the persistent download queue."""

from __future__ import annotations

import builtins
import threading
import time
from collections.abc import Iterable
from enum import Enum
from pathlib import Path

from openmediadl.database.connection import Database
from openmediadl.database.repositories import (
    ArchiveRepository,
    ClearedDownloadState,
    HistoryRepository,
    QueueRepository,
    SettingsRepository,
    clear_download_state,
)
from openmediadl.domain.download_item import DownloadItem
from openmediadl.domain.download_status import ACTIVE_STATUSES, DownloadStatus


def _text_or_none(value: str | Enum | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


class QueueBusyError(RuntimeError):
    """Raised when destructive queue maintenance is requested during active work."""


class QueueManager:
    """High-level queue operations without any Qt dependencies."""

    PAUSED_SETTING_KEY = "queue.paused"

    def __init__(
        self,
        database: Database | QueueRepository | str | Path,
        *,
        progress_persist_interval: float = 1.0,
        restore_on_startup: bool = True,
    ) -> None:
        if isinstance(database, QueueRepository):
            self.repository = database
            self.database = database.database
        else:
            self.database = database if isinstance(database, Database) else Database(database)
            self.repository = QueueRepository(self.database)
        self.history = HistoryRepository(self.database)
        self.archive = ArchiveRepository(self.database)
        self.settings = SettingsRepository(self.database)
        self.progress_persist_interval = max(0.0, float(progress_persist_interval))
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._progress_cache: dict[str, DownloadItem] = {}
        self._last_progress_write: dict[str, float] = {}
        self._recorded_terminal: set[tuple[str, DownloadStatus, int]] = set()
        self._paused = bool(self.settings.get(self.PAUSED_SETTING_KEY, False))
        self.restored_items = self.restore_unfinished() if restore_on_startup else []

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def pause(self) -> None:
        """Stop workers from claiming another item; active operations are untouched."""

        with self._lock:
            self._paused = True
            self.settings.set(self.PAUSED_SETTING_KEY, True)

    pause_queue = pause

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self.settings.set(self.PAUSED_SETTING_KEY, False)

    resume_queue = resume

    def add(self, item: DownloadItem, *, skip_if_archived: bool = True) -> DownloadItem:
        """Add one item, converting a known archive match into a skipped row."""

        if skip_if_archived and self.archive.contains(item.video_id, item.source_url):
            item.status = DownloadStatus.SKIPPED
            item.current_phase = "Already downloaded"
        persisted = self.repository.add(item)
        if persisted.id == item.id and persisted.status is DownloadStatus.SKIPPED:
            self.history.add(persisted)
        return persisted

    enqueue = add

    def add_many(
        self,
        items: Iterable[DownloadItem],
        *,
        skip_if_archived: bool = True,
    ) -> list[DownloadItem]:
        return [self.add(item, skip_if_archived=skip_if_archived) for item in items]

    enqueue_many = add_many

    def get(self, item_id: str) -> DownloadItem | None:
        return self.repository.get(item_id)

    def list(
        self,
        statuses: Iterable[DownloadStatus | str] | None = None,
        *,
        selected_only: bool = False,
    ) -> list[DownloadItem]:
        return self.repository.list(statuses, selected_only=selected_only)

    list_items = list

    def save(self, item: DownloadItem) -> DownloadItem:
        return self.repository.upsert(item)

    update = save

    def claim_next(self) -> DownloadItem | None:
        """Claim a ready item unless pause has been requested."""

        with self._lock:
            if self._paused:
                return None
            item = self.repository.claim_next_ready()
            if item is not None:
                self._cancel_events[item.id] = threading.Event()
                self._progress_cache[item.id] = item
            return item

    claim_next_ready = claim_next

    def mark_ready(self, item_id: str) -> DownloadItem | None:
        item = self.repository.set_status(item_id, DownloadStatus.READY)
        if item is not None:
            self.clear_cancel_request(item_id)
        return item

    def mark_processing(self, item_id: str, phase: str = "Processing") -> DownloadItem | None:
        return self.repository.set_status(
            item_id,
            DownloadStatus.PROCESSING,
            current_phase=phase,
        )

    def mark_completed(
        self,
        item_id: str,
        *,
        final_media_path: str | None = None,
    ) -> DownloadItem | None:
        with self._lock:
            item = self._progress_cache.get(item_id) or self.repository.get(item_id)
            if item is None:
                return None
            terminal_key = (item_id, DownloadStatus.COMPLETED, item.retry_count)
            if item.status is DownloadStatus.COMPLETED and terminal_key in self._recorded_terminal:
                return item
            item.status = DownloadStatus.COMPLETED
            item.progress_percentage = 100.0
            item.speed = None
            item.eta = None
            item.current_phase = "Completed"
            if final_media_path is not None:
                item.final_media_path = final_media_path
            item.clear_error()
            persisted = self.repository.upsert(item)
            self.history.add(persisted)
            self.archive.add(persisted)
            self._recorded_terminal.add(terminal_key)
            self._forget_runtime_state(item_id)
            return persisted

    complete = mark_completed

    def mark_failed(
        self,
        item_id: str,
        *,
        error_category: str | Enum | None = None,
        error_message: str | None = None,
        technical_error: str | None = None,
    ) -> DownloadItem | None:
        with self._lock:
            item = self._progress_cache.get(item_id) or self.repository.get(item_id)
            if item is None:
                return None
            current_key = (item_id, DownloadStatus.FAILED, item.retry_count)
            if item.status is DownloadStatus.FAILED and current_key in self._recorded_terminal:
                return item
            item.status = DownloadStatus.FAILED
            item.retry_count += 1
            item.error_category = _text_or_none(error_category)
            item.error_message = error_message
            item.technical_error = technical_error
            item.speed = None
            item.eta = None
            item.current_phase = "Failed"
            item.touch()
            persisted = self.repository.upsert(item)
            self.history.add(persisted)
            self._recorded_terminal.add((item_id, DownloadStatus.FAILED, persisted.retry_count))
            self._forget_runtime_state(item_id)
            return persisted

    fail = mark_failed

    def mark_skipped(self, item_id: str, message: str = "Skipped") -> DownloadItem | None:
        with self._lock:
            current = self.repository.get(item_id)
            if current is None:
                return None
            if current.status is DownloadStatus.SKIPPED:
                return current
            terminal_key = (item_id, DownloadStatus.SKIPPED, current.retry_count)
            if terminal_key in self._recorded_terminal:
                return current
            item = self.repository.set_status(
                item_id,
                DownloadStatus.SKIPPED,
                error_message=message,
                current_phase="Skipped",
            )
            if item is not None:
                self.history.add(item)
                self._recorded_terminal.add(terminal_key)
            self._forget_runtime_state(item_id)
            return item

    def prepare_for_download(
        self,
        item_id: str,
        *,
        skip_archived: bool,
    ) -> DownloadItem | None:
        """Apply the current archive preference immediately before queue start."""

        with self._lock:
            item = self.repository.get(item_id)
            if item is None or item.status is DownloadStatus.COMPLETED:
                return item
            if item.status is DownloadStatus.CANCELLED:
                # Starting a selected cancelled row is an explicit retry. Reset
                # the persisted cancel request before its worker starts so fresh
                # snapshots belong to the new attempt.
                item = self.retry(item_id)
                if item is None:
                    return None
            archived = self.archive.contains(item.video_id, item.source_url)
            if skip_archived and archived:
                return self.mark_skipped(item_id, "Already downloaded")
            if (
                not skip_archived
                and item.status is DownloadStatus.SKIPPED
                and item.error_message == "Already downloaded"
            ):
                item.status = (
                    DownloadStatus.READY if item.has_analyzed_metadata else DownloadStatus.PENDING
                )
                item.clear_error()
                item.current_phase = ""
                self._clear_terminal_records(item_id)
                return self.repository.upsert(item)
            return item

    def cancel(self, item_id: str) -> DownloadItem | None:
        """Request worker cancellation and persist a truthful cancelled state."""

        with self._lock:
            current = self.repository.get(item_id)
            if current is None:
                return None
            event = self._cancel_events.setdefault(item_id, threading.Event())
            event.set()
            if current.status in {
                DownloadStatus.COMPLETED,
                DownloadStatus.SKIPPED,
                DownloadStatus.CANCELLED,
            }:
                return current
            cancelled = self.repository.set_status(
                item_id,
                DownloadStatus.CANCELLED,
                current_phase="Cancelled",
            )
            if cancelled is not None:
                self.history.add(cancelled)
                self._recorded_terminal.add(
                    (item_id, DownloadStatus.CANCELLED, cancelled.retry_count)
                )
            self._progress_cache.pop(item_id, None)
            self._last_progress_write.pop(item_id, None)
            return cancelled

    cancel_item = cancel

    def cancel_all_pending(self) -> int:
        candidates = self.repository.list((DownloadStatus.PENDING, DownloadStatus.READY))
        changed = self.repository.cancel_pending()
        for candidate in candidates:
            restored = self.repository.get(candidate.id)
            if restored is not None and restored.status is DownloadStatus.CANCELLED:
                self._cancel_events.setdefault(candidate.id, threading.Event()).set()
                self.history.add(restored)
                self._recorded_terminal.add(
                    (candidate.id, DownloadStatus.CANCELLED, restored.retry_count)
                )
        return changed

    def is_cancel_requested(self, item_id: str) -> bool:
        with self._lock:
            event = self._cancel_events.get(item_id)
            if event is not None and event.is_set():
                return True
        item = self.repository.get(item_id)
        return item is not None and item.status is DownloadStatus.CANCELLED

    def cancellation_event(self, item_id: str) -> threading.Event:
        with self._lock:
            return self._cancel_events.setdefault(item_id, threading.Event())

    def clear_cancel_request(self, item_id: str) -> None:
        with self._lock:
            self._cancel_events.pop(item_id, None)

    def retry(self, item_id: str) -> DownloadItem | None:
        with self._lock:
            item = self.repository.get(item_id)
            if item is None or item.status not in {
                DownloadStatus.FAILED,
                DownloadStatus.CANCELLED,
            }:
                return item
            item.status = (
                DownloadStatus.READY if item.has_analyzed_metadata else DownloadStatus.PENDING
            )
            item.current_phase = ""
            item.speed = None
            item.eta = None
            item.touch()
            self._clear_terminal_records(item_id)
            self.clear_cancel_request(item_id)
            return self.repository.upsert(item)

    retry_item = retry

    def retry_failed(self) -> builtins.list[DownloadItem]:
        with self._lock:
            items = self.repository.retry_failed()
            for item in items:
                self._clear_terminal_records(item.id)
                self.clear_cancel_request(item.id)
            return items

    def remove(self, item_id: str) -> bool:
        with self._lock:
            current = self.repository.get(item_id)
            if current is not None and current.status.is_active:
                self.cancellation_event(item_id).set()
            removed = self.repository.delete(item_id)
            self._forget_runtime_state(item_id)
            self._clear_terminal_records(item_id)
            return removed

    remove_item = remove

    def remove_completed(self) -> int:
        with self._lock:
            completed_ids = [item.id for item in self.repository.list((DownloadStatus.COMPLETED,))]
            removed = self.repository.delete_completed()
            for item_id in completed_ids:
                self._forget_runtime_state(item_id)
                self._clear_terminal_records(item_id)
            return removed

    def clear_all(self, archive_path: str | Path | None = None) -> ClearedDownloadState:
        """Clear queue, history and archives without deleting media or settings."""

        with self._lock:
            if self._progress_cache or self.repository.count(ACTIVE_STATUSES):
                raise QueueBusyError("Cannot clear download state while tasks are active")

            archive = Path(archive_path) if archive_path is not None else None
            archive_contents: bytes | None = None
            archive_existed = bool(archive and archive.exists())
            if archive_existed and archive is not None:
                # Remove the yt-dlp text archive before changing SQLite. If the
                # database transaction fails, restore its exact contents.
                archive_contents = archive.read_bytes()
                archive.unlink()
            try:
                cleared = clear_download_state(self.database)
            except Exception:
                if archive_existed and archive is not None and archive_contents is not None:
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    archive.write_bytes(archive_contents)
                raise

            self._cancel_events.clear()
            self._progress_cache.clear()
            self._last_progress_write.clear()
            self._recorded_terminal.clear()
            return cleared

    def restore_unfinished(self) -> builtins.list[DownloadItem]:
        """Map interrupted analyzing/downloading/processing rows to safe states."""

        with self._lock:
            items = self.repository.restore_unfinished()
            for item in items:
                self._cancel_events.pop(item.id, None)
                self._progress_cache.pop(item.id, None)
                self._last_progress_write.pop(item.id, None)
            return items

    restore = restore_unfinished

    def update_progress(
        self,
        item_id: str,
        *,
        progress_percentage: float | None = None,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        speed: float | None = None,
        eta: float | None = None,
        current_phase: str | None = None,
        status: DownloadStatus | None = None,
        force: bool = False,
    ) -> DownloadItem | None:
        """Update cached progress and persist no more often than configured."""

        with self._lock:
            item = self._progress_cache.get(item_id) or self.repository.get(item_id)
            if item is None:
                return None
            if self.is_cancel_requested(item_id):
                return item
            if progress_percentage is not None:
                item.progress = progress_percentage
            if downloaded_bytes is not None:
                item.downloaded_bytes = max(0, int(downloaded_bytes))
            if total_bytes is not None:
                item.total_bytes = max(0, int(total_bytes))
            item.speed = speed
            item.eta = eta
            if current_phase is not None:
                item.current_phase = current_phase
            if status is not None:
                item.status = status
            item.touch()
            self._progress_cache[item_id] = item
            self.persist_progress(item, force=force)
            return item

    def persist_progress(self, item: DownloadItem, *, force: bool = False) -> bool:
        """Persist a progress snapshot if its throttle interval has elapsed."""

        with self._lock:
            now = time.monotonic()
            last = self._last_progress_write.get(item.id, float("-inf"))
            if not force and now - last < self.progress_persist_interval:
                self._progress_cache[item.id] = item
                return False
            saved = self.repository.update_progress(item)
            if saved:
                self._last_progress_write[item.id] = now
                self._progress_cache[item.id] = item
            return saved

    def flush_progress(self) -> None:
        with self._lock:
            for item in tuple(self._progress_cache.values()):
                self.persist_progress(item, force=True)

    def _forget_runtime_state(self, item_id: str) -> None:
        self._progress_cache.pop(item_id, None)
        self._last_progress_write.pop(item_id, None)
        self._cancel_events.pop(item_id, None)

    def _clear_terminal_records(self, item_id: str) -> None:
        self._recorded_terminal = {key for key in self._recorded_terminal if key[0] != item_id}
