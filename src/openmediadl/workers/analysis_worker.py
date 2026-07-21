"""Qt worker for non-blocking, batched URL analysis."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from openmediadl.core.analyzer import AnalysisOptions, AnalyzedEntry, Analyzer
from openmediadl.core.error_mapper import map_error

LOGGER = logging.getLogger(__name__)

ThumbnailLoader = Callable[[str, str], Path | None]


class AnalysisWorker(QThread):
    batch_ready = Signal(object)
    thumbnail_ready = Signal(str, str)
    item_error = Signal(str, str, str)
    phase_changed = Signal(str)
    analysis_finished = Signal(bool, int)

    def __init__(
        self,
        urls: list[str],
        options: AnalysisOptions,
        thumbnail_loader: ThumbnailLoader | None = None,
        *,
        batch_size: int = 20,
    ) -> None:
        super().__init__()
        self._urls = urls
        self._options = options
        self._thumbnail_loader = thumbnail_loader
        self._batch_size = batch_size
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        batch: list[tuple[AnalyzedEntry, str]] = []
        count = 0
        last_emit = time.monotonic()
        self.phase_changed.emit("Analyzing")
        thumbnail_futures: set[Future[Path | None]] = set()
        thumbnail_futures_lock = threading.Lock()
        thumbnail_slots = threading.BoundedSemaphore(12)
        thumbnail_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yt-dw-thumbnails")

        def thumbnail_finished(future: Future[Path | None], source_url: str) -> None:
            try:
                if future.cancelled():
                    return
                loaded = future.result()
            except Exception:
                LOGGER.exception("Could not cache thumbnail for %s", source_url)
            else:
                if loaded:
                    self.thumbnail_ready.emit(source_url, str(loaded))
            finally:
                with thumbnail_futures_lock:
                    thumbnail_futures.discard(future)
                thumbnail_slots.release()

        def handle_entry(entry: AnalyzedEntry) -> None:
            nonlocal batch, count, last_emit
            if self._cancel_event.is_set():
                return
            batch.append((entry, ""))
            count += 1
            now = time.monotonic()
            if len(batch) >= self._batch_size or now - last_emit >= 0.5:
                self.batch_ready.emit(batch)
                batch = []
                last_emit = now
            self.phase_changed.emit(f"Analyzing — {count} found")
            if self._thumbnail_loader and entry.thumbnail_url:
                while not self._cancel_event.is_set():
                    if thumbnail_slots.acquire(timeout=0.1):
                        break
                else:
                    return
                cache_key = (
                    f"{entry.extractor.casefold()}:{entry.video_id}"
                    if entry.extractor and entry.video_id
                    else entry.source_url
                )
                future = thumbnail_pool.submit(
                    self._thumbnail_loader, entry.thumbnail_url, cache_key
                )
                with thumbnail_futures_lock:
                    thumbnail_futures.add(future)
                future.add_done_callback(partial(thumbnail_finished, source_url=entry.source_url))

        analyzer = Analyzer(self._options, self._cancel_event, handle_entry)
        try:
            for source_url in self._urls:
                if self._cancel_event.is_set():
                    break
                try:
                    for entry in analyzer.analyze([source_url]):
                        handle_entry(entry)
                except Exception as error:
                    LOGGER.exception("Analysis failed for %s", source_url)
                    mapped = map_error(error)
                    self.item_error.emit(
                        mapped.category.value,
                        mapped.message,
                        f"{source_url}: {mapped.technical}",
                    )
        except Exception as error:
            LOGGER.exception("Analysis worker failed")
            mapped = map_error(error)
            self.item_error.emit(mapped.category.value, mapped.message, mapped.technical)
        finally:
            if batch:
                self.batch_ready.emit(batch)
            if self._cancel_event.is_set():
                with thumbnail_futures_lock:
                    pending_thumbnails = tuple(thumbnail_futures)
                for future in pending_thumbnails:
                    future.cancel()
            else:
                with thumbnail_futures_lock:
                    has_pending_thumbnails = bool(thumbnail_futures)
                if has_pending_thumbnails:
                    self.phase_changed.emit("Finishing thumbnails")
            thumbnail_pool.shutdown(
                wait=True,
                cancel_futures=self._cancel_event.is_set(),
            )
            self.analysis_finished.emit(self._cancel_event.is_set(), count)
