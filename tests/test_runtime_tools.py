from __future__ import annotations

import hashlib
import io
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest

import openmediadl.core.runtime_tools as runtime_tools
from openmediadl.core.runtime_tools import (
    RuntimeToolCancelled,
    RuntimeToolIntegrityError,
    RuntimeToolsService,
    ToolArchive,
)


class _Response:
    def __init__(self, payload: bytes, url: str) -> None:
        self._stream = io.BytesIO(payload)
        self._url = url
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def _zip_payload(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


def _manifest(name: str, payload: bytes, members: tuple[tuple[str, str], ...]) -> ToolArchive:
    return ToolArchive(
        name=name,  # type: ignore[arg-type]
        version="test-1",
        url=f"https://github.com/example/project/releases/download/test-1/{name}.zip",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        members=members,
    )


def _fake_versions(_self: RuntimeToolsService, executable: Path, argument: str) -> str:
    if not executable.is_file():
        return ""
    if executable.name.startswith("ffmpeg") or executable.name.startswith("ffprobe"):
        return f"{executable.stem} version test"
    if executable.name == "deno.exe" and argument == "--version":
        return "deno 2.9.3"
    return ""


def _install_manifests(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ToolArchive, bytes, ToolArchive, bytes]:
    ffmpeg_members = {
        "ffmpeg-test/bin/ffmpeg.exe": b"ffmpeg executable",
        "ffmpeg-test/bin/ffprobe.exe": b"ffprobe executable",
    }
    deno_members = {"deno.exe": b"deno executable"}
    ffmpeg_payload = _zip_payload(ffmpeg_members)
    deno_payload = _zip_payload(deno_members)
    ffmpeg = _manifest(
        "ffmpeg",
        ffmpeg_payload,
        (
            ("ffmpeg-test/bin/ffmpeg.exe", "ffmpeg.exe"),
            ("ffmpeg-test/bin/ffprobe.exe", "ffprobe.exe"),
        ),
    )
    deno = _manifest("deno", deno_payload, (("deno.exe", "deno.exe"),))
    monkeypatch.setattr(runtime_tools, "FFMPEG_ARCHIVE", ffmpeg)
    monkeypatch.setattr(runtime_tools, "DENO_ARCHIVE", deno)
    monkeypatch.setattr(RuntimeToolsService, "_run_version", _fake_versions)
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda _name: None)
    return ffmpeg, ffmpeg_payload, deno, deno_payload


def test_detect_prefers_manual_ffmpeg_and_managed_deno(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RuntimeToolsService(tmp_path / "managed", platform_name="win32")
    monkeypatch.setattr(RuntimeToolsService, "_run_version", _fake_versions)
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda _name: None)
    manual = tmp_path / "manual ffmpeg"
    manual.mkdir()
    (manual / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (manual / "ffprobe.exe").write_bytes(b"ffprobe")
    managed_deno = service.managed_deno_executable
    managed_deno.parent.mkdir(parents=True)
    managed_deno.write_bytes(b"deno")

    status = service.detect(manual)

    assert status.ffmpeg_source == "manual"
    assert status.ffmpeg == (manual / "ffmpeg.exe").resolve()
    assert status.deno_source == "managed"
    assert status.deno == managed_deno.resolve()
    assert status.available


def test_deno_older_than_minimum_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RuntimeToolsService(tmp_path, platform_name="win32")
    deno = service.managed_deno_executable
    deno.parent.mkdir(parents=True)
    deno.write_bytes(b"old")
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        service,
        "_run_version",
        lambda executable, _argument: "deno 2.2.9" if executable == deno else "",
    )

    assert not service.detect().deno_available


def test_provision_installs_verified_archives_and_reuses_them_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg, ffmpeg_payload, deno, deno_payload = _install_manifests(monkeypatch)
    payloads = {ffmpeg.url: ffmpeg_payload, deno.url: deno_payload}
    calls: list[str] = []

    def opener(request: Any, **_kwargs: object) -> _Response:
        calls.append(request.full_url)
        return _Response(payloads[request.full_url], request.full_url)

    progress: list[tuple[str, int, int]] = []
    service = RuntimeToolsService(
        tmp_path / "tools",
        opener=opener,
        platform_name="win32",
    )

    first = service.provision(progress=lambda *values: progress.append(values))
    second = service.provision()

    assert first.available and second.available
    assert first.ffmpeg_source == "managed"
    assert first.deno_source == "managed"
    assert calls == [ffmpeg.url, deno.url]
    assert (tmp_path / "tools" / "ffmpeg" / "test-1" / ".installed.json").is_file()
    assert (tmp_path / "tools" / "deno" / "test-1" / ".installed.json").is_file()
    assert progress[0] == ("ffmpeg", 0, len(ffmpeg_payload))
    assert progress[-1] == ("deno", len(deno_payload), len(deno_payload))


def test_checksum_mismatch_never_publishes_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _zip_payload({"deno.exe": b"deno"})
    manifest = _manifest("deno", payload, (("deno.exe", "deno.exe"),))
    manifest = ToolArchive(
        name=manifest.name,
        version=manifest.version,
        url=manifest.url,
        sha256="0" * 64,
        size=manifest.size,
        members=manifest.members,
    )
    service = RuntimeToolsService(
        tmp_path / "tools",
        opener=lambda request, **_kwargs: _Response(payload, request.full_url),
        platform_name="win32",
    )

    with pytest.raises(RuntimeToolIntegrityError, match="SHA-256"):
        service._install_archive(manifest, None, lambda: False)

    assert not (tmp_path / "tools" / "deno" / "test-1").exists()
    assert list((tmp_path / "tools" / ".downloads").glob("*.partial")) == []


@pytest.mark.parametrize(
    "unsafe_name",
    ("../deno.exe", "..\\deno.exe", "/deno.exe", "C:\\deno.exe"),
)
def test_unsafe_zip_paths_are_rejected(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    payload = _zip_payload({unsafe_name: b"bad", "deno.exe": b"good"})
    archive_path = tmp_path / "unsafe.zip"
    archive_path.write_bytes(payload)
    manifest = _manifest("deno", payload, (("deno.exe", "deno.exe"),))
    service = RuntimeToolsService(tmp_path / "tools", platform_name="win32")
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(RuntimeToolIntegrityError, match="unsafe archive path"):
        service._extract_selected(manifest, archive_path, staging, lambda: False)

    assert list(staging.iterdir()) == []


def test_download_retries_transient_network_errors(
    tmp_path: Path,
) -> None:
    payload = _zip_payload({"deno.exe": b"deno"})
    manifest = _manifest("deno", payload, (("deno.exe", "deno.exe"),))
    attempts = 0
    sleeps: list[float] = []

    def opener(request: Any, **_kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise urllib.error.URLError("temporary outage")
        return _Response(payload, request.full_url)

    service = RuntimeToolsService(
        tmp_path / "tools",
        opener=opener,
        sleeper=sleeps.append,
        platform_name="win32",
    )

    downloaded = service._download_archive(manifest, None, lambda: False)

    assert downloaded.read_bytes() == payload
    assert attempts == 3
    assert sum(sleeps) == pytest.approx(3.0)


def test_download_retries_a_truncated_response(
    tmp_path: Path,
) -> None:
    payload = _zip_payload({"deno.exe": b"deno" * 100})
    manifest = _manifest("deno", payload, (("deno.exe", "deno.exe"),))
    attempts = 0

    def opener(request: Any, **_kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        response_payload = payload[: len(payload) // 2] if attempts == 1 else payload
        return _Response(response_payload, request.full_url)

    service = RuntimeToolsService(
        tmp_path / "tools",
        opener=opener,
        sleeper=lambda _seconds: None,
        platform_name="win32",
    )

    downloaded = service._download_archive(manifest, None, lambda: False)

    assert downloaded.read_bytes() == payload
    assert attempts == 2


def test_download_does_not_retry_a_local_os_error(tmp_path: Path) -> None:
    payload = _zip_payload({"deno.exe": b"deno"})
    manifest = _manifest("deno", payload, (("deno.exe", "deno.exe"),))
    attempts = 0

    def opener(_request: Any, **_kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        raise OSError("disk is full")

    service = RuntimeToolsService(
        tmp_path / "tools",
        opener=opener,
        sleeper=lambda _seconds: None,
        platform_name="win32",
    )

    with pytest.raises(RuntimeError, match="disk is full"):
        service._download_archive(manifest, None, lambda: False)

    assert attempts == 1


def test_provision_removes_abandoned_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg, ffmpeg_payload, deno, deno_payload = _install_manifests(monkeypatch)
    payloads = {ffmpeg.url: ffmpeg_payload, deno.url: deno_payload}
    root = tmp_path / "tools"
    stale_paths = (
        root / ".downloads" / "old.partial",
        root / "ffmpeg" / ".old.staging-test" / "file.bin",
        root / "deno" / ".old.corrupt-test" / "file.bin",
    )
    for path in stale_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"stale")
    service = RuntimeToolsService(
        root,
        opener=lambda request, **_kwargs: _Response(payloads[request.full_url], request.full_url),
        platform_name="win32",
    )

    status = service.provision()

    assert status.available
    assert all(not path.exists() for path in stale_paths)


def test_cancellation_removes_partial_download(
    tmp_path: Path,
) -> None:
    payload = b"x" * (runtime_tools._CHUNK_SIZE * 2)
    manifest = _manifest("deno", payload, (("deno.exe", "deno.exe"),))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    service = RuntimeToolsService(
        tmp_path / "tools",
        opener=lambda request, **_kwargs: _Response(payload, request.full_url),
        platform_name="win32",
    )

    with pytest.raises(RuntimeToolCancelled):
        service._download_archive(manifest, None, cancelled)

    assert list((tmp_path / "tools" / ".downloads").glob("*.partial")) == []


def test_non_windows_provision_reports_missing_tools_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_tools.shutil, "which", lambda _name: None)
    service = RuntimeToolsService(
        tmp_path,
        opener=lambda *_args, **_kwargs: pytest.fail("network must not be used"),
        platform_name="linux",
    )

    status = service.provision()

    assert not status.available
    assert len(status.errors) == 2
