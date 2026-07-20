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
        database_file=tmp_path / "data" / "openmediadl.sqlite3",
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
    assert title == "OpenMediaDL could not start"
    assert expected_stage in message
    assert "A log file could not be initialized." in message


def test_application_icon_resource_exists() -> None:
    resource = application.application_icon_resource()

    assert resource.is_file()
    assert resource.name == "openmediadl.png"


def test_run_application_sets_icon_before_opening_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = application.ApplicationPaths(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        database_file=tmp_path / "data" / "openmediadl.sqlite3",
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
        lambda _log_dir: tmp_path / "openmediadl.log",
    )
    monkeypatch.setattr(application, "load_application_icon", lambda: expected_icon)
    monkeypatch.setattr(application, "Database", fail_database)

    assert application.run_application([]) == 1
    assert events == [("icon", expected_icon), "database"]
