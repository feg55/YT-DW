"""Background FFmpeg discovery so subprocess probes never block the GUI."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from openmediadl.core.ffmpeg_service import FFmpegService


class FFmpegCheckWorker(QThread):
    result_ready = Signal(object)

    def __init__(
        self,
        service: FFmpegService,
        configured_directory: str | None,
    ) -> None:
        super().__init__()
        self.service = service
        self.configured_directory = configured_directory

    def run(self) -> None:
        self.result_ready.emit(self.service.detect(self.configured_directory))
