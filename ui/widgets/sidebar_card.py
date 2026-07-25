from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame


class SidebarCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # 左下角流动波浪曲线装饰
        wave_gradient = QLinearGradient(0, h * 0.6, 0, h)
        wave_gradient.setColorAt(0.0, QColor(110, 230, 182, 26))
        wave_gradient.setColorAt(0.5, QColor(72, 204, 211, 97))
        wave_gradient.setColorAt(1.0, QColor(39, 169, 199, 122))

        # 绘制底部渐变区域
        painter.setBrush(wave_gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        path.moveTo(0, h * 0.65)
        path.lineTo(0, h)
        path.lineTo(w, h)
        path.lineTo(w, h * 0.72)
        path.cubicTo(
            w * 0.7, h * 0.68,
            w * 0.4, h * 0.75,
            0, h * 0.65
        )
        path.closeSubpath()
        painter.drawPath(path)

        # 绘制流动曲线
        wave_pen = QPen(QColor(85, 220, 195, 95), 2.2)
        painter.setPen(wave_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 波浪线1
        wave1 = QPainterPath()
        wave1.moveTo(0, h * 0.72)
        wave1.cubicTo(
            w * 0.25, h * 0.68,
            w * 0.5, h * 0.76,
            w, h * 0.73
        )
        painter.drawPath(wave1)

        # 波浪线2
        wave2 = QPainterPath()
        wave2.moveTo(0, h * 0.82)
        wave2.cubicTo(
            w * 0.3, h * 0.79,
            w * 0.6, h * 0.85,
            w, h * 0.82
        )
        painter.drawPath(wave2)

        # 波浪线3
        wave3 = QPainterPath()
        wave3.moveTo(0, h * 0.92)
        wave3.cubicTo(
            w * 0.2, h * 0.89,
            w * 0.7, h * 0.94,
            w, h * 0.91
        )
        painter.drawPath(wave3)

        # 小圆点装饰
        painter.setPen(Qt.PenStyle.NoPen)
        dot_positions = [
            (0.15, 0.75, 3, 51),
            (0.65, 0.80, 4, 46),
            (0.35, 0.88, 3, 41),
            (0.80, 0.93, 3, 38),
        ]

        for x_ratio, y_ratio, radius, alpha in dot_positions:
            dot_color = QColor(140, 225, 200, alpha)
            painter.setBrush(dot_color)
            painter.drawEllipse(
                int(w * x_ratio), int(h * y_ratio),
                radius * 2, radius * 2
            )

        painter.end()
