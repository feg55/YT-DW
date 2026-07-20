"""Pure yt-dlp hook parsing, kept independent from Qt."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    status: str
    phase: str
    percentage: float
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: int | None
    filename: str | None


def parse_progress_hook(data: Mapping[str, Any]) -> ProgressSnapshot:
    """Normalize documented yt-dlp progress-hook fields."""

    status = str(data.get("status") or "downloading")
    downloaded = _as_int(data.get("downloaded_bytes"), default=0)
    total = _optional_int(data.get("total_bytes")) or _optional_int(
        data.get("total_bytes_estimate")
    )
    percentage = (
        min(100.0, max(0.0, downloaded * 100.0 / total))
        if total
        else (100.0 if status == "finished" else 0.0)
    )
    info = data.get("info_dict")
    kind = str(info.get("vcodec") if isinstance(info, Mapping) else "")
    phase = "Downloading audio" if kind == "none" else "Downloading media"
    if status == "finished":
        phase = "Processing"
    elif status == "error":
        phase = "Failed"
    return ProgressSnapshot(
        status=status,
        phase=phase,
        percentage=percentage,
        downloaded_bytes=downloaded,
        total_bytes=total,
        speed=_optional_float(data.get("speed")),
        eta=_optional_int(data.get("eta")),
        filename=str(data["filename"]) if data.get("filename") else None,
    )


def _as_int(value: object, *, default: int) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return default


def _optional_int(value: object) -> int | None:
    try:
        return int(cast(Any, value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
