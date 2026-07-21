from __future__ import annotations

from pathlib import Path
from typing import Any

from openmediadl.core.runtime_tools import RuntimeToolsStatus
from openmediadl.workers.runtime_tools_worker import RuntimeToolsWorker


def test_worker_forwards_progress_and_result(tmp_path: Path) -> None:
    expected = RuntimeToolsStatus(
        ffmpeg=tmp_path / "ffmpeg.exe",
        ffprobe=tmp_path / "ffprobe.exe",
        deno=tmp_path / "deno.exe",
    )

    class Service:
        @staticmethod
        def provision(
            _manual: str | None,
            *,
            progress: Any,
            is_cancelled: Any,
        ) -> RuntimeToolsStatus:
            assert not is_cancelled()
            progress("ffmpeg", 50, 100)
            return expected

    worker = RuntimeToolsWorker(Service(), None)  # type: ignore[arg-type]
    progress_values: list[tuple[str, int, int]] = []
    results: list[object] = []
    worker.progress_changed.connect(
        lambda tool, downloaded, total: progress_values.append((tool, downloaded, total))
    )
    worker.result_ready.connect(results.append)

    worker.run()

    assert progress_values == [("ffmpeg", 50, 100)]
    assert results == [expected]


def test_worker_reports_unexpected_setup_failure() -> None:
    class Service:
        @staticmethod
        def provision(*_args: object, **_kwargs: object) -> RuntimeToolsStatus:
            raise OSError("disk unavailable")

    worker = RuntimeToolsWorker(Service(), None)  # type: ignore[arg-type]
    failures: list[str] = []
    worker.setup_failed.connect(failures.append)

    worker.run()

    assert failures == ["disk unavailable"]
