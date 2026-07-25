from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, QThread, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceController(QObject):
    activation_requested = Signal()

    def __init__(self, server_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = server_name
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", server_name)
        temp_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation
        )
        self.lock = QLockFile(str(Path(temp_root) / f"{safe_name}.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._handle_connections)
        self.is_primary = False

    def acquire(self) -> bool:
        if self.notify_existing():
            return False
        if not self.lock.tryLock(0):
            for _ in range(20):
                if self.notify_existing():
                    return False
                QThread.msleep(50)
            return False
        QLocalServer.removeServer(self.server_name)
        if not self.server.listen(self.server_name):
            self.lock.unlock()
            raise RuntimeError(
                f"无法建立单实例通信：{self.server.errorString()}"
            )
        self.is_primary = True
        return True

    def notify_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(150):
            socket.abort()
            return False
        socket.write(b"activate")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True

    @Slot()
    def _handle_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self.activation_requested.emit()
            socket.readAll()
            socket.disconnectFromServer()
            socket.deleteLater()

    def release(self) -> None:
        if self.is_primary:
            self.server.close()
            self.lock.unlock()
            self.is_primary = False
