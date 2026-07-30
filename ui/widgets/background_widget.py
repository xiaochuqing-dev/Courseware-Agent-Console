from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from services.resource_paths import bundled_resource_root


class BackgroundWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appBackground")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 加载背景图，兼容打包环境
        self._bg = QPixmap(str(bundled_resource_root() / "background.jpg"))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if not self._bg.isNull():
            # 拉伸填满整个 widget
            painter.drawPixmap(self.rect(), self._bg)
        painter.end()
