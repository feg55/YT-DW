"""Background provisioning for FFmpeg, FFprobe, and the YouTube JS runtime."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QThread, Signal

from openmediadl.core.runtime_tools import RuntimeToolName, RuntimeToolsService

LOGGER = logging.getLogger(__name__)


class RuntimeToolsWorker(QThread):
    progress_changed = Signal(str, int, int)
    result_ready = Signal(object)
    setup_failed = Signal(str)

    def __init__(
        self,
        service: RuntimeToolsService,
        manual_ffmpeg_directory: str | None,
    ) -> None:
        super().__init__()
        self.service = service
        self.manual_ffmpeg_directory = manual_ffmpeg_directory
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        def report(tool: RuntimeToolName, downloaded: int, total: int) -> None:
            self.progress_changed.emit(tool, downloaded, total)

        try:
            result = self.service.provision(
                self.manual_ffmpeg_directory,
                progress=report,
                is_cancelled=self._cancel_event.is_set,
            )
        except Exception as error:
            if self._cancel_event.is_set():
                return
            LOGGER.exception("Automatic runtime tool setup failed")
            self.setup_failed.emit(str(error))
            return
        if not self._cancel_event.is_set():
            self.result_ready.emit(result)
