from __future__ import annotations

import os
from pathlib import Path

import pytest

from openmediadl.core.ffmpeg_service import FFmpegService


def _fake_tools(directory: Path) -> None:
    directory.mkdir(parents=True)
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("ffmpeg", "ffprobe"):
        (directory / f"{name}{suffix}").write_bytes(b"test executable")


def _mock_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        FFmpegService,
        "_version",
        staticmethod(lambda executable: f"{executable.stem} test-version"),
    )


def _normalized(value: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(value))))


def test_configured_installation_is_prepended_to_isolated_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured tools"
    bundled = tmp_path / "bundled tools"
    original = tmp_path / "original path"
    _fake_tools(configured)
    _fake_tools(bundled)
    original.mkdir()
    _mock_versions(monkeypatch)
    monkeypatch.setenv("PATH", str(original))

    installation = FFmpegService(bundled).detect(configured)

    assert installation.available
    assert installation.directory == configured
    entries = os.environ["PATH"].split(os.pathsep)
    assert _normalized(entries[0]) == _normalized(configured)
    assert _normalized(entries[1]) == _normalized(original)
    assert all(_normalized(entry) != _normalized(bundled) for entry in entries)


def test_bundled_installation_is_added_to_path_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bundled tools"
    original = tmp_path / "original path"
    _fake_tools(bundled)
    original.mkdir()
    _mock_versions(monkeypatch)
    monkeypatch.setenv("PATH", str(original))
    service = FFmpegService(bundled)

    first = service.detect()
    second = service.detect()
    service.ensure_on_path(bundled / ".")

    assert first.available and second.available
    entries = os.environ["PATH"].split(os.pathsep)
    bundled_key = _normalized(bundled)
    assert [_normalized(entry) for entry in entries].count(bundled_key) == 1
    assert _normalized(entries[0]) == bundled_key
    assert _normalized(entries[1]) == _normalized(original)
