from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services import TaskService
from .elided_label import ElidedLabel


class RulesEditorDialog(QDialog):
    def __init__(
        self,
        group_root: Path,
        task_service: TaskService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.group_root = group_root
        self.task_service = task_service
        self.setWindowTitle("编辑任务规则")
        self.setModal(True)
        screen = parent.screen() if parent is not None else QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        target_width = min(780, max(420, available.width() - 64)) if available else 780
        target_height = min(650, max(360, available.height() - 64)) if available else 650
        self.setMinimumSize(min(540, target_width), min(400, target_height))
        self.resize(target_width, target_height)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)

        title = QLabel("AGENT 任务规则")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        path_label = ElidedLabel(str(group_root / "AGENT任务规则.md"))
        path_label.setObjectName("mutedText")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlainText(self.task_service.read_rules(group_root))
        layout.addWidget(self.editor, 1)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("恢复默认")
        self.reset_button.clicked.connect(self._restore_default)
        actions.addWidget(self.reset_button)
        actions.addStretch()

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        actions.addWidget(cancel_button)

        save_button = QPushButton("保存")
        save_button.setProperty("role", "primary")
        save_button.clicked.connect(self._save)
        actions.addWidget(save_button)
        layout.addLayout(actions)

    def _save(self) -> None:
        try:
            self.task_service.save_rules(self.group_root, self.editor.toPlainText())
        except Exception as exc:
            QMessageBox.warning(
                self,
                "规则未保存",
                f"无法写入任务规则：{self.group_root / 'AGENT任务规则.md'}\n\n{exc}",
            )
            return
        self.accept()

    def _restore_default(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复默认规则",
            "将使用内置模板覆盖当前规则，且无法自动撤销。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.editor.setPlainText(self.task_service.restore_default_rules(self.group_root))
