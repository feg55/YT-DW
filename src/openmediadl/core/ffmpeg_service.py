"""Portable FFmpeg/FFprobe discovery and validation."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

_PATH_UPDATE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class FFmpegInstallation:
    ffmpeg: Path | None
    ffprobe: Path | None
    ffmpeg_version: str = ""
    ffprobe_version: str = ""

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None

    @property
    def directory(self) -> Path | None:
        return self.ffmpeg.parent if self.ffmpeg else None


class FFmpegService:
    def __init__(self, bundled_tools_dir: Path | None = None) -> None:
        self._bundled_tools_dir = bundled_tools_dir

    def detect(self, configured_directory: str | Path | None = None) -> FFmpegInstallation:
        directories: list[Path] = []
        if configured_directory:
            directories.append(Path(configured_directory).expanduser())
        if self._bundled_tools_dir:
            directories.append(self._bundled_tools_dir)

        suffix = ".exe" if os.name == "nt" else ""
        for directory in directories:
            installation = self._from_paths(
                directory / f"ffmpeg{suffix}", directory / f"ffprobe{suffix}"
            )
            if installation.available:
                self.ensure_on_path(directory)
                return installation

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        return self._from_paths(
            Path(ffmpeg) if ffmpeg else None, Path(ffprobe) if ffprobe else None
        )

    @staticmethod
    def ensure_on_path(directory: str | Path) -> Path:
        """Prepend a tool directory to process PATH once and return its absolute path."""

        resolved = Path(directory).expanduser().resolve()
        target_key = _path_key(resolved)
        with _PATH_UPDATE_LOCK:
            current = os.environ.get("PATH", "")
            if any(
                entry and _path_key(entry) == target_key
                for entry in current.split(os.pathsep)
            ):
                return resolved
            os.environ["PATH"] = (
                f"{resolved}{os.pathsep}{current}" if current else str(resolved)
            )
        return resolved

    def _from_paths(self, ffmpeg: Path | None, ffprobe: Path | None) -> FFmpegInstallation:
        ffmpeg_version = self._version(ffmpeg) if ffmpeg else ""
        ffprobe_version = self._version(ffprobe) if ffprobe else ""
        return FFmpegInstallation(
            ffmpeg=ffmpeg if ffmpeg_version else None,
            ffprobe=ffprobe if ffprobe_version else None,
            ffmpeg_version=ffmpeg_version,
            ffprobe_version=ffprobe_version,
        )

    @staticmethod
    def _version(executable: Path) -> str:
        if not executable.is_file():
            return ""
        try:
            result = subprocess.run(
                [str(executable), "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return (result.stdout or result.stderr).splitlines()[0].strip()


def _path_key(value: str | Path) -> str:
    """Return a platform-aware identity for comparing PATH entries."""

    text = os.path.expandvars(os.fspath(value)).strip('"')
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(text))))
