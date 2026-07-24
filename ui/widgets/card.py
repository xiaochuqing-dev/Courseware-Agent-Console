from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QWidget


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, shadow: bool = True) -> None:
        super().__init__(parent)
        self.setProperty("card", True)
        if shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(30)
            effect.setOffset(0, 8)
            effect.setColor(QColor(31, 92, 74, 28))
            self.setGraphicsEffect(effect)

