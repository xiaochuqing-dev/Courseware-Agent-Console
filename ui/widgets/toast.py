from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { color: white; background: rgba(30, 79, 66, 235); "
            "border-radius: 8px; padding: 9px 18px; font-weight: 600; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def show_message(self, text: str, duration_ms: int = 1800) -> None:
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 34
        self.move(max(12, x), max(12, y))
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._animation.stop()
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()
        QTimer.singleShot(duration_ms, self.hide)

