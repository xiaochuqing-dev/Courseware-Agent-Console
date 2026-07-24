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
        self, title: str, prompt: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.prompt = prompt
        self.setWindowTitle(title)
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        editor = QPlainTextEdit(prompt)
        editor.setReadOnly(True)
        layout.addWidget(editor, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        self.copy_button = QPushButton("复制验收 Prompt")
        self.copy_button.setProperty("role", "primary")
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.prompt)
        self.copy_button.setText("已复制")
