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
        self.setMinimumHeight(118)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel()
        icon.setPixmap(
            self.style()
            .standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
            .pixmap(26, 26)
        )
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("拖拽或粘贴反馈材料到这里")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("支持截图、文字、DOCX、DOC、PDF、TXT、PNG、JPG、JPEG")
        hint.setObjectName("mutedText")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.browse_button = QPushButton("选择文件")
        self.browse_button.clicked.connect(self.browse_requested)
        layout.addWidget(self.browse_button, 0, Qt.AlignmentFlag.AlignHCenter)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            self.mime_received.emit(QGuiApplication.clipboard().mimeData())
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.browse_button.geometry().contains(event.position().toPoint())
        ):
            self.browse_requested.emit()
            event.accept()
            return
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
    preview_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(
        self,
        feedback: PendingFeedback,
        parent: QWidget | None = None,
        read_only: bool = False,
        allow_saved_delete: bool = False,
    ) -> None:
        super().__init__(parent)
        self.feedback = feedback
        self.remove_button: QPushButton | None = None
        self.setObjectName("pendingFeedbackRow")
        layout = QHBoxLayout(self)
        self.setMinimumHeight(66)
        layout.setContentsMargins(10, 7, 7, 7)
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
        detail = feedback.detail or feedback.preview or self._kind_text(feedback)
        preview = ElidedLabel(detail, mode=Qt.TextElideMode.ElideRight)
        preview.setObjectName("mutedText")
        text_layout.addWidget(preview)
        system_managed = bool(getattr(feedback, "system_managed", False))
        status_text = "系统批量说明" if system_managed else feedback.status
        self.status_label = QLabel(status_text)
        self.status_label.setObjectName("feedbackStatus")
        self.status_label.setProperty(
            "status", "error" if feedback.error else "ready"
        )
        self.status_label.setToolTip(
            "用于防串项目和批次识别，不作为普通反馈材料删除。"
            if system_managed
            else feedback.error
        )
        text_layout.addWidget(self.status_label)
        layout.addLayout(text_layout, 1)

        self.preview_button = QPushButton()
        self.preview_button.setProperty("role", "quiet")
        self.preview_button.setProperty("iconOnly", True)
        self.preview_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        )
        self.preview_button.setToolTip("预览反馈材料")
        self.preview_button.clicked.connect(
            lambda: self.preview_requested.emit(feedback.item_id)
        )
        layout.addWidget(self.preview_button)

        if feedback.kind == "text" and not read_only:
            edit = QPushButton()
            edit.setProperty("role", "quiet")
            edit.setProperty("iconOnly", True)
            edit.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
            )
            edit.setToolTip("编辑文字反馈")
            edit.clicked.connect(lambda: self.edit_requested.emit(feedback.item_id))
            layout.addWidget(edit)

        if not read_only or (allow_saved_delete and not system_managed):
            self.remove_button = QPushButton()
            self.remove_button.setProperty("role", "quiet")
            self.remove_button.setProperty("danger", True)
            self.remove_button.setProperty("iconOnly", True)
            self.remove_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
            )
            self.remove_button.setToolTip(
                "移入系统回收站并使绑定任务失效"
                if read_only
                else "移除待保存反馈"
            )
            self.remove_button.clicked.connect(
                lambda: self.remove_requested.emit(feedback.item_id)
            )
            layout.addWidget(self.remove_button)

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
