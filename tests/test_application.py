from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import openmediadl.application as application


class _FakeApplication:
    def __init__(self, _argv: list[str]) -> None:
        self.style = ""

    def setStyle(self, style: str) -> None:  # noqa: N802 - Qt API compatibility
        self.style = style

    def exec(self) -> int:
        raise AssertionError("the event loop must not start after initialization failure")


@pytest.mark.parametrize(
    ("failure_point", "expected_stage"),
    (
        ("discover", "locating application data"),
        ("create", "creating application directories"),
        ("logging", "initializing application logging"),
    ),
)
def test_run_application_reports_early_initialization_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
    expected_stage: str,
) -> None:
    paths = application.ApplicationPaths(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        database_file=tmp_path / "data" / "yt-dw.sqlite3",
        archive_file=tmp_path / "data" / "archive.txt",
        bundled_tools_dir=tmp_path / "tools",
    )
    messages: list[tuple[str, str]] = []

    class FakeMessageBox:
        @staticmethod
        def critical(_parent: Any, title: str, message: str) -> None:
            messages.append((title, message))

    def discover(_cls: type[application.ApplicationPaths]) -> application.ApplicationPaths:
        if failure_point == "discover":
            raise OSError("data location unavailable")
        return paths

    def create(_self: application.ApplicationPaths) -> None:
        if failure_point == "create":
            raise PermissionError("directory is read-only")

    def configure_logging(_log_dir: Path) -> Path:
        if failure_point == "logging":
            raise OSError("log file is locked")
        raise AssertionError("logging must not be reached for this failure point")

    monkeypatch.setattr(application, "QApplication", _FakeApplication)
    monkeypatch.setattr(application, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(application.ApplicationPaths, "discover", classmethod(discover))
    monkeypatch.setattr(application.ApplicationPaths, "create", create)
    monkeypatch.setattr(application, "configure_logging", configure_logging)

    assert application.run_application([]) == 1
    assert len(messages) == 1
    title, message = messages[0]
    assert title == "YT-DW could not start"
    assert expected_stage in message
    assert "A log file could not be initialized." in message


def test_application_icon_resource_exists() -> None:
    resource = application.application_icon_resource()

    assert resource.is_file()
    assert resource.name == "yt-dw.png"


def test_run_application_sets_icon_before_opening_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = application.ApplicationPaths(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        database_file=tmp_path / "data" / "yt-dw.sqlite3",
        archive_file=tmp_path / "data" / "archive.txt",
        bundled_tools_dir=tmp_path / "tools",
    )
    expected_icon = object()
    events: list[object] = []

    class FakeApplication(_FakeApplication):
        def setWindowIcon(self, icon: object) -> None:  # noqa: N802 - Qt API compatibility
            events.append(("icon", icon))

    class FakeMessageBox:
        @staticmethod
        def critical(_parent: Any, _title: str, _message: str) -> None:
            return None

    def fail_database(_database_file: Path) -> None:
        events.append("database")
        raise OSError("database unavailable")

    monkeypatch.setattr(application, "QApplication", FakeApplication)
    monkeypatch.setattr(application, "QMessageBox", FakeMessageBox)
    monkeypatch.setattr(
        application.ApplicationPaths,
        "discover",
        classmethod(lambda _cls: paths),
    )
    monkeypatch.setattr(
        application,
        "configure_logging",
        lambda _log_dir: tmp_path / "yt-dw.log",
    )
    monkeypatch.setattr(application, "load_application_icon", lambda: expected_icon)
    monkeypatch.setattr(application, "Database", fail_database)

    assert application.run_application([]) == 1
    assert events == [("icon", expected_icon), "database"]


def test_discover_uses_yt_dw_paths_for_new_windows_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    paths = application.ApplicationPaths.discover()

    assert paths.data_dir == tmp_path / "YT-DW"
    assert paths.cache_dir == tmp_path / "YT-DW" / "cache"
    assert paths.database_file == tmp_path / "YT-DW" / "yt-dw.sqlite3"
    assert paths.archive_file == tmp_path / "YT-DW" / "yt-dw-archive.txt"
    assert paths.managed_tools_dir == tmp_path / "YT-DW" / "tools"


def test_discover_reuses_legacy_database_without_moving_user_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "OpenMediaDL"
    legacy.mkdir()
    (legacy / "openmediadl.sqlite3").touch()
    # Even an empty new directory must not hide an existing legacy queue.
    (tmp_path / "YT-DW").mkdir()

    paths = application.ApplicationPaths.discover()

    assert paths.data_dir == legacy
    assert paths.cache_dir == legacy / "cache"
    assert paths.database_file == legacy / "openmediadl.sqlite3"
    assert paths.archive_file == legacy / "yt-dlp-archive.txt"
    assert paths.managed_tools_dir == tmp_path / "YT-DW" / "tools"


def test_discover_ignores_empty_legacy_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "OpenMediaDL").mkdir()

    paths = application.ApplicationPaths.discover()

    assert paths.data_dir == tmp_path / "YT-DW"
    assert paths.database_file == tmp_path / "YT-DW" / "yt-dw.sqlite3"


def test_discover_prefers_new_database_when_both_exist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "OpenMediaDL"
    current = tmp_path / "YT-DW"
    legacy.mkdir()
    current.mkdir()
    (legacy / "openmediadl.sqlite3").touch()
    (current / "yt-dw.sqlite3").touch()

    paths = application.ApplicationPaths.discover()

    assert paths.data_dir == current
    assert paths.database_file == current / "yt-dw.sqlite3"


def test_log_file_preserves_existing_legacy_log_until_new_log_exists(tmp_path: Path) -> None:
    legacy = tmp_path / "openmediadl.log"
    current = tmp_path / "yt-dw.log"

    assert application._select_log_file(tmp_path) == current

    legacy.touch()

    assert application._select_log_file(tmp_path) == legacy

    current.touch()
    assert application._select_log_file(tmp_path) == current
