from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models import ProjectGroup
from services import BatchFeedbackService, FeedbackService, ProjectService, TaskService
from ui.widgets import BatchFeedbackPanel, Card


class BatchFeedbackPage(QWidget):
    back_requested = Signal()
    error_requested = Signal(str)
    toast_requested = Signal(str)
    feedback_saved = Signal()
    project_requested = Signal(str, int)

    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        feedback_service: FeedbackService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.group: ProjectGroup | None = None
        self.batch_service = BatchFeedbackService(
            project_service,
            feedback_service,
            task_service,
        )
        self._build_ui(feedback_service)

    def _build_ui(self, feedback_service: FeedbackService) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(12)

        header = Card()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 13, 18, 13)
        self.back_button = QPushButton("返回当前项目")
        self.back_button.clicked.connect(self.back_requested)
        header_layout.addWidget(self.back_button)
        title_column = QVBoxLayout()
        title = QLabel("批量反馈")
        title.setObjectName("pageTitle")
        title_column.addWidget(title)
        self.group_label = QLabel("当前项目组：未选择")
        self.group_label.setObjectName("mutedText")
        self.group_label.setWordWrap(True)
        title_column.addWidget(self.group_label)
        header_layout.addLayout(title_column, 1)
        root.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("page")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 4)
        content_layout.setSpacing(10)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(10)
        description = QLabel(
            "选择至少两个进行中课件，统一材料会分别保存到每个课件自己的下一轮。"
            "历史反馈请回到对应项目查看。"
        )
        description.setObjectName("mutedText")
        description.setWordWrap(True)
        card_layout.addWidget(description)
        self.panel = BatchFeedbackPanel(
            self.batch_service,
            feedback_service,
            card,
        )
        card_layout.addWidget(self.panel)
        content_layout.addWidget(card)
        content_layout.addStretch()
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)

        self.panel.error_requested.connect(self.error_requested)
        self.panel.toast_requested.connect(self.toast_requested)
        self.panel.feedback_saved.connect(self.feedback_saved)
        self.panel.project_requested.connect(self.project_requested)

    def set_group(self, group: ProjectGroup, preserve: bool = False) -> None:
        same_group = bool(
            self.group and self.group.root.resolve() == group.root.resolve()
        )
        self.group = group
        self.group_label.setText(
            f"当前项目组：{group.name} · {group.root.resolve()}"
        )
        self.panel.set_group(group, preserve=preserve and same_group)

    def clear_group(self) -> None:
        self.group = None
        self.group_label.setText("当前项目组：未选择")
        self.panel.clear_group()

    def has_unsaved_content(self) -> bool:
        return self.panel.has_unsaved_content()

    def discard_unsaved_content(self) -> None:
        self.panel.discard_unsaved_content()
