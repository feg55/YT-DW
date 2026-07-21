"""Verified per-user provisioning for external runtime tools.

The frozen application already contains Python, Qt, yt-dlp, and yt-dlp-ejs.
FFmpeg/FFprobe and a JavaScript engine remain native executables, so Windows
builds install pinned portable releases into the current user's app-data
directory without requiring elevation or changing the system configuration.
"""

from __future__ import annotations

import errno
import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

RuntimeToolName = Literal["ffmpeg", "deno"]
ToolSource = Literal["manual", "managed", "bundled", "path"]
ProgressCallback = Callable[[RuntimeToolName, int, int], None]
CancelCheck = Callable[[], bool]

_CHUNK_SIZE = 1024 * 1024
_MAX_ARCHIVE_FILES = 10_000
_MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024
_MAX_MEMBER_SIZE = 512 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 200
_LOCK_TIMEOUT_SECONDS = 15 * 60
_DENO_MIN_VERSION = (2, 3, 0)
_ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


@dataclass(frozen=True, slots=True)
class ToolArchive:
    name: RuntimeToolName
    version: str
    url: str
    sha256: str
    size: int
    members: tuple[tuple[str, str], ...]


FFMPEG_ARCHIVE = ToolArchive(
    name="ffmpeg",
    version="8.1.2",
    url=(
        "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/"
        "ffmpeg-8.1.2-essentials_build.zip"
    ),
    sha256="db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
    size=109_728_040,
    members=(
        ("ffmpeg-8.1.2-essentials_build/bin/ffmpeg.exe", "ffmpeg.exe"),
        ("ffmpeg-8.1.2-essentials_build/bin/ffprobe.exe", "ffprobe.exe"),
    ),
)

DENO_ARCHIVE = ToolArchive(
    name="deno",
    version="2.9.3",
    url=(
        "https://github.com/denoland/deno/releases/download/v2.9.3/deno-x86_64-pc-windows-msvc.zip"
    ),
    sha256="60343461ac5fe3a31f4ef12667f2946bb852e20655c8610aeb7e751e87f7df3a",
    size=42_726_295,
    members=(("deno.exe", "deno.exe"),),
)


class RuntimeToolError(RuntimeError):
    """Base class for safe, user-reportable tool provisioning failures."""


class RuntimeToolCancelled(RuntimeToolError):
    """Raised when application shutdown cancels provisioning."""


class RuntimeToolIntegrityError(RuntimeToolError):
    """Raised when a downloaded archive does not match its pinned manifest."""


class _IncompleteDownloadError(RuntimeToolError):
    """Retryable error raised when a trusted response ends prematurely."""


@dataclass(frozen=True, slots=True)
class RuntimeToolsStatus:
    ffmpeg: Path | None
    ffprobe: Path | None
    deno: Path | None
    ffmpeg_version: str = ""
    ffprobe_version: str = ""
    deno_version: str = ""
    ffmpeg_source: ToolSource | None = None
    deno_source: ToolSource | None = None
    errors: tuple[str, ...] = ()

    @property
    def ffmpeg_available(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None

    @property
    def deno_available(self) -> bool:
        return self.deno is not None

    @property
    def available(self) -> bool:
        return self.ffmpeg_available and self.deno_available


class RuntimeToolsService:
    """Discover and provision a complete yt-dlp native toolchain."""

    def __init__(
        self,
        install_root: Path,
        bundled_tools_dir: Path | None = None,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        platform_name: str | None = None,
    ) -> None:
        self.install_root = Path(install_root)
        self.bundled_tools_dir = Path(bundled_tools_dir) if bundled_tools_dir else None
        self._opener = opener
        self._sleeper = sleeper
        self._platform = platform_name or sys.platform

    @property
    def managed_ffmpeg_directory(self) -> Path:
        return self._component_directory(FFMPEG_ARCHIVE)

    @property
    def managed_deno_executable(self) -> Path:
        return self._component_directory(DENO_ARCHIVE) / "deno.exe"

    def detect(
        self,
        manual_ffmpeg_directory: str | Path | None = None,
    ) -> RuntimeToolsStatus:
        ffmpeg: Path | None = None
        ffprobe: Path | None = None
        ffmpeg_version = ""
        ffprobe_version = ""
        ffmpeg_source: ToolSource | None = None

        candidates: list[tuple[ToolSource, Path]] = []
        if manual_ffmpeg_directory:
            candidates.append(("manual", Path(manual_ffmpeg_directory).expanduser()))
        if self._platform == "win32":
            candidates.append(("managed", self.managed_ffmpeg_directory))
        if self.bundled_tools_dir is not None:
            candidates.append(("bundled", self.bundled_tools_dir))
        for source, directory in candidates:
            detected = self._validate_ffmpeg_directory(directory)
            if detected is not None:
                ffmpeg, ffprobe, ffmpeg_version, ffprobe_version = detected
                ffmpeg_source = source
                break
        if ffmpeg is None or ffprobe is None:
            ffmpeg_path = shutil.which("ffmpeg")
            ffprobe_path = shutil.which("ffprobe")
            if ffmpeg_path and ffprobe_path:
                detected = self._validate_ffmpeg_paths(Path(ffmpeg_path), Path(ffprobe_path))
                if detected is not None:
                    ffmpeg, ffprobe, ffmpeg_version, ffprobe_version = detected
                    ffmpeg_source = "path"

        deno: Path | None = None
        deno_version = ""
        deno_source: ToolSource | None = None
        deno_candidates: list[tuple[ToolSource, Path]] = []
        if self._platform == "win32":
            deno_candidates.append(("managed", self.managed_deno_executable))
        if self.bundled_tools_dir is not None:
            deno_candidates.append(
                (
                    "bundled",
                    self.bundled_tools_dir / ("deno.exe" if self._platform == "win32" else "deno"),
                )
            )
        deno_path = shutil.which("deno")
        if deno_path:
            deno_candidates.append(("path", Path(deno_path)))
        for source, executable in deno_candidates:
            version = self._validate_deno(executable)
            if version:
                deno = executable.resolve()
                deno_version = version
                deno_source = source
                break

        return RuntimeToolsStatus(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            deno=deno,
            ffmpeg_version=ffmpeg_version,
            ffprobe_version=ffprobe_version,
            deno_version=deno_version,
            ffmpeg_source=ffmpeg_source,
            deno_source=deno_source,
        )

    def provision(
        self,
        manual_ffmpeg_directory: str | Path | None = None,
        *,
        progress: ProgressCallback | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> RuntimeToolsStatus:
        cancel = is_cancelled or (lambda: False)
        self._raise_if_cancelled(cancel)
        status = self.detect(manual_ffmpeg_directory)
        if status.available:
            return status
        if self._platform != "win32":
            missing_errors: list[str] = []
            if not status.ffmpeg_available:
                missing_errors.append("FFmpeg and FFprobe were not found on this system")
            if not status.deno_available:
                missing_errors.append("Deno 2.3 or newer was not found on this system")
            return replace(status, errors=tuple(missing_errors))

        self.install_root.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        with self._installation_lock(cancel):
            self._cleanup_stale_installation_files()
            status = self.detect(manual_ffmpeg_directory)
            if not status.ffmpeg_available:
                try:
                    self._install_archive(FFMPEG_ARCHIVE, progress, cancel)
                except RuntimeToolCancelled:
                    raise
                except Exception as error:
                    LOGGER.exception("Could not provision FFmpeg")
                    errors.append(f"FFmpeg: {error}")
            self._raise_if_cancelled(cancel)
            status = self.detect(manual_ffmpeg_directory)
            if not status.deno_available:
                try:
                    self._install_archive(DENO_ARCHIVE, progress, cancel)
                except RuntimeToolCancelled:
                    raise
                except Exception as error:
                    LOGGER.exception("Could not provision Deno")
                    errors.append(f"Deno: {error}")

        status = self.detect(manual_ffmpeg_directory)
        if not status.ffmpeg_available and not any(item.startswith("FFmpeg:") for item in errors):
            errors.append("FFmpeg: installation did not produce valid executables")
        if not status.deno_available and not any(item.startswith("Deno:") for item in errors):
            errors.append("Deno: installation did not produce a supported executable")
        return replace(status, errors=tuple(errors))

    def _component_directory(self, archive: ToolArchive) -> Path:
        return self.install_root / archive.name / archive.version

    def _install_archive(
        self,
        archive: ToolArchive,
        progress: ProgressCallback | None,
        cancel: CancelCheck,
    ) -> None:
        self._validate_manifest_url(archive.url)
        final_directory = self._component_directory(archive)
        if self._validate_component(archive, final_directory):
            return
        archive_path = self._download_archive(archive, progress, cancel)
        staging = final_directory.parent / f".{archive.version}.staging-{uuid.uuid4().hex}"
        quarantine: Path | None = None
        try:
            staging.mkdir(parents=True, exist_ok=False)
            self._extract_selected(archive, archive_path, staging, cancel)
            if not self._validate_component(archive, staging):
                raise RuntimeToolIntegrityError(
                    f"{archive.name} executables failed their version checks"
                )
            marker = {
                "schema": 1,
                "component": archive.name,
                "version": archive.version,
                "sha256": archive.sha256,
                "source": archive.url,
            }
            (staging / ".installed.json").write_text(
                json.dumps(marker, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            self._raise_if_cancelled(cancel)
            final_directory.parent.mkdir(parents=True, exist_ok=True)
            if final_directory.exists():
                if self._validate_component(archive, final_directory):
                    return
                quarantine = final_directory.parent / (
                    f".{archive.version}.corrupt-{uuid.uuid4().hex}"
                )
                os.replace(final_directory, quarantine)
            os.replace(staging, final_directory)
        finally:
            archive_path.unlink(missing_ok=True)
            self._remove_tree(staging)
            if quarantine is not None:
                self._remove_tree(quarantine)

    def _download_archive(
        self,
        archive: ToolArchive,
        progress: ProgressCallback | None,
        cancel: CancelCheck,
    ) -> Path:
        downloads = self.install_root / ".downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        last_error: BaseException | None = None
        for attempt in range(3):
            self._raise_if_cancelled(cancel)
            handle = tempfile.NamedTemporaryFile(
                prefix=f"{archive.name}-",
                suffix=".partial",
                dir=downloads,
                delete=False,
            )
            partial = Path(handle.name)
            handle.close()
            try:
                if progress is not None:
                    progress(archive.name, 0, archive.size)
                request = urllib.request.Request(
                    archive.url,
                    headers={"User-Agent": "YT-DW/0.1 runtime-tool-installer"},
                )
                with self._opener(request, timeout=10) as response:
                    final_url = response.geturl() if hasattr(response, "geturl") else archive.url
                    self._validate_manifest_url(str(final_url))
                    declared_size = self._content_length(response)
                    allowed_size = archive.size + max(1024 * 1024, archive.size // 50)
                    if declared_size is not None and declared_size > allowed_size:
                        raise RuntimeToolIntegrityError(
                            f"server reported an oversized {archive.name} archive"
                        )
                    digest = hashlib.sha256()
                    downloaded = 0
                    with partial.open("wb") as destination:
                        while True:
                            self._raise_if_cancelled(cancel)
                            chunk = response.read(_CHUNK_SIZE)
                            if not chunk:
                                break
                            downloaded += len(chunk)
                            if downloaded > allowed_size:
                                raise RuntimeToolIntegrityError(
                                    f"downloaded {archive.name} archive exceeded its size limit"
                                )
                            digest.update(chunk)
                            destination.write(chunk)
                            if progress is not None:
                                progress(archive.name, downloaded, archive.size)
                if downloaded != archive.size:
                    raise _IncompleteDownloadError(
                        f"unexpected {archive.name} archive size: {downloaded} bytes"
                    )
                if digest.hexdigest().casefold() != archive.sha256.casefold():
                    raise RuntimeToolIntegrityError(
                        f"SHA-256 verification failed for {archive.name}"
                    )
                return partial
            except RuntimeToolCancelled:
                partial.unlink(missing_ok=True)
                raise
            except RuntimeToolIntegrityError:
                partial.unlink(missing_ok=True)
                raise
            except Exception as error:
                partial.unlink(missing_ok=True)
                last_error = error
                if not self._is_retryable(error) or attempt == 2:
                    break
                self._sleep_with_cancellation(float(attempt + 1), cancel)
        raise RuntimeToolError(f"download failed: {last_error}") from last_error

    @staticmethod
    def _content_length(response: Any) -> int | None:
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get("Content-Length")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_retryable(error: BaseException) -> bool:
        if isinstance(error, urllib.error.HTTPError):
            return error.code in {408, 429} or 500 <= error.code < 600
        return isinstance(
            error,
            (
                _IncompleteDownloadError,
                http.client.IncompleteRead,
                urllib.error.URLError,
                ConnectionError,
                TimeoutError,
                ssl.SSLError,
            ),
        )

    def _cleanup_stale_installation_files(self) -> None:
        """Remove abandoned temporary data while holding the installation lock."""

        downloads = self.install_root / ".downloads"
        if downloads.is_dir():
            for partial in downloads.glob("*.partial"):
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Could not remove stale runtime download %s", partial)
        for archive in (FFMPEG_ARCHIVE, DENO_ARCHIVE):
            component_root = self.install_root / archive.name
            if not component_root.is_dir():
                continue
            for pattern in (".*.staging-*", ".*.corrupt-*"):
                for abandoned in component_root.glob(pattern):
                    self._remove_tree(abandoned)

    def _extract_selected(
        self,
        archive: ToolArchive,
        archive_path: Path,
        staging: Path,
        cancel: CancelCheck,
    ) -> None:
        expected = {source.casefold(): target for source, target in archive.members}
        found: dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(archive_path) as bundle:
            infos = bundle.infolist()
            if len(infos) > _MAX_ARCHIVE_FILES:
                raise RuntimeToolIntegrityError("archive contains too many files")
            total_uncompressed = 0
            seen_names: set[str] = set()
            for info in infos:
                normalized = self._safe_archive_name(info.filename)
                key = normalized.casefold()
                if key in seen_names:
                    raise RuntimeToolIntegrityError("archive contains duplicate file names")
                seen_names.add(key)
                if self._zip_entry_is_link(info):
                    raise RuntimeToolIntegrityError("archive contains a symbolic link")
                total_uncompressed += info.file_size
                if info.file_size > _MAX_MEMBER_SIZE:
                    raise RuntimeToolIntegrityError("archive member exceeds its size limit")
                if total_uncompressed > _MAX_UNCOMPRESSED_SIZE:
                    raise RuntimeToolIntegrityError("archive expands beyond its size limit")
                if (
                    info.file_size > 0
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO
                ):
                    raise RuntimeToolIntegrityError("archive has an unsafe compression ratio")
                if key in expected:
                    found[key] = info
            missing = expected.keys() - found.keys()
            if missing:
                raise RuntimeToolIntegrityError(
                    f"archive is missing required files: {', '.join(sorted(missing))}"
                )
            for source, target in archive.members:
                self._raise_if_cancelled(cancel)
                destination = staging / target
                if destination.parent != staging or destination.exists():
                    raise RuntimeToolIntegrityError("invalid archive target path")
                info = found[source.casefold()]
                written = 0
                with bundle.open(info) as source_file, destination.open("xb") as target_file:
                    while True:
                        self._raise_if_cancelled(cancel)
                        chunk = source_file.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size:
                            raise RuntimeToolIntegrityError(
                                "archive member changed while extracting"
                            )
                        target_file.write(chunk)
                if written != info.file_size:
                    raise RuntimeToolIntegrityError("archive member was truncated")

    @staticmethod
    def _safe_archive_name(value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith(("/", "//"))
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(":" in part for part in path.parts)
        ):
            raise RuntimeToolIntegrityError(f"unsafe archive path: {value!r}")
        return path.as_posix()

    @staticmethod
    def _zip_entry_is_link(info: zipfile.ZipInfo) -> bool:
        mode = (info.external_attr >> 16) & 0xFFFF
        return stat.S_ISLNK(mode)

    def _validate_component(self, archive: ToolArchive, directory: Path) -> bool:
        if archive.name == "ffmpeg":
            return self._validate_ffmpeg_directory(directory) is not None
        return bool(self._validate_deno(directory / "deno.exe"))

    def _validate_ffmpeg_directory(
        self,
        directory: Path,
    ) -> tuple[Path, Path, str, str] | None:
        suffix = ".exe" if self._platform == "win32" else ""
        return self._validate_ffmpeg_paths(
            directory / f"ffmpeg{suffix}",
            directory / f"ffprobe{suffix}",
        )

    def _validate_ffmpeg_paths(
        self,
        ffmpeg: Path,
        ffprobe: Path,
    ) -> tuple[Path, Path, str, str] | None:
        ffmpeg_version = self._run_version(ffmpeg, "-version")
        ffprobe_version = self._run_version(ffprobe, "-version")
        if not ffmpeg_version or not ffprobe_version:
            return None
        return (
            ffmpeg.resolve(),
            ffprobe.resolve(),
            ffmpeg_version,
            ffprobe_version,
        )

    def _validate_deno(self, executable: Path) -> str:
        output = self._run_version(executable, "--version")
        if not output:
            return ""
        match = re.search(r"^deno\s+(\d+(?:\.\d+){1,3})", output, re.IGNORECASE)
        if not match:
            return ""
        version = match.group(1)
        parts = tuple(int(item) for item in version.split("."))
        padded = parts + (0,) * max(0, 3 - len(parts))
        return output if padded[:3] >= _DENO_MIN_VERSION else ""

    def _run_version(self, executable: Path, argument: str) -> str:
        if not executable.is_file():
            return ""
        try:
            result = subprocess.run(
                [str(executable), argument],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if result.returncode != 0:
            return ""
        return (result.stdout or result.stderr).splitlines()[0].strip()

    @staticmethod
    def _validate_manifest_url(url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme.casefold() != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or host not in _ALLOWED_DOWNLOAD_HOSTS
        ):
            raise RuntimeToolIntegrityError(f"untrusted runtime-tool URL: {url}")

    @contextmanager
    def _installation_lock(self, cancel: CancelCheck) -> Iterator[None]:
        lock_path = self.install_root / ".install.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                self._raise_if_cancelled(cancel)
                try:
                    self._lock_file(handle)
                    break
                except OSError as error:
                    if not self._lock_is_busy(error):
                        raise
                    if time.monotonic() >= deadline:
                        raise RuntimeToolError(
                            "timed out waiting for another installation"
                        ) from error
                    self._sleeper(0.1)
            try:
                yield
            finally:
                self._unlock_file(handle)

    @staticmethod
    def _lock_file(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        fcntl: Any = __import__("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_file(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        fcntl: Any = __import__("fcntl")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _lock_is_busy(error: OSError) -> bool:
        return error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
            error, "winerror", None
        ) in {33, 36}

    def _sleep_with_cancellation(self, seconds: float, cancel: CancelCheck) -> None:
        remaining = seconds
        while remaining > 0:
            self._raise_if_cancelled(cancel)
            interval = min(0.1, remaining)
            self._sleeper(interval)
            remaining -= interval

    @staticmethod
    def _raise_if_cancelled(cancel: CancelCheck) -> None:
        if cancel():
            raise RuntimeToolCancelled("runtime tool setup was cancelled")

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "DENO_ARCHIVE",
    "FFMPEG_ARCHIVE",
    "RuntimeToolCancelled",
    "RuntimeToolError",
    "RuntimeToolIntegrityError",
    "RuntimeToolName",
    "RuntimeToolsService",
    "RuntimeToolsStatus",
    "ToolArchive",
    "ToolSource",
]
