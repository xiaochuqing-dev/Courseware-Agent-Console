from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PromptDialog(QDialog):
    def __init__(
        self,
        title: str,
        prompt: str,
        parent: QWidget | None = None,
        copy_button_text: str = "复制 Prompt",
        copy_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.prompt = prompt if copy_text is None else copy_text
        self.setWindowTitle(title)
        screen = parent.screen() if parent is not None else QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        target_width = min(760, max(420, available.width() - 64)) if available else 760
        target_height = min(620, max(360, available.height() - 64)) if available else 620
        self.setMinimumSize(min(520, target_width), min(380, target_height))
        self.resize(target_width, target_height)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self.editor = QPlainTextEdit(prompt)
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        self.copy_button = QPushButton(copy_button_text)
        self.copy_button.setProperty("role", "primary")
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.prompt)
        self.copy_button.setText("已复制")
