from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from models import ProjectEntry, ProjectGroup
from services import ProjectService, TaskService
from ui.widgets import Card


class HomePage(QWidget):
    create_project_requested = Signal()
    choose_group_requested = Signal()
    edit_rules_requested = Signal()
    toast_requested = Signal(str)
    error_requested = Signal(str)

    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.project_service = project_service
        self.task_service = task_service
        self.group: ProjectGroup | None = None
        self.current_project: ProjectEntry | None = None
        self._build_ui()
        self.set_empty_state()

    def _build_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(30, 28, 30, 30)
        root_layout.setSpacing(18)

        sidebar = Card()
        sidebar.setFixedWidth(238)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 18)
        sidebar_layout.setSpacing(14)

        brand = QLabel("课件 Agent 控制台")
        brand.setObjectName("sectionTitle")
        sidebar_layout.addWidget(brand)

        create_button = QPushButton("创建项目")
        create_button.setProperty("role", "primary")
        create_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder)
        )
        create_button.clicked.connect(self.create_project_requested)
        sidebar_layout.addWidget(create_button)

        projects_label = QLabel("当前项目")
        projects_label.setObjectName("fieldLabel")
        sidebar_layout.addWidget(projects_label)

        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.currentItemChanged.connect(self._on_project_selected)
        sidebar_layout.addWidget(self.project_list, 1)
        root_layout.addWidget(sidebar)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(18)

        header_card = Card()
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(24, 18, 24, 18)
        header_layout.setSpacing(12)

        top_row = QHBoxLayout()
        group_label = QLabel("当前项目组")
        group_label.setObjectName("fieldLabel")
        top_row.addWidget(group_label)

        self.group_selector = QComboBox()
        self.group_selector.setMinimumWidth(210)
        self.group_selector.activated.connect(self._group_selector_activated)
        top_row.addWidget(self.group_selector)
        top_row.addStretch()

        self.edit_rules_button = QPushButton("编辑任务规则")
        self.edit_rules_button.clicked.connect(self.edit_rules_requested)
        top_row.addWidget(self.edit_rules_button)
        header_layout.addLayout(top_row)

        path_row = QHBoxLayout()
        path_title = QLabel("根目录")
        path_title.setObjectName("fieldLabel")
        path_row.addWidget(path_title)
        self.root_path_label = QLabel()
        self.root_path_label.setObjectName("mutedText")
        self.root_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_row.addWidget(self.root_path_label, 1)
        self.open_root_button = QPushButton("打开")
        self.open_root_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.open_root_button.clicked.connect(self._open_group_root)
        path_row.addWidget(self.open_root_button)
        header_layout.addLayout(path_row)
        main_layout.addWidget(header_card)

        self.content_stack = QStackedWidget()

        empty_card = Card()
        empty_layout = QVBoxLayout(empty_card)
        empty_layout.setContentsMargins(42, 42, 42, 42)
        empty_layout.addStretch()
        empty_title = QLabel("尚未打开项目组")
        empty_title.setObjectName("pageTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        self.empty_message = QLabel("请选择已有项目组，或从左侧创建新项目。")
        self.empty_message.setObjectName("mutedText")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_message)
        select_group_button = QPushButton("选择已有项目组")
        select_group_button.setFixedWidth(180)
        select_group_button.clicked.connect(self.choose_group_requested)
        empty_layout.addWidget(select_group_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.content_stack.addWidget(empty_card)

        task_card = Card()
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(28, 24, 28, 24)
        task_layout.setSpacing(16)

        task_title = QLabel("当前任务")
        task_title.setObjectName("sectionTitle")
        task_layout.addWidget(task_title)

        details_row = QHBoxLayout()
        details_row.setSpacing(16)

        project_column = QVBoxLayout()
        project_field = QLabel("当前项目")
        project_field.setObjectName("fieldLabel")
        project_column.addWidget(project_field)
        self.current_project_label = QLabel()
        self.current_project_label.setObjectName("sectionTitle")
        project_column.addWidget(self.current_project_label)
        details_row.addLayout(project_column, 1)

        task_type_column = QVBoxLayout()
        type_field = QLabel("任务类型")
        type_field.setObjectName("fieldLabel")
        task_type_column.addWidget(type_field)
        self.task_type_combo = QComboBox()
        self.task_type_combo.addItem("首次制作")
        self.task_type_combo.setMinimumWidth(220)
        task_type_column.addWidget(self.task_type_combo)
        details_row.addLayout(task_type_column)
        task_layout.addLayout(details_row)

        requirements_label = QLabel("特殊要求（可留空）")
        requirements_label.setObjectName("fieldLabel")
        task_layout.addWidget(requirements_label)
        self.requirements_input = QPlainTextEdit()
        self.requirements_input.setPlaceholderText("填写本次任务的额外约束")
        self.requirements_input.setMinimumHeight(145)
        task_layout.addWidget(self.requirements_input, 1)

        actions = QHBoxLayout()
        self.copy_prompt_button = QPushButton("复制提示词")
        self.copy_prompt_button.clicked.connect(self._copy_prompt)
        actions.addWidget(self.copy_prompt_button)

        self.open_project_button = QPushButton("打开项目文件夹")
        self.open_project_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.open_project_button.clicked.connect(self._open_current_project)
        actions.addWidget(self.open_project_button)
        actions.addStretch()

        generate_button = QPushButton("生成当前任务")
        generate_button.setProperty("role", "primary")
        generate_button.clicked.connect(self._generate_task)
        actions.addWidget(generate_button)
        task_layout.addLayout(actions)
        self.content_stack.addWidget(task_card)
        main_layout.addWidget(self.content_stack, 1)

        root_layout.addLayout(main_layout, 1)

    def set_group(self, group: ProjectGroup) -> None:
        self.group = group
        self.current_project = None
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        self.group_selector.addItem(group.name)
        self.group_selector.addItem("选择其他项目组…")
        self.group_selector.setCurrentIndex(0)
        self.group_selector.blockSignals(False)
        self.root_path_label.setText(str(group.root))
        self.edit_rules_button.setEnabled(True)
        self.open_root_button.setEnabled(True)

        self.project_list.clear()
        for project in group.projects:
            item = QListWidgetItem(project.name)
            item.setData(Qt.ItemDataRole.UserRole, str(project.path))
            self.project_list.addItem(item)
        self.content_stack.setCurrentIndex(1)
        if self.project_list.count():
            self.project_list.setCurrentRow(0)

    def set_empty_state(self, message: str | None = None) -> None:
        self.group = None
        self.current_project = None
        self.project_list.clear()
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        self.group_selector.addItem("未选择项目组")
        self.group_selector.addItem("选择已有项目组…")
        self.group_selector.setCurrentIndex(0)
        self.group_selector.blockSignals(False)
        self.root_path_label.setText("—")
        self.edit_rules_button.setEnabled(False)
        self.open_root_button.setEnabled(False)
        self.copy_prompt_button.setEnabled(False)
        self.empty_message.setText(message or "请选择已有项目组，或从左侧创建新项目。")
        self.content_stack.setCurrentIndex(0)

    def _group_selector_activated(self, index: int) -> None:
        if index == 1:
            self.group_selector.setCurrentIndex(0)
            self.choose_group_requested.emit()

    def _on_project_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if not current or not self.group:
            self.current_project = None
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        self.current_project = next(
            (project for project in self.group.projects if project.path == path), None
        )
        if not self.current_project:
            return
        self.current_project_label.setText(self.current_project.name)
        self.requirements_input.clear()
        task_path = self.current_project.path / "当前任务.md"
        self.copy_prompt_button.setEnabled(
            task_path.is_file() and bool(task_path.read_text(encoding="utf-8").strip())
        )

    def _generate_task(self) -> None:
        if not self.current_project:
            self.error_requested.emit("请先选择项目。")
            return
        try:
            self.task_service.generate_first_build_task(
                self.current_project.path,
                self.requirements_input.toPlainText(),
            )
        except Exception as exc:
            self.error_requested.emit(f"生成当前任务失败：{exc}")
            return
        self.copy_prompt_button.setEnabled(True)
        self.toast_requested.emit("当前任务已生成")

    def _copy_prompt(self) -> None:
        if not self.current_project:
            return
        prompt = self.task_service.execution_prompt(self.current_project.name)
        QGuiApplication.clipboard().setText(prompt)
        self.toast_requested.emit("已复制")

    def _open_group_root(self) -> None:
        if self.group:
            self._open_path(self.group.root)

    def _open_current_project(self) -> None:
        if self.current_project:
            self._open_path(self.current_project.path)

    def _open_path(self, path: Path) -> None:
        try:
            self.project_service.open_in_file_manager(path)
        except Exception as exc:
            self.error_requested.emit(f"无法打开文件夹：{exc}")

