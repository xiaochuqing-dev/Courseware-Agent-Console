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
        w, h = self.width(), self.height()

        # 左下角小叶片装饰（3-4片）
        leaf_color = QColor(88, 204, 151, 31)
        painter.setBrush(leaf_color)
        painter.setPen(Qt.PenStyle.NoPen)

        # 叶片1
        painter.drawEllipse(int(w * 0.05), int(h * 0.75), 32, 18)
        # 叶片2
        painter.drawEllipse(int(w * 0.03), int(h * 0.82), 28, 16)
        # 叶片3
        painter.drawEllipse(int(w * 0.08), int(h * 0.88), 24, 14)

        # 右下角小叶片
        painter.drawEllipse(int(w * 0.92), int(h * 0.86), 26, 15)
        painter.drawEllipse(int(w * 0.88), int(h * 0.92), 22, 13)

        # 中部区域散落圆点（6-8个）
        dot_positions = [
            (0.38, 0.25, 5, 41),
            (0.52, 0.32, 4, 31),
            (0.45, 0.68, 6, 36),
            (0.62, 0.48, 4, 28),
            (0.73, 0.62, 5, 33),
            (0.58, 0.78, 4, 26),
        ]

        for x_ratio, y_ratio, radius, alpha in dot_positions:
            dot_color = QColor(115, 212, 178, alpha)
            painter.setBrush(dot_color)
            painter.drawEllipse(
                QPointF(w * x_ratio, h * y_ratio),
                radius, radius
            )

        # 轻柔波浪曲线（2条）
        pen = QPen(QColor(125, 190, 180, 20), 1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # 波浪线1：中部偏上
        from PySide6.QtGui import QPainterPath
        path1 = QPainterPath()
        path1.moveTo(w * 0.35, h * 0.28)
        path1.cubicTo(
            w * 0.45, h * 0.22,
            w * 0.55, h * 0.32,
            w * 0.68, h * 0.26
        )
        painter.drawPath(path1)

        # 波浪线2：中部偏右
        path2 = QPainterPath()
        path2.moveTo(w * 0.52, h * 0.52)
        path2.cubicTo(
            w * 0.62, h * 0.48,
            w * 0.68, h * 0.58,
            w * 0.78, h * 0.54
        )
        painter.drawPath(path2)

