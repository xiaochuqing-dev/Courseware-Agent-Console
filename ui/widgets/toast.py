from PySide6.QtCore import QEvent, QEasingCurve, QPoint, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    def __init__(self, parent: QWidget, anchor: QWidget | None = None) -> None:
        super().__init__(parent)
        self._anchor = anchor
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setStyleSheet(
            "QLabel { color: white; background: rgba(30, 79, 66, 235); "
            "border-radius: 8px; padding: 9px 18px; font-weight: 600; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._animation = QPropertyAnimation(self, b"windowOpacity", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._reposition_timer = QTimer(self)
        self._reposition_timer.setSingleShot(True)
        self._reposition_timer.timeout.connect(self.reposition)
        parent.installEventFilter(self)
        if anchor is not None:
            anchor.installEventFilter(self)

    def show_message(self, text: str, duration_ms: int = 1800) -> None:
        self.setText(text)
        self.reposition()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._reposition_timer.start(0)
        self._animation.stop()
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()
        self._hide_timer.start(duration_ms)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        anchor = self._anchor
        if anchor is not None and anchor.isVisibleTo(parent):
            anchor_top_left = anchor.mapTo(parent, QPoint(0, 0))
            available_width = max(180, anchor.width() - 48)
            content_width = self.fontMetrics().horizontalAdvance(self.text()) + 48
            self.setFixedWidth(min(available_width, max(280, content_width)))
            self.adjustSize()
            x = anchor_top_left.x() + (anchor.width() - self.width()) // 2
            y = anchor_top_left.y() + 8
        else:
            available_width = max(180, parent.width() - 48)
            content_width = self.fontMetrics().horizontalAdvance(self.text()) + 48
            self.setFixedWidth(min(available_width, max(280, content_width)))
            self.adjustSize()
            x = (parent.width() - self.width()) // 2
            y = parent.height() - self.height() - 34
        x = max(12, min(x, parent.width() - self.width() - 12))
        y = max(12, min(y, parent.height() - self.height() - 12))
        self.move(x, y)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if self.isVisible() and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.LayoutRequest,
            QEvent.Type.Show,
        }:
            self._reposition_timer.start(0)
        return super().eventFilter(watched, event)
