"""Application composition root and platform-specific storage paths."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from openmediadl.appearance import AppearanceController
from openmediadl.core.ffmpeg_service import FFmpegService
from openmediadl.core.queue_manager import QueueManager
from openmediadl.core.thumbnail_service import ThumbnailService
from openmediadl.database.connection import Database
from openmediadl.database.repositories import SettingsRepository, WindowStateRepository
from openmediadl.domain.settings import AppearanceSettings
from openmediadl.i18n import Translator
from openmediadl.ui.main_window import MainWindow

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    data_dir: Path
    cache_dir: Path
    log_dir: Path
    database_file: Path
    archive_file: Path
    bundled_tools_dir: Path

    @classmethod
    def discover(cls) -> ApplicationPaths:
        if sys.platform == "win32":
            base = (
                Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
                / "OpenMediaDL"
            )
            cache = base / "cache"
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "OpenMediaDL"
            cache = Path.home() / "Library" / "Caches" / "OpenMediaDL"
        else:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            base = data_home / "openmediadl"
            cache = cache_home / "openmediadl"
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return cls(
            data_dir=base,
            cache_dir=cache,
            log_dir=base / "logs",
            database_file=base / "openmediadl.sqlite3",
            archive_file=base / "yt-dlp-archive.txt",
            bundled_tools_dir=bundle_root / "tools",
        )

    def create(self) -> None:
        for directory in (self.data_dir, self.cache_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)


def configure_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "openmediadl.log"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    if not getattr(sys, "frozen", False):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    return log_file


def application_icon_resource() -> Traversable:
    """Return the application icon bundled with the Python package."""

    return files("openmediadl").joinpath("resources", "icons", "openmediadl.png")


def load_application_icon() -> QIcon:
    """Load the packaged icon in source and frozen PyInstaller builds."""

    resource = application_icon_resource()
    if not resource.is_file():
        raise FileNotFoundError(f"Application icon resource is missing: {resource}")
    with as_file(resource) as icon_path:
        icon = QIcon(str(icon_path))
    if icon.isNull():
        raise ValueError(f"Application icon resource is invalid: {resource}")
    return icon


def run_application(argv: list[str] | None = None) -> int:
    QCoreApplication.setOrganizationName("OpenMediaDL")
    QCoreApplication.setApplicationName("OpenMediaDL")
    QCoreApplication.setApplicationVersion("0.1.0")
    app = QApplication(argv if argv is not None else sys.argv)
    database: Database | None = None
    paths: ApplicationPaths | None = None
    log_file: Path | None = None
    startup_stage = "locating application data"
    try:
        paths = ApplicationPaths.discover()
        startup_stage = "creating application directories"
        paths.create()
        startup_stage = "initializing application logging"
        log_file = configure_logging(paths.log_dir)
        startup_stage = "loading the application icon"
        app.setWindowIcon(load_application_icon())
        startup_stage = "opening the application database"
        database = Database(paths.database_file)
        queue_manager = QueueManager(database)
        settings = SettingsRepository(database)
        appearance_controller = AppearanceController(app)
        appearance = settings.load().appearance or AppearanceSettings()
        appearance_controller.apply(appearance.theme)
        translator = Translator(appearance.language)
        window_state = WindowStateRepository(database)
        ffmpeg_service = FFmpegService(paths.bundled_tools_dir)
        thumbnail_service = ThumbnailService(paths.cache_dir / "thumbnails")
        window = MainWindow(
            paths,
            queue_manager,
            settings,
            window_state,
            ffmpeg_service,
            thumbnail_service,
            translator=translator,
            appearance_controller=appearance_controller,
        )
        window.show()
        LOGGER.info("OpenMediaDL started successfully")
        return app.exec()
    except Exception as error:
        try:
            LOGGER.exception("OpenMediaDL failed while %s", startup_stage)
        except Exception:
            # A broken logging destination must not suppress the visible startup error.
            pass
        message = f"OpenMediaDL failed while {startup_stage}.\n\n{type(error).__name__}: {error}"
        if log_file is not None:
            message += f"\n\nFull details were written to {log_file}."
        else:
            message += "\n\nA log file could not be initialized."
        try:
            QMessageBox.critical(None, "OpenMediaDL could not start", message)
        except Exception:
            print(message, file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()
