from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget


class BackgroundWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appBackground")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 基础线性渐变：左上浅白 → 中部青绿 → 右下浅蓝
        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0.0, QColor("#F8FFFC"))
        base.setColorAt(0.32, QColor("#E6FAF2"))
        base.setColorAt(0.65, QColor("#DFF5FF"))
        base.setColorAt(1.0, QColor("#F4FFFB"))
        painter.fillRect(self.rect(), base)

        # 多层径向渐变叠加：更明显的青翠绿和流动蓝色泽
        layers = (
            (0.12, 0.18, 0.58, QColor(57, 196, 205, 82)),      # 左上方蓝绿光晕
            (0.45, 0.55, 0.48, QColor(111, 225, 181, 51)),     # 中央青翠绿光晕
            (0.88, 0.20, 0.52, QColor(110, 199, 236, 46)),     # 右上流动蓝
            (0.68, 0.88, 0.42, QColor(115, 212, 178, 38)),     # 右下青绿补充
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

        # 绘制装饰元素
        self._draw_decorations(painter)

        painter.end()

    def _draw_decorations(self, painter: QPainter) -> None:
        from PySide6.QtGui import QPainterPath
        w, h = self.width(), self.height()

        # 左下角流动波浪曲线（更明显）
        wave_pen = QPen(QColor(85, 195, 185, 65), 2.8)
        painter.setPen(wave_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 波浪1：左下角大弧线
        wave1 = QPainterPath()
        wave1.moveTo(0, h * 0.88)
        wave1.cubicTo(
            w * 0.08, h * 0.82,
            w * 0.15, h * 0.90,
            w * 0.22, h * 0.85
        )
        painter.drawPath(wave1)

        # 波浪2：左下角第二条
        wave2 = QPainterPath()
        wave2.moveTo(0, h * 0.72)
        wave2.cubicTo(
            w * 0.10, h * 0.68,
            w * 0.18, h * 0.76,
            w * 0.26, h * 0.72
        )
        painter.drawPath(wave2)

        # 波浪3：左下角第三条
        wave3 = QPainterPath()
        wave3.moveTo(0, h * 0.96)
        wave3.cubicTo(
            w * 0.06, h * 0.93,
            w * 0.12, h * 0.98,
            w * 0.18, h * 0.95
        )
        painter.drawPath(wave3)

        # 右下角大叶片装饰（更明显）
        leaf_color = QColor(88, 204, 151, 56)
        painter.setBrush(leaf_color)
        painter.setPen(Qt.PenStyle.NoPen)

        # 右下大叶片1
        painter.drawEllipse(int(w * 0.88), int(h * 0.88), 52, 28)
        # 右下大叶片2
        painter.drawEllipse(int(w * 0.92), int(h * 0.82), 46, 24)
        # 右下小叶片3
        painter.drawEllipse(int(w * 0.85), int(h * 0.94), 38, 20)

        # 右上角小叶片点缀
        painter.drawEllipse(int(w * 0.91), int(h * 0.14), 28, 16)
        painter.drawEllipse(int(w * 0.95), int(h * 0.10), 22, 13)

        # 左上角小叶片
        painter.drawEllipse(int(w * 0.08), int(h * 0.12), 32, 18)
        painter.drawEllipse(int(w * 0.05), int(h * 0.18), 28, 15)

        # 中部区域散落圆点（更多更明显）
        dot_positions = [
            (0.38, 0.25, 6, 51),
            (0.52, 0.32, 5, 41),
            (0.45, 0.68, 7, 46),
            (0.62, 0.48, 5, 38),
            (0.73, 0.62, 6, 43),
            (0.58, 0.78, 5, 36),
            (0.68, 0.38, 4, 33),
            (0.42, 0.52, 5, 40),
        ]

        for x_ratio, y_ratio, radius, alpha in dot_positions:
            dot_color = QColor(115, 212, 178, alpha)
            painter.setBrush(dot_color)
            painter.drawEllipse(
                QPointF(w * x_ratio, h * y_ratio),
                radius, radius
            )

        # 中部轻柔装饰曲线
        deco_pen = QPen(QColor(125, 190, 180, 28), 1.8)
        painter.setPen(deco_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 中部曲线1
        path1 = QPainterPath()
        path1.moveTo(w * 0.35, h * 0.28)
        path1.cubicTo(
            w * 0.45, h * 0.22,
            w * 0.55, h * 0.32,
            w * 0.68, h * 0.26
        )
        painter.drawPath(path1)

        # 中部曲线2
        path2 = QPainterPath()
        path2.moveTo(w * 0.52, h * 0.55)
        path2.cubicTo(
            w * 0.62, h * 0.50,
            w * 0.68, h * 0.60,
            w * 0.78, h * 0.56
        )
        painter.drawPath(path2)

