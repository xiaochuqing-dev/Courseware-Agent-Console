from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget


class BackgroundWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appBackground")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0.0, QColor("#f4fbf8"))
        base.setColorAt(0.48, QColor("#eaf7f2"))
        base.setColorAt(1.0, QColor("#f5fbf9"))
        painter.fillRect(self.rect(), base)

        layers = (
            (0.12, 0.18, 0.52, QColor(110, 203, 169, 48)),
            (0.88, 0.12, 0.44, QColor(94, 184, 166, 36)),
            (0.70, 0.88, 0.60, QColor(119, 201, 174, 34)),
            (0.32, 0.72, 0.38, QColor(163, 222, 198, 28)),
        )
        diagonal = (self.width() ** 2 + self.height() ** 2) ** 0.5
        for x_ratio, y_ratio, radius_ratio, color in layers:
            gradient = QRadialGradient(
                QPointF(self.width() * x_ratio, self.height() * y_ratio),
                diagonal * radius_ratio,
            )
            gradient.setColorAt(0.0, color)
            gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.fillRect(self.rect(), gradient)
        painter.end()

