from __future__ import annotations

from openmediadl.core.progress import parse_progress_hook


def test_progress_hook_uses_estimated_total() -> None:
    result = parse_progress_hook(
        {
            "status": "downloading",
            "downloaded_bytes": 25,
            "total_bytes_estimate": 100,
            "speed": 12.5,
            "eta": 6,
            "info_dict": {"vcodec": "none"},
        }
    )
    assert result.percentage == 25.0
    assert result.total_bytes == 100
    assert result.phase == "Downloading audio"


def test_finished_hook_is_complete_without_total() -> None:
    result = parse_progress_hook({"status": "finished", "downloaded_bytes": 99})
    assert result.percentage == 100.0
    assert result.phase == "Processing"
