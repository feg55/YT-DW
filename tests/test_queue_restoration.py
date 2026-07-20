from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from openmediadl.core.queue_manager import QueueManager
from openmediadl.database import Database, QueueRepository
from openmediadl.domain import DownloadItem, DownloadStatus


def _item(video_id: str, status: DownloadStatus, *, analyzed: bool = True) -> DownloadItem:
    return DownloadItem.new(
        f"https://example.test/watch?v={video_id}",
        video_id=video_id if analyzed else None,
        original_title=f"Title {video_id}" if analyzed else "",
        cleaned_title=f"Title {video_id}" if analyzed else "",
        status=status,
    )


def test_startup_restores_interrupted_states_safely(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    repository = QueueRepository(Database(path))
    pending = repository.add(_item("pending", DownloadStatus.PENDING))
    ready = repository.add(_item("ready", DownloadStatus.READY))
    analyzing = repository.add(_item("analyzing", DownloadStatus.ANALYZING))
    downloading = repository.add(_item("downloading", DownloadStatus.DOWNLOADING))
    processing_without_metadata = repository.add(
        _item("processing", DownloadStatus.PROCESSING, analyzed=False)
    )
    completed = repository.add(_item("completed", DownloadStatus.COMPLETED))
    failed = repository.add(_item("failed", DownloadStatus.FAILED))

    manager = QueueManager(path)
    restored = {item.id: item for item in manager.restored_items}

    assert restored[pending.id].status is DownloadStatus.PENDING
    assert restored[ready.id].status is DownloadStatus.READY
    assert restored[analyzing.id].status is DownloadStatus.PENDING
    assert restored[downloading.id].status is DownloadStatus.READY
    assert restored[processing_without_metadata.id].status is DownloadStatus.PENDING
    assert completed.id not in restored
    assert failed.id not in restored
    assert manager.get(completed.id).status is DownloadStatus.COMPLETED  # type: ignore[union-attr]
    assert manager.get(failed.id).status is DownloadStatus.FAILED  # type: ignore[union-attr]


def test_pause_cancel_retry_and_remove_completed(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    ready = manager.add(_item("ready", DownloadStatus.READY), skip_if_archived=False)

    manager.pause()
    assert manager.claim_next() is None
    manager.resume()
    claimed = manager.claim_next()
    assert claimed is not None
    assert claimed.id == ready.id
    assert claimed.status is DownloadStatus.DOWNLOADING

    cancelled = manager.cancel(ready.id)
    assert cancelled is not None
    assert cancelled.status is DownloadStatus.CANCELLED
    assert manager.is_cancel_requested(ready.id)
    retried = manager.retry(ready.id)
    assert retried is not None
    assert retried.status is DownloadStatus.READY
    assert not manager.is_cancel_requested(ready.id)

    failed = manager.add(_item("failed", DownloadStatus.FAILED), skip_if_archived=False)
    retried_failed = manager.retry_failed()
    assert [item.id for item in retried_failed] == [failed.id]
    assert retried_failed[0].status is DownloadStatus.READY

    completed = manager.add(_item("completed", DownloadStatus.COMPLETED), skip_if_archived=False)
    assert manager.remove_completed() == 1
    assert manager.get(completed.id) is None
    assert manager.get(ready.id) is not None


def test_completed_item_is_archived_and_future_duplicate_is_skipped(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    item = manager.add(_item("archived", DownloadStatus.READY), skip_if_archived=False)

    completed = manager.mark_completed(item.id, final_media_path="track.m4a")
    assert completed is not None
    assert manager.archive.contains("archived")

    manager.remove_completed()
    second = manager.add(_item("archived", DownloadStatus.READY))
    assert second.status is DownloadStatus.SKIPPED
    assert len(manager.history.list()) == 2


def test_archive_preference_is_rechecked_at_queue_start(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    completed = manager.add(_item("archived-later", DownloadStatus.READY), skip_if_archived=False)
    manager.mark_completed(completed.id, final_media_path="track.m4a")
    manager.remove_completed()
    queued = manager.add(
        _item("archived-later", DownloadStatus.READY),
        skip_if_archived=False,
    )

    skipped = manager.prepare_for_download(queued.id, skip_archived=True)
    assert skipped is not None and skipped.status is DownloadStatus.SKIPPED
    assert skipped.error_message == "Already downloaded"

    restored = manager.prepare_for_download(queued.id, skip_archived=False)
    assert restored is not None and restored.status is DownloadStatus.READY
    assert restored.error_message is None


def test_stale_active_snapshot_cannot_resurrect_cancelled_item(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    item = manager.add(_item("cancel-race", DownloadStatus.READY), skip_if_archived=False)
    stale = deepcopy(item)
    manager.cancel(item.id)
    stale.status = DownloadStatus.PROCESSING

    persisted = manager.save(stale)

    assert persisted.status is DownloadStatus.CANCELLED
    assert manager.get(item.id).status is DownloadStatus.CANCELLED  # type: ignore[union-attr]


def test_terminal_worker_snapshot_replaces_cancelled_state(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    item = manager.add(_item("cancel-completed", DownloadStatus.READY), skip_if_archived=False)
    cancelled = manager.cancel(item.id)
    assert cancelled is not None

    completed = deepcopy(cancelled)
    completed.status = DownloadStatus.COMPLETED
    completed.progress_percentage = 100.0
    completed.current_phase = "Completed"

    persisted = manager.save(completed)

    assert persisted.status is DownloadStatus.COMPLETED
    assert persisted.progress_percentage == 100.0
    restored = manager.get(item.id)
    assert restored is not None
    assert restored.status is DownloadStatus.COMPLETED
    assert restored.progress_percentage == 100.0


def test_queue_start_resets_persisted_cancel_request(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    item = manager.add(_item("cancel-retry", DownloadStatus.READY), skip_if_archived=False)
    manager.cancel(item.id)
    assert manager.is_cancel_requested(item.id)

    prepared = manager.prepare_for_download(item.id, skip_archived=False)

    assert prepared is not None
    assert prepared.status is DownloadStatus.READY
    assert not manager.is_cancel_requested(item.id)


def test_retry_retains_metadata_failure_context_until_worker_start(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    item = manager.add(_item("metadata-retry", DownloadStatus.READY), skip_if_archived=False)
    manager.mark_failed(
        item.id,
        error_category="metadata_writing_failed",
        error_message="Metadata failed",
        technical_error="fixture error",
    )

    retried = manager.retry(item.id)

    assert retried is not None and retried.status is DownloadStatus.READY
    assert retried.error_category == "metadata_writing_failed"
    assert retried.technical_error == "fixture error"


def test_repeated_terminal_worker_updates_are_idempotent(tmp_path: Path) -> None:
    manager = QueueManager(tmp_path / "queue.sqlite3")
    completed = manager.add(_item("complete-once", DownloadStatus.READY), skip_if_archived=False)
    manager.mark_completed(completed.id, final_media_path="track.m4a")
    manager.mark_completed(completed.id, final_media_path="track.m4a")

    failed = manager.add(_item("fail-once", DownloadStatus.READY), skip_if_archived=False)
    first_failure = manager.mark_failed(failed.id, error_message="Network timeout")
    assert first_failure is not None
    failed.status = DownloadStatus.FAILED
    manager.save(failed)  # Simulate a delayed worker snapshot with a stale retry count.
    second_failure = manager.mark_failed(failed.id, error_message="Network timeout")

    assert second_failure is not None
    assert second_failure.retry_count == 1
    assert len(manager.history.list()) == 2


def test_progress_is_throttled_but_force_flushes_latest_value(tmp_path: Path) -> None:
    manager = QueueManager(
        tmp_path / "queue.sqlite3",
        progress_persist_interval=60.0,
    )
    item = manager.add(_item("progress", DownloadStatus.READY), skip_if_archived=False)
    manager.update_progress(item.id, progress_percentage=10.0)
    manager.update_progress(item.id, progress_percentage=30.0)

    assert manager.repository.get(item.id).progress_percentage == 10.0  # type: ignore[union-attr]
    manager.flush_progress()
    assert manager.repository.get(item.id).progress_percentage == 30.0  # type: ignore[union-attr]
