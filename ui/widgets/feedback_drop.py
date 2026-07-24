from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import PendingFeedback
from .elided_label import ElidedLabel


class FeedbackDropArea(QFrame):
    mime_received = Signal(object)
    browse_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("feedbackDropArea")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 13, 18, 13)
        layout.setSpacing(12)

        icon = QLabel()
        icon.setPixmap(
            self.style()
            .standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
            .pixmap(26, 26)
        )
        layout.addWidget(icon)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        title = QLabel("Ctrl+V 粘贴微信截图、文字或已复制文件")
        title.setObjectName("dropTitle")
        title.setWordWrap(True)
        text_layout.addWidget(title)
        hint = QLabel("也可以从资源管理器拖入一个或多个具体文件")
        hint.setObjectName("mutedText")
        hint.setWordWrap(True)
        text_layout.addWidget(hint)
        layout.addLayout(text_layout, 1)

        browse = QPushButton("选择文件")
        browse.clicked.connect(self.browse_requested)
        layout.addWidget(browse)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            self.mime_received.emit(QGuiApplication.clipboard().mimeData())
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasImage() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self.mime_received.emit(event.mimeData())
        event.acceptProposedAction()


class PendingFeedbackRow(QFrame):
    remove_requested = Signal(str)

    def __init__(self, feedback: PendingFeedback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.feedback = feedback
        self.setObjectName("pendingFeedbackRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 6, 5)
        layout.setSpacing(10)

        thumbnail = QLabel()
        thumbnail.setFixedSize(38, 38)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = self._preview_pixmap(feedback)
        thumbnail.setPixmap(
            pixmap.scaled(
                36,
                36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        layout.addWidget(thumbnail)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        name = ElidedLabel(feedback.name)
        name.setObjectName("pendingFileName")
        text_layout.addWidget(name)
        detail = feedback.preview or self._kind_text(feedback)
        preview = ElidedLabel(detail, mode=Qt.TextElideMode.ElideRight)
        preview.setObjectName("mutedText")
        text_layout.addWidget(preview)
        layout.addLayout(text_layout, 1)

        remove = QPushButton("×")
        remove.setProperty("role", "quiet")
        remove.setProperty("iconOnly", True)
        remove.setToolTip("移除待保存反馈")
        remove.clicked.connect(lambda: self.remove_requested.emit(feedback.item_id))
        layout.addWidget(remove)

    def _preview_pixmap(self, feedback: PendingFeedback) -> QPixmap:
        pixmap = QPixmap()
        if feedback.kind == "image":
            if feedback.source_path:
                pixmap.load(str(feedback.source_path))
            elif feedback.content:
                pixmap.loadFromData(feedback.content)
        if pixmap.isNull():
            standard = (
                QStyle.StandardPixmap.SP_FileIcon
                if feedback.kind != "text"
                else QStyle.StandardPixmap.SP_FileDialogDetailedView
            )
            pixmap = self.style().standardIcon(standard).pixmap(28, 28)
        return pixmap

    @staticmethod
    def _kind_text(feedback: PendingFeedback) -> str:
        if feedback.kind == "image":
            return "图片"
        if feedback.kind == "text":
            return "文字"
        return "文件"
