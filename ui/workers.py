from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot


class BackgroundWorker(QObject):
    stage_changed = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, operation: Callable[[Callable[[str], None]], Any]) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(self.stage_changed.emit)
        except Exception as exc:
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()


class BackgroundTaskRelay(QObject):
    def __init__(self, succeeded, failed, finished, parent: QObject) -> None:
        super().__init__(parent)
        self._succeeded = succeeded
        self._failed = failed
        self._finished = finished

    @Slot(object)
    def on_succeeded(self, result: object) -> None:
        self._succeeded(result)

    @Slot(object)
    def on_failed(self, error: object) -> None:
        self._failed(error)

    @Slot()
    def on_finished(self) -> None:
        self._finished()
