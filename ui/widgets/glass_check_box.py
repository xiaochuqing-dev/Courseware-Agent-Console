from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QWidget


class GlassCheckBox(QCheckBox):
    """在 Windows 亮色和深色主题下都保持清晰的整格可点击复选框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("选择或取消选择此课件")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(46, 36)

    def hitButton(self, position) -> bool:  # noqa: N802
        return self.rect().contains(position)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        side = 22.0
        box = QRectF(
            (self.width() - side) / 2,
            (self.height() - side) / 2,
            side,
            side,
        )
        checked = self.isChecked()
        fill = QColor("#087F68") if checked else QColor("#FFFFFF")
        border = QColor("#075E54") if checked else QColor("#425466")
        if self.hasFocus() and not checked:
            border = QColor("#0BA878")
        painter.setBrush(fill)
        painter.setPen(QPen(border, 2.0))
        painter.drawRoundedRect(box, 4.0, 4.0)
        if checked:
            painter.setPen(
                QPen(
                    QColor("#FFFFFF"),
                    2.7,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawLine(
                QPointF(box.left() + 5.0, box.center().y()),
                QPointF(box.left() + 9.2, box.bottom() - 5.0),
            )
            painter.drawLine(
                QPointF(box.left() + 9.2, box.bottom() - 5.0),
                QPointF(box.right() - 4.2, box.top() + 5.0),
            )
