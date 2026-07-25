from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from models import ProjectEntry, ProjectGroup
from services import (
    AcceptanceService,
    ArchiveService,
    FeedbackService,
    PendingFeedback,
    ProjectService,
    PromptService,
    TaskService,
)
from ui.widgets import (
    AcceptanceDialog,
    Card,
    ElidedLabel,
    FeedbackDropArea,
    FlowLayout,
    PendingFeedbackRow,
    PromptDialog,
)


class HomePage(QWidget):
    create_project_requested = Signal()
    choose_group_requested = Signal()
    edit_rules_requested = Signal()
    archive_requested = Signal()
    completed_projects_requested = Signal()
    workflow_optimization_requested = Signal()
    toast_requested = Signal(str)
    error_requested = Signal(str)
    project_selected = Signal(object, str)
    group_switch_requested = Signal(object)
    delete_group_requested = Signal(object)

    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        feedback_service: FeedbackService | None = None,
        archive_service: ArchiveService | None = None,
        prompt_service: PromptService | None = None,
        acceptance_service: AcceptanceService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.project_service = project_service
        self.task_service = task_service
        self.feedback_service = feedback_service or FeedbackService()
        self.archive_service = archive_service or ArchiveService()
        self.prompt_service = prompt_service or PromptService()
        self.acceptance_service = acceptance_service or AcceptanceService(
            project_service, self.archive_service, self.feedback_service
        )
        self.group: ProjectGroup | None = None
        self.available_groups: tuple[Path, ...] = ()
        self.current_project: ProjectEntry | None = None
        self.pending_feedback: list[PendingFeedback] = []
        self.saved_feedback: list[PendingFeedback] = []
        self.current_task_content = ""
        self._prompt_dialog: PromptDialog | None = None
        self._acceptance_dialog: AcceptanceDialog | None = None
        self._build_ui()
        self.set_empty_state()

    def _build_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 20)
        root_layout.setSpacing(14)

        sidebar = Card()
        sidebar.setFixedWidth(206)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 17, 15, 17)
        sidebar_layout.setSpacing(12)

        brand = QLabel("课件 Agent 控制台")
        brand.setObjectName("sectionTitle")
        sidebar_layout.addWidget(brand)
        version_label = QLabel("v1.0.0")
        version_label.setObjectName("versionText")
        sidebar_layout.addWidget(version_label)

        create_button = QPushButton("创建项目")
        create_button.setProperty("role", "primary")
        create_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder)
        )
        create_button.clicked.connect(self.create_project_requested)
        sidebar_layout.addWidget(create_button)

        projects_label = QLabel("进行中项目")
        projects_label.setObjectName("fieldLabel")
        sidebar_layout.addWidget(projects_label)

        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.currentItemChanged.connect(self._on_project_selected)
        sidebar_layout.addWidget(self.project_list, 1)

        self.workflow_button = QPushButton("工作流优化")
        self.workflow_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        )
        self.workflow_button.clicked.connect(self.workflow_optimization_requested)
        sidebar_layout.addWidget(self.workflow_button)

        self.completed_button = QPushButton("已完成项目")
        self.completed_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.completed_button.clicked.connect(self.completed_projects_requested)
        sidebar_layout.addWidget(self.completed_button)
        root_layout.addWidget(sidebar)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)

        header_card = Card()
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(18, 11, 18, 11)
        header_layout.setSpacing(7)

        top_row = QHBoxLayout()
        group_label = QLabel("当前项目组")
        group_label.setObjectName("fieldLabel")
        top_row.addWidget(group_label)
        self.group_selector = QComboBox()
        self.group_selector.setMinimumWidth(190)
        self.group_selector.activated.connect(self._group_selector_activated)
        top_row.addWidget(self.group_selector)
        self.refresh_button = QPushButton()
        self.refresh_button.setProperty("iconOnly", True)
        self.refresh_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.refresh_button.setToolTip("刷新项目文件信息")
        self.refresh_button.clicked.connect(self.refresh_group)
        top_row.addWidget(self.refresh_button)
        top_row.addStretch()
        self.edit_rules_button = QPushButton("编辑任务规则")
        self.edit_rules_button.clicked.connect(self.edit_rules_requested)
        top_row.addWidget(self.edit_rules_button)

        self.archive_button = QPushButton("标记已完成 / 归档")
        self.archive_button.clicked.connect(self.archive_requested)
        top_row.addWidget(self.archive_button)
        self.delete_group_button = QPushButton()
        self.delete_group_button.setProperty("iconOnly", True)
        self.delete_group_button.setProperty("danger", True)
        self.delete_group_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self.delete_group_button.setToolTip("移除或删除当前项目组")
        self.delete_group_button.clicked.connect(
            lambda: self.delete_group_requested.emit(self.group.root)
            if self.group
            else None
        )
        top_row.addWidget(self.delete_group_button)
        header_layout.addLayout(top_row)

        path_row = QHBoxLayout()
        path_title = QLabel("根目录")
        path_title.setObjectName("fieldLabel")
        path_row.addWidget(path_title)
        self.root_path_label = ElidedLabel()
        self.root_path_label.setObjectName("mutedText")
        self.root_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_row.addWidget(self.root_path_label, 1)
        self.open_root_button = QPushButton()
        self.open_root_button.setProperty("iconOnly", True)
        self.open_root_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.open_root_button.clicked.connect(self._open_group_root)
        self.open_root_button.setToolTip("打开项目组根目录")
        path_row.addWidget(self.open_root_button)
        header_layout.addLayout(path_row)
        main_layout.addWidget(header_card)

        self.content_stack = QStackedWidget()
        empty_page = QWidget()
        empty_page.setObjectName("page")
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.setContentsMargins(42, 36, 42, 36)
        empty_layout.setSpacing(12)
        empty_layout.addStretch()
        self.empty_title = QLabel("课件项目")
        self.empty_title.setObjectName("pageTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_message = QLabel("尚未选择项目组")
        self.empty_message.setObjectName("mutedText")
        self.empty_message.setWordWrap(True)
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_message)

        empty_actions = QHBoxLayout()
        empty_actions.addStretch()
        self.empty_create_button = QPushButton("创建新项目")
        self.empty_create_button.setProperty("role", "primary")
        self.empty_create_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder)
        )
        self.empty_create_button.clicked.connect(self.create_project_requested)
        empty_actions.addWidget(self.empty_create_button)
        self.empty_select_button = QPushButton("选择已有项目组")
        self.empty_select_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.empty_select_button.clicked.connect(self.choose_group_requested)
        empty_actions.addWidget(self.empty_select_button)
        self.empty_completed_button = QPushButton("查看已完成项目")
        self.empty_completed_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.empty_completed_button.clicked.connect(self.completed_projects_requested)
        empty_actions.addWidget(self.empty_completed_button)
        empty_actions.addStretch()
        empty_layout.addLayout(empty_actions)
        empty_layout.addStretch()
        self.content_stack.addWidget(empty_page)

        self.work_scroll = QScrollArea()
        self.work_scroll.setWidgetResizable(True)
        self.work_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_content = QWidget()
        scroll_content.setObjectName("page")
        work_layout = QVBoxLayout(scroll_content)
        work_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        work_layout.setContentsMargins(0, 0, 8, 4)
        work_layout.setSpacing(10)
        self.task_card = self._build_task_card()
        self.feedback_card = self._build_feedback_card()
        work_layout.addWidget(self.task_card)
        work_layout.addWidget(self.feedback_card)
        work_layout.addStretch()
        self.work_scroll.setWidget(scroll_content)
        self.content_stack.addWidget(self.work_scroll)
        main_layout.addWidget(self.content_stack, 1)
        root_layout.addLayout(main_layout, 1)

    def _build_task_card(self) -> Card:
        task_card = Card()
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(20, 14, 20, 14)
        task_layout.setSpacing(9)

        title_row = QHBoxLayout()
        task_title = QLabel("当前任务")
        task_title.setObjectName("sectionTitle")
        title_row.addWidget(task_title)
        self.current_project_label = QLabel()
        self.current_project_label.setObjectName("taskProjectName")
        title_row.addWidget(self.current_project_label)
        title_row.addStretch()
        self.latest_product_label = ElidedLabel("最新产品：无")
        self.latest_product_label.setObjectName("mutedText")
        title_row.addWidget(self.latest_product_label)
        task_layout.addLayout(title_row)

        details_row = QHBoxLayout()
        details_row.setSpacing(12)

        mode_column = QVBoxLayout()
        mode_label = QLabel("任务类型")
        mode_label.setObjectName("fieldLabel")
        mode_column.addWidget(mode_label)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        self.task_mode_group = QButtonGroup(self)
        self.task_mode_group.setExclusive(True)
        self.first_build_button = QPushButton("首次制作")
        self.feedback_task_button = QPushButton("反馈修改")
        for index, button in enumerate(
            (self.first_build_button, self.feedback_task_button)
        ):
            button.setCheckable(True)
            button.setProperty("taskMode", True)
            self.task_mode_group.addButton(button, index)
            mode_row.addWidget(button)
        self.first_build_button.setChecked(True)
        self.task_mode_group.buttonClicked.connect(self._on_task_mode_changed)
        mode_column.addLayout(mode_row)
        details_row.addLayout(mode_column)

        self.round_widget = QWidget()
        round_layout = QVBoxLayout(self.round_widget)
        round_layout.setContentsMargins(0, 0, 0, 0)
        round_label = QLabel("反馈轮次")
        round_label.setObjectName("fieldLabel")
        round_layout.addWidget(round_label)
        self.feedback_round_combo = QComboBox()
        self.feedback_round_combo.setMinimumWidth(130)
        self.feedback_round_combo.currentIndexChanged.connect(
            self._update_task_mode_state
        )
        round_layout.addWidget(self.feedback_round_combo)
        details_row.addWidget(self.round_widget)
        details_row.addStretch(1)
        self.tools_binding_label = QLabel("工具绑定：未检查")
        self.tools_binding_label.setObjectName("toolBindingStatus")
        self.tools_binding_label.setWordWrap(True)
        details_row.addWidget(self.tools_binding_label)
        self.round_widget.hide()
        task_layout.addLayout(details_row)

        task_body = QHBoxLayout()
        task_body.setSpacing(14)
        requirements_column = QVBoxLayout()
        requirements_label = QLabel("特殊要求（可留空）")
        requirements_label.setObjectName("fieldLabel")
        requirements_column.addWidget(requirements_label)
        self.requirements_input = QPlainTextEdit()
        self.requirements_input.setPlaceholderText("仅填写本次额外约束")
        self.requirements_input.setMinimumHeight(62)
        self.requirements_input.setMaximumHeight(76)
        self.requirements_input.setMaximumWidth(760)
        requirements_column.addWidget(self.requirements_input)
        task_body.addLayout(requirements_column, 3)

        task_state_column = QVBoxLayout()
        task_state_column.setSpacing(5)
        task_state_label = QLabel("任务状态")
        task_state_label.setObjectName("fieldLabel")
        task_state_column.addWidget(task_state_label)
        self.task_status_text = QLabel("尚未生成当前任务")
        self.task_status_text.setObjectName("mutedText")
        self.task_status_text.setWordWrap(True)
        task_state_column.addWidget(self.task_status_text)
        self.acceptance_status_label = QLabel("验收状态：未验收")
        self.acceptance_status_label.setObjectName("mutedText")
        task_state_column.addWidget(self.acceptance_status_label)
        self.task_preview_button = QPushButton("查看当前任务")
        self.task_preview_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        )
        self.task_preview_button.clicked.connect(self._show_task_preview)
        self.task_preview_button.setEnabled(False)
        task_state_column.addWidget(self.task_preview_button)
        task_body.addLayout(task_state_column, 1)
        task_layout.addLayout(task_body)

        self.feedback_task_hint = QLabel("当前项目还没有客户反馈，请先导入反馈。")
        self.feedback_task_hint.setObjectName("errorBanner")
        self.feedback_task_hint.hide()
        task_layout.addWidget(self.feedback_task_hint)

        actions = FlowLayout(horizontal_spacing=8, vertical_spacing=8)
        self.open_project_button = QPushButton("打开项目文件夹")
        self.open_project_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.open_project_button.clicked.connect(self._open_current_project)
        actions.addWidget(self.open_project_button)

        self.acceptance_button = QPushButton("完整验收（可选）")
        self.acceptance_button.setToolTip(
            "多轮修改或交付前使用，日常小改无需执行"
        )
        self.acceptance_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.acceptance_button.clicked.connect(self._show_acceptance_prompt)
        actions.addWidget(self.acceptance_button)

        self.record_button = QPushButton("打开项目记录")
        self.record_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        )
        self.record_button.clicked.connect(self._open_project_record)
        actions.addWidget(self.record_button)

        self.generate_button = QPushButton("生成当前任务")
        self.generate_button.setProperty("role", "primary")
        self.generate_button.clicked.connect(self._generate_task)
        actions.addWidget(self.generate_button)
        task_layout.addLayout(actions)
        return task_card

    def _build_feedback_card(self) -> Card:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 19, 24, 19)
        layout.setSpacing(11)

        header = QHBoxLayout()
        title = QLabel("客户反馈导入")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self.latest_feedback_label = QLabel("尚无反馈轮次")
        self.latest_feedback_label.setObjectName("mutedText")
        header.addWidget(self.latest_feedback_label)
        layout.addLayout(header)

        self.feedback_drop_area = FeedbackDropArea()
        self.feedback_drop_area.mime_received.connect(self._receive_mime_data)
        self.feedback_drop_area.browse_requested.connect(self._choose_feedback_files)
        layout.addWidget(self.feedback_drop_area)

        pending_header = QHBoxLayout()
        pending_label = QLabel("待保存")
        pending_label.setObjectName("fieldLabel")
        pending_header.addWidget(pending_label)
        self.pending_count_label = QLabel("0 项")
        self.pending_count_label.setObjectName("mutedText")
        pending_header.addWidget(self.pending_count_label)
        pending_header.addStretch()
        layout.addLayout(pending_header)

        self.pending_scroll = QScrollArea()
        self.pending_scroll.setWidgetResizable(True)
        self.pending_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.pending_scroll.setMinimumHeight(58)
        self.pending_scroll.setMaximumHeight(172)
        self.pending_container = QWidget()
        self.pending_layout = QVBoxLayout(self.pending_container)
        self.pending_layout.setContentsMargins(0, 0, 0, 0)
        self.pending_layout.setSpacing(5)
        self.pending_scroll.setWidget(self.pending_container)
        layout.addWidget(self.pending_scroll)

        self.pending_empty_label = QLabel("粘贴或拖入的资料会先显示在这里，不会立即写入项目。")
        self.pending_empty_label.setObjectName("mutedText")
        self.pending_layout.addWidget(self.pending_empty_label)

        save_actions = QHBoxLayout()
        save_actions.addStretch()
        self.append_round_button = QPushButton()
        self.append_round_button.clicked.connect(self._append_to_latest_round)
        save_actions.addWidget(self.append_round_button)
        self.new_round_button = QPushButton()
        self.new_round_button.setProperty("role", "primary")
        self.new_round_button.clicked.connect(self._save_to_new_round)
        save_actions.addWidget(self.new_round_button)
        layout.addLayout(save_actions)

        saved_header = QHBoxLayout()
        saved_label = QLabel("已保存反馈")
        saved_label.setObjectName("fieldLabel")
        saved_header.addWidget(saved_label)
        self.saved_count_label = QLabel("0 项")
        self.saved_count_label.setObjectName("mutedText")
        saved_header.addWidget(self.saved_count_label)
        saved_header.addStretch()
        layout.addLayout(saved_header)

        self.saved_scroll = QScrollArea()
        self.saved_scroll.setWidgetResizable(True)
        self.saved_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.saved_scroll.setMinimumHeight(54)
        self.saved_scroll.setMaximumHeight(152)
        self.saved_container = QWidget()
        self.saved_layout = QVBoxLayout(self.saved_container)
        self.saved_layout.setContentsMargins(0, 0, 0, 0)
        self.saved_layout.setSpacing(5)
        self.saved_scroll.setWidget(self.saved_container)
        layout.addWidget(self.saved_scroll)
        self._refresh_saved_list()
        return card

    def set_group(self, group: ProjectGroup, preferred_project: str | None = None) -> None:
        self.group = group
        self.current_project = None
        self.pending_feedback.clear()
        self.saved_feedback.clear()
        self._refresh_pending_list()
        self._refresh_saved_list()
        self._populate_group_selector()
        self.root_path_label.setText(str(group.root))
        self.root_path_label.setToolTip(str(group.root))
        for widget in (
            self.edit_rules_button,
            self.open_root_button,
            self.refresh_button,
            self.completed_button,
            self.workflow_button,
            self.delete_group_button,
        ):
            widget.setEnabled(True)

        self.project_list.clear()
        selected_row = 0
        for row, project in enumerate(group.projects):
            item = QListWidgetItem(project.name)
            item.setData(Qt.ItemDataRole.UserRole, str(project.path))
            self.project_list.addItem(item)
            if project.name == preferred_project:
                selected_row = row
        if self.project_list.count():
            self.content_stack.setCurrentIndex(1)
            self.project_list.setCurrentRow(selected_row)
        else:
            self.archive_button.setEnabled(False)
            self.empty_title.setText("当前项目组没有进行中项目")
            self.empty_message.setText(group.name)
            self.empty_create_button.hide()
            self.empty_select_button.show()
            self.empty_completed_button.show()
            self.content_stack.setCurrentIndex(0)

    def set_empty_state(self, message: str | None = None) -> None:
        self.group = None
        self.current_project = None
        self.current_task_content = ""
        self.pending_feedback.clear()
        self.saved_feedback.clear()
        self._refresh_pending_list()
        self._refresh_saved_list()
        self.project_list.clear()
        self._populate_group_selector()
        self.root_path_label.setText("尚未选择项目组")
        self.root_path_label.setToolTip("")
        for widget in (
            self.edit_rules_button,
            self.open_root_button,
            self.refresh_button,
            self.completed_button,
            self.workflow_button,
            self.archive_button,
            self.open_project_button,
            self.task_preview_button,
            self.acceptance_button,
            self.record_button,
            self.delete_group_button,
        ):
            widget.setEnabled(False)
        self.empty_title.setText("课件项目")
        self.empty_message.setText(message or "尚未选择项目组")
        self.empty_create_button.show()
        self.empty_select_button.show()
        self.empty_completed_button.hide()
        self.content_stack.setCurrentIndex(0)
        self.task_status_text.setText("尚未生成当前任务")
        self.current_project_label.clear()
        self.tools_binding_label.setText("工具绑定：未选择项目组")
        self.acceptance_status_label.setText("验收状态：未验收")
        self._update_task_mode_state()

    def set_available_groups(self, group_paths: tuple[Path, ...]) -> None:
        self.available_groups = tuple(Path(path).resolve() for path in group_paths)
        self._populate_group_selector()

    def _populate_group_selector(self) -> None:
        current_path = self.group.root.resolve() if self.group else None
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        selected_index = -1
        for path in self.available_groups:
            index = self.group_selector.count()
            self.group_selector.addItem(path.name, str(path))
            self.group_selector.setItemData(index, str(path), Qt.ItemDataRole.ToolTipRole)
            if current_path is not None and path == current_path:
                selected_index = index
        if not self.available_groups:
            self.group_selector.addItem("未选择项目组", "")
            selected_index = 0
        self.group_selector.addItem("选择已有项目组…", None)
        self.group_selector.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.group_selector.blockSignals(False)

    def refresh_group(self) -> None:
        if not self.group:
            return
        if not self.group.root.is_dir():
            return
        preferred = self.current_project.name if self.current_project else None
        pending = list(self.pending_feedback)
        try:
            group = self.project_service.load_project_group(self.group.root)
        except Exception as exc:
            self.error_requested.emit(f"刷新项目组失败：{exc}")
            return
        self.set_group(group, preferred)
        if preferred and self.current_project and self.current_project.name == preferred:
            self.pending_feedback = pending
            self._refresh_pending_list()

    def refresh_current_project(self) -> None:
        if not self.current_project:
            return
        if not self.current_project.path.is_dir():
            return
        self._refresh_project_state()

    def _group_selector_activated(self, index: int) -> None:
        data = self.group_selector.itemData(index)
        if data is None:
            self._populate_group_selector()
            self.choose_group_requested.emit()
            return
        if not data:
            return
        path = Path(str(data)).resolve()
        if not self.group or path != self.group.root.resolve():
            self.group_switch_requested.emit(path)

    def _on_project_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if not current or not self.group:
            self.current_project = None
            self.current_task_content = ""
            self.archive_button.setEnabled(False)
            self.task_preview_button.setEnabled(False)
            self.acceptance_button.setEnabled(False)
            self.record_button.setEnabled(False)
            self.task_status_text.setText("尚未生成当前任务")
            self._update_task_mode_state()
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        next_project = next(
            (project for project in self.group.projects if project.path == path), None
        )
        if not next_project:
            return
        if self.current_project and self.current_project.path != next_project.path:
            self.pending_feedback.clear()
            self._refresh_pending_list()
        self.current_project = next_project
        self.current_project_label.setText(next_project.name)
        self.requirements_input.clear()
        self.archive_button.setEnabled(True)
        self.open_project_button.setEnabled(True)
        self.acceptance_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self._refresh_project_state()
        self.project_selected.emit(self.group.root, next_project.name)

    def _refresh_project_state(self) -> None:
        if not self.current_project:
            return
        task_path = self.current_project.path / "当前任务.md"
        task_content = ""
        if task_path.is_file():
            try:
                task_content = task_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                self.error_requested.emit(
                    f"无法读取当前任务：{task_path}\n请检查文件权限和编码。\n\n{exc}"
                )
        self.current_task_content = task_content
        has_task = bool(task_content.strip())
        self.task_preview_button.setEnabled(has_task)
        self.task_status_text.setText(self._task_status(task_content))

        try:
            if not self.group:
                raise ValueError("项目组未加载")
            self.project_service.validate_group_resources(self.group.root)
            self.tools_binding_label.setText("工具绑定：workflow / template / validate 已核对")
            self.tools_binding_label.setProperty("status", "ready")
            self.tools_binding_label.setToolTip(str(self.group.root / "项目组配置.json"))
        except Exception as exc:
            self.tools_binding_label.setText("工具绑定：不可用于生成或验收")
            self.tools_binding_label.setProperty("status", "error")
            self.tools_binding_label.setToolTip(str(exc))
        self.tools_binding_label.style().unpolish(self.tools_binding_label)
        self.tools_binding_label.style().polish(self.tools_binding_label)

        acceptance = self.acceptance_service.latest_report(self.current_project.path)
        if not acceptance:
            self.acceptance_status_label.setText("验收状态：未验收")
        elif self.acceptance_service.has_current_passing_report(self.current_project.path):
            self.acceptance_status_label.setText("验收状态：已通过")
        else:
            self.acceptance_status_label.setText("验收状态：未通过或已过期")

        latest_product = self.archive_service.latest_product(self.current_project.path)
        self.latest_product_label.setText(
            f"最新产品：{latest_product.name}" if latest_product else "最新产品：无"
        )
        rounds = self.feedback_service.scan_rounds(self.current_project.path)
        self.feedback_round_combo.blockSignals(True)
        self.feedback_round_combo.clear()
        for number in rounds:
            self.feedback_round_combo.addItem(f"第{number}轮", number)
        if rounds:
            self.feedback_round_combo.setCurrentIndex(len(rounds) - 1)
            self.latest_feedback_label.setText(f"当前最新反馈：第{rounds[-1]}轮")
            self.saved_feedback = list(
                self.feedback_service.saved_items(
                    self.current_project.path, rounds[-1]
                )
            )
        else:
            self.latest_feedback_label.setText("尚无反馈轮次")
            self.saved_feedback = []
        self.feedback_round_combo.blockSignals(False)
        self._refresh_saved_list()
        self._update_feedback_actions()
        self._update_task_mode_state()

    def _on_task_mode_changed(self) -> None:
        self._update_task_mode_state()

    def _update_task_mode_state(self) -> None:
        feedback_mode = self.task_mode_group.checkedId() == 1
        self.round_widget.setVisible(feedback_mode)
        no_rounds = self.feedback_round_combo.count() == 0
        self.feedback_task_hint.setVisible(feedback_mode and no_rounds)
        self.generate_button.setEnabled(
            self.current_project is not None and (not feedback_mode or not no_rounds)
        )
        if self._task_matches_selected_mode():
            self.generate_button.setText("重新生成任务")
        elif feedback_mode:
            self.generate_button.setText("生成反馈修改任务")
        else:
            self.generate_button.setText("生成当前任务")

    def _task_matches_selected_mode(self) -> bool:
        content = self.current_task_content
        if not content.strip():
            return False
        if self.task_mode_group.checkedId() == 0:
            return "任务类型：首次制作" in content
        round_number = self.feedback_round_combo.currentData()
        return (
            round_number is not None
            and "任务类型：反馈修改" in content
            and f"反馈轮次：第{int(round_number)}轮" in content
        )

    @staticmethod
    def _task_status(content: str) -> str:
        if not content.strip():
            return "尚未生成当前任务"
        task_type = "反馈修改" if "任务类型：反馈修改" in content else "首次制作"
        if task_type == "反馈修改":
            round_line = next(
                (line for line in content.splitlines() if line.startswith("反馈轮次：")),
                "",
            )
            if round_line:
                return f"已有任务：{task_type} · {round_line.removeprefix('反馈轮次：')}"
        return f"已有任务：{task_type}"

    def _generate_task(self) -> None:
        if not self.current_project:
            self.error_requested.emit("请先选择项目。")
            return
        regenerating = self._task_matches_selected_mode()
        try:
            if not self.group:
                raise ValueError("当前项目组未加载。")
            self.project_service.validate_project_structure(
                self.group.root, self.current_project.path
            )
            if self.task_mode_group.checkedId() == 1:
                round_number = self.feedback_round_combo.currentData()
                if round_number is None:
                    raise ValueError("当前项目还没有客户反馈，请先导入反馈。")
                self.task_service.generate_feedback_task(
                    self.current_project.path,
                    int(round_number),
                    self.requirements_input.toPlainText(),
                )
            else:
                self.task_service.generate_first_build_task(
                    self.current_project.path,
                    self.requirements_input.toPlainText(),
                )
        except Exception as exc:
            self.error_requested.emit(f"生成当前任务失败：{exc}")
            return
        self._refresh_project_state()
        self.toast_requested.emit("任务已重新生成" if regenerating else "当前任务已生成")

    def _show_task_preview(self) -> None:
        if not self.current_project or not self.current_task_content.strip():
            return
        prompt = self.prompt_service.execution_prompt(self.current_project.name)
        self._prompt_dialog = PromptDialog(
            "当前任务预览",
            self.current_task_content,
            self,
            "复制提示词（可选）",
            copy_text=prompt,
        )
        self._prompt_dialog.exec()

    def _show_acceptance_prompt(self) -> None:
        if not self.current_project or not self.group:
            return
        try:
            report = self.acceptance_service.run(
                self.group.root, self.current_project.path
            )
        except Exception as exc:
            self.error_requested.emit(f"完整产品验收无法执行：{exc}")
            return
        self._acceptance_dialog = AcceptanceDialog(report, self)
        self._acceptance_dialog.rerun_requested.connect(
            lambda: self._rerun_acceptance(self._acceptance_dialog)
        )
        self._acceptance_dialog.exec()
        self._refresh_project_state()

    def _rerun_acceptance(self, dialog: AcceptanceDialog | None) -> None:
        if dialog is not None:
            dialog.accept()
        self._show_acceptance_prompt()

    def _choose_feedback_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择客户反馈文件",
            "",
            "支持的反馈材料 (*.pdf *.txt *.png *.jpg *.jpeg)",
        )
        if selected:
            self._add_feedback_files([Path(path) for path in selected])

    def _receive_mime_data(self, mime) -> None:
        if mime.hasUrls():
            files = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
            if files:
                self._add_feedback_files(files)
                return
        if mime.hasImage():
            image_data = mime.imageData()
            if isinstance(image_data, QPixmap):
                image_data = image_data.toImage()
            if isinstance(image_data, QImage):
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not image_data.save(buffer, "PNG"):
                    self.error_requested.emit("剪贴板图片无法转换为 PNG。")
                    return
                name = f"微信截图-{datetime.now():%Y%m%d-%H%M%S}.png"
                try:
                    self._append_pending(
                        self.feedback_service.pending_from_bytes(
                            name,
                            bytes(buffer.data()),
                            "image",
                            self._pending_names(),
                        )
                    )
                except Exception as exc:
                    self.error_requested.emit(f"无法接收剪贴板图片：{exc}")
                return
        if mime.hasText():
            try:
                self._append_pending(
                    self.feedback_service.pending_from_text(
                        mime.text(), self._pending_names()
                    )
                )
            except Exception as exc:
                self.error_requested.emit(f"无法接收剪贴板文字：{exc}")
            return
        self.error_requested.emit("剪贴板中没有可导入的图片、文字或文件。")

    def _add_feedback_files(self, paths: list[Path]) -> None:
        errors: list[str] = []
        fingerprints = {
            item.fingerprint for item in self.pending_feedback if item.fingerprint
        }
        for path in paths:
            try:
                item = self.feedback_service.pending_from_file(path, self._pending_names())
            except Exception as exc:
                errors.append(f"{path.name or path}：{exc}")
                continue
            if item.fingerprint and item.fingerprint in fingerprints:
                errors.append(f"{path.name}：内容与待保存列表中的材料重复，已忽略。")
                continue
            self.pending_feedback.append(item)
            fingerprints.add(item.fingerprint)
        self._refresh_pending_list()
        if errors:
            self.error_requested.emit("部分资料未加入：\n" + "\n".join(errors))

    def _append_pending(self, item: PendingFeedback) -> None:
        if item.fingerprint and any(
            existing.fingerprint == item.fingerprint
            for existing in self.pending_feedback
        ):
            self.error_requested.emit("该内容已在待保存列表中，未重复添加。")
            return
        self.pending_feedback.append(item)
        self._refresh_pending_list()

    def _pending_names(self) -> set[str]:
        return {item.name for item in self.pending_feedback}

    def _remove_pending(self, item_id: str) -> None:
        item = self._pending_item(item_id)
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "确认移除待保存反馈",
            f"将从待保存列表移除：\n{item.name}\n\n"
            "该材料尚未写入项目。移除后需要重新粘贴、拖拽或选择才能恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.pending_feedback = [
            item for item in self.pending_feedback if item.item_id != item_id
        ]
        self._refresh_pending_list()

    def _refresh_pending_list(self) -> None:
        while self.pending_layout.count():
            layout_item = self.pending_layout.takeAt(0)
            if layout_item.widget():
                layout_item.widget().hide()
                layout_item.widget().deleteLater()
        if not self.pending_feedback:
            self.pending_empty_label = QLabel(
                "粘贴或拖入的资料会先显示在这里，不会立即写入项目。"
            )
            self.pending_empty_label.setObjectName("mutedText")
            self.pending_layout.addWidget(self.pending_empty_label)
        else:
            for feedback in self.pending_feedback:
                row = PendingFeedbackRow(feedback)
                row.remove_requested.connect(self._remove_pending)
                row.preview_requested.connect(self._preview_pending)
                row.edit_requested.connect(self._edit_pending)
                self.pending_layout.addWidget(row)
        self.pending_count_label.setText(f"{len(self.pending_feedback)} 项")
        self._update_feedback_actions()

    def _pending_item(self, item_id: str) -> PendingFeedback | None:
        return next(
            (item for item in self.pending_feedback if item.item_id == item_id),
            None,
        )

    def _feedback_item(self, item_id: str) -> PendingFeedback | None:
        return next(
            (
                item
                for item in [*self.pending_feedback, *self.saved_feedback]
                if item.item_id == item_id
            ),
            None,
        )

    def _refresh_saved_list(self) -> None:
        if not hasattr(self, "saved_layout"):
            return
        while self.saved_layout.count():
            layout_item = self.saved_layout.takeAt(0)
            if layout_item.widget():
                layout_item.widget().hide()
                layout_item.widget().deleteLater()
        if not self.saved_feedback:
            empty = QLabel("最新反馈轮次尚无已保存材料。")
            empty.setObjectName("mutedText")
            self.saved_layout.addWidget(empty)
        else:
            for feedback in self.saved_feedback:
                row = PendingFeedbackRow(feedback, read_only=True)
                row.preview_requested.connect(self._preview_pending)
                self.saved_layout.addWidget(row)
        self.saved_count_label.setText(f"{len(self.saved_feedback)} 项")

    def _preview_pending(self, item_id: str) -> None:
        item = self._feedback_item(item_id)
        if item is None:
            return
        if item.kind == "text":
            try:
                if item.content is not None:
                    text = item.content.decode("utf-8-sig")
                elif item.source_path is not None:
                    text = item.source_path.read_text(encoding="utf-8-sig")
                else:
                    text = item.preview
            except Exception as exc:
                self.error_requested.emit(f"无法预览 {item.name}：{exc}")
                return
            self._prompt_dialog = PromptDialog(item.name, text, self, "复制全文")
            self._prompt_dialog.exec()
            return
        if item.source_path is not None:
            self._open_path(item.source_path)
            return
        if item.kind == "image" and item.content:
            pixmap = QPixmap()
            pixmap.loadFromData(item.content)
            box = QMessageBox(self)
            box.setWindowTitle(item.name)
            box.setText(item.detail)
            box.setIconPixmap(
                pixmap.scaled(
                    640,
                    420,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            box.exec()

    def _edit_pending(self, item_id: str) -> None:
        item = self._pending_item(item_id)
        if item is None or item.kind != "text":
            return
        try:
            original = (
                item.content.decode("utf-8-sig")
                if item.content is not None
                else item.source_path.read_text(encoding="utf-8-sig")
                if item.source_path is not None
                else item.preview
            )
        except Exception as exc:
            self.error_requested.emit(f"无法编辑 {item.name}：{exc}")
            return
        updated, accepted = QInputDialog.getMultiLineText(
            self, "编辑文字反馈", item.name, original
        )
        if not accepted:
            return
        try:
            parsed = self.feedback_service.pending_from_text(updated)
        except Exception as exc:
            self.error_requested.emit(f"文字反馈未更新：{exc}")
            return
        replacement = replace(
            item,
            source_path=None,
            content=parsed.content,
            preview=parsed.preview,
            size_bytes=parsed.size_bytes,
            detail=parsed.detail,
            fingerprint=parsed.fingerprint,
        )
        self.pending_feedback = [
            replacement if current.item_id == item_id else current
            for current in self.pending_feedback
        ]
        self._refresh_pending_list()

    def _update_feedback_actions(self) -> None:
        latest = (
            self.feedback_service.latest_round(self.current_project.path)
            if self.current_project
            else None
        )
        has_pending = bool(self.pending_feedback) and self.current_project is not None
        if latest is None:
            self.append_round_button.hide()
            self.new_round_button.setText("保存为第1轮")
        else:
            self.append_round_button.show()
            self.append_round_button.setText(f"追加到第{latest}轮")
            self.new_round_button.setText(f"创建并保存为第{latest + 1}轮")
            self.append_round_button.setEnabled(has_pending)
        self.new_round_button.setEnabled(has_pending)

    def _append_to_latest_round(self) -> None:
        if not self.current_project:
            return
        latest = self.feedback_service.latest_round(self.current_project.path)
        if latest is not None:
            self._save_feedback(latest)

    def _save_to_new_round(self) -> None:
        if not self.current_project:
            return
        latest = self.feedback_service.latest_round(self.current_project.path)
        self._save_feedback(1 if latest is None else latest + 1)

    def _save_feedback(self, round_number: int) -> None:
        if not self.current_project or not self.pending_feedback:
            return
        try:
            result = self.feedback_service.save_pending(
                self.current_project.path, round_number, self.pending_feedback
            )
        except Exception as exc:
            self.error_requested.emit(f"保存客户反馈失败：{exc}")
            return
        saved = set(result.saved_item_ids)
        self.pending_feedback = [
            item for item in self.pending_feedback if item.item_id not in saved
        ]
        self._refresh_pending_list()
        self._refresh_project_state()
        if result.saved_paths:
            self.toast_requested.emit(
                f"已保存 {len(result.saved_paths)} 项到第{round_number}轮"
            )
        if result.errors:
            self.error_requested.emit("部分反馈保存失败：\n" + "\n".join(result.errors))

    def _open_group_root(self) -> None:
        if self.group:
            self._open_path(self.group.root)

    def _open_current_project(self) -> None:
        if self.current_project:
            self._open_path(self.current_project.path)

    def _open_project_record(self) -> None:
        if self.current_project:
            self._open_path(self.current_project.path / "项目记录.md")

    def _open_path(self, path: Path) -> None:
        try:
            self.project_service.open_in_file_manager(path)
        except Exception as exc:
            self.error_requested.emit(f"无法打开：{exc}")
