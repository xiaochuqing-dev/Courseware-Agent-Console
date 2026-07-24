from __future__ import annotations

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
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from models import ProjectEntry, ProjectGroup
from services import (
    ArchiveService,
    FeedbackService,
    PendingFeedback,
    ProjectService,
    PromptService,
    TaskService,
)
from ui.widgets import Card, FeedbackDropArea, PendingFeedbackRow, PromptDialog


class HomePage(QWidget):
    create_project_requested = Signal()
    choose_group_requested = Signal()
    edit_rules_requested = Signal()
    archive_requested = Signal()
    completed_projects_requested = Signal()
    toast_requested = Signal(str)
    error_requested = Signal(str)

    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        feedback_service: FeedbackService | None = None,
        archive_service: ArchiveService | None = None,
        prompt_service: PromptService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.project_service = project_service
        self.task_service = task_service
        self.feedback_service = feedback_service or FeedbackService()
        self.archive_service = archive_service or ArchiveService()
        self.prompt_service = prompt_service or PromptService()
        self.group: ProjectGroup | None = None
        self.current_project: ProjectEntry | None = None
        self.pending_feedback: list[PendingFeedback] = []
        self._prompt_dialog: PromptDialog | None = None
        self._build_ui()
        self.set_empty_state()

    def _build_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(26, 24, 26, 26)
        root_layout.setSpacing(18)

        sidebar = Card()
        sidebar.setFixedWidth(224)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 17, 15, 17)
        sidebar_layout.setSpacing(12)

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

        projects_label = QLabel("进行中项目")
        projects_label.setObjectName("fieldLabel")
        sidebar_layout.addWidget(projects_label)

        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.currentItemChanged.connect(self._on_project_selected)
        sidebar_layout.addWidget(self.project_list, 1)

        self.completed_button = QPushButton("已完成项目")
        self.completed_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.completed_button.clicked.connect(self.completed_projects_requested)
        sidebar_layout.addWidget(self.completed_button)
        root_layout.addWidget(sidebar)

        main_layout = QVBoxLayout()
        main_layout.setSpacing(14)

        header_card = Card()
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(21, 15, 21, 15)
        header_layout.setSpacing(9)

        top_row = QHBoxLayout()
        group_label = QLabel("当前项目组")
        group_label.setObjectName("fieldLabel")
        top_row.addWidget(group_label)
        self.group_selector = QComboBox()
        self.group_selector.setMinimumWidth(190)
        self.group_selector.activated.connect(self._group_selector_activated)
        top_row.addWidget(self.group_selector)
        top_row.addStretch()

        self.refresh_button = QPushButton()
        self.refresh_button.setProperty("iconOnly", True)
        self.refresh_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.refresh_button.setToolTip("刷新项目文件信息")
        self.refresh_button.clicked.connect(self.refresh_group)
        top_row.addWidget(self.refresh_button)

        self.edit_rules_button = QPushButton("编辑任务规则")
        self.edit_rules_button.clicked.connect(self.edit_rules_requested)
        top_row.addWidget(self.edit_rules_button)

        self.archive_button = QPushButton("标记已完成 / 归档")
        self.archive_button.clicked.connect(self.archive_requested)
        top_row.addWidget(self.archive_button)
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
        empty_title = QLabel("尚未选择进行中项目")
        empty_title.setObjectName("pageTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        self.empty_message = QLabel("请选择已有项目组，或从左侧创建新项目。")
        self.empty_message.setObjectName("mutedText")
        self.empty_message.setWordWrap(True)
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_message)
        select_group_button = QPushButton("选择已有项目组")
        select_group_button.setFixedWidth(180)
        select_group_button.clicked.connect(self.choose_group_requested)
        empty_layout.addWidget(select_group_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.content_stack.addWidget(empty_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_content.setObjectName("page")
        work_layout = QVBoxLayout(scroll_content)
        work_layout.setContentsMargins(0, 0, 8, 4)
        work_layout.setSpacing(14)
        work_layout.addWidget(self._build_task_card())
        work_layout.addWidget(self._build_feedback_card())
        work_layout.addStretch()
        scroll.setWidget(scroll_content)
        self.content_stack.addWidget(scroll)
        main_layout.addWidget(self.content_stack, 1)
        root_layout.addLayout(main_layout, 1)

    def _build_task_card(self) -> Card:
        task_card = Card()
        task_layout = QVBoxLayout(task_card)
        task_layout.setContentsMargins(24, 19, 24, 19)
        task_layout.setSpacing(12)

        title_row = QHBoxLayout()
        task_title = QLabel("当前任务")
        task_title.setObjectName("sectionTitle")
        title_row.addWidget(task_title)
        title_row.addStretch()
        self.latest_product_label = QLabel("最新产品：无")
        self.latest_product_label.setObjectName("mutedText")
        title_row.addWidget(self.latest_product_label)
        task_layout.addLayout(title_row)

        details_row = QHBoxLayout()
        details_row.setSpacing(20)
        project_column = QVBoxLayout()
        project_field = QLabel("当前项目")
        project_field.setObjectName("fieldLabel")
        project_column.addWidget(project_field)
        self.current_project_label = QLabel()
        self.current_project_label.setObjectName("sectionTitle")
        project_column.addWidget(self.current_project_label)
        details_row.addLayout(project_column, 1)

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
        round_layout.addWidget(self.feedback_round_combo)
        details_row.addWidget(self.round_widget)
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
        self.requirements_input.setMinimumHeight(92)
        self.requirements_input.setMaximumHeight(118)
        requirements_column.addWidget(self.requirements_input)
        task_body.addLayout(requirements_column, 1)

        preview_column = QVBoxLayout()
        preview_label = QLabel("当前任务预览")
        preview_label.setObjectName("fieldLabel")
        preview_column.addWidget(preview_label)
        self.task_preview = QPlainTextEdit()
        self.task_preview.setReadOnly(True)
        self.task_preview.setPlaceholderText("生成后在这里查看当前任务索引")
        self.task_preview.setMinimumHeight(92)
        self.task_preview.setMaximumHeight(118)
        preview_column.addWidget(self.task_preview)
        task_body.addLayout(preview_column, 1)
        task_layout.addLayout(task_body)

        self.feedback_task_hint = QLabel("当前项目还没有客户反馈，请先导入反馈。")
        self.feedback_task_hint.setObjectName("errorBanner")
        self.feedback_task_hint.hide()
        task_layout.addWidget(self.feedback_task_hint)

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

        more_button = QToolButton()
        more_button.setText("更多操作")
        more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more_menu = QMenu(more_button)
        acceptance_action = more_menu.addAction("完整产品验收")
        acceptance_action.triggered.connect(self._show_acceptance_prompt)
        record_action = more_menu.addAction("打开项目记录")
        record_action.triggered.connect(self._open_project_record)
        more_button.setMenu(more_menu)
        actions.addWidget(more_button)
        actions.addStretch()

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

        self.pending_container = QWidget()
        self.pending_layout = QVBoxLayout(self.pending_container)
        self.pending_layout.setContentsMargins(0, 0, 0, 0)
        self.pending_layout.setSpacing(5)
        layout.addWidget(self.pending_container)

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
        return card

    def set_group(self, group: ProjectGroup, preferred_project: str | None = None) -> None:
        self.group = group
        self.current_project = None
        self.pending_feedback.clear()
        self._refresh_pending_list()
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        self.group_selector.addItem(group.name)
        self.group_selector.addItem("选择其他项目组…")
        self.group_selector.setCurrentIndex(0)
        self.group_selector.blockSignals(False)
        self.root_path_label.setText(str(group.root))
        for widget in (
            self.edit_rules_button,
            self.open_root_button,
            self.refresh_button,
            self.completed_button,
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
            self.empty_message.setText(
                "该项目组暂无进行中的项目。可从左侧“已完成项目”查看归档内容。"
            )
            self.content_stack.setCurrentIndex(0)

    def set_empty_state(self, message: str | None = None) -> None:
        self.group = None
        self.current_project = None
        self.pending_feedback.clear()
        self._refresh_pending_list()
        self.project_list.clear()
        self.group_selector.blockSignals(True)
        self.group_selector.clear()
        self.group_selector.addItem("未选择项目组")
        self.group_selector.addItem("选择已有项目组…")
        self.group_selector.setCurrentIndex(0)
        self.group_selector.blockSignals(False)
        self.root_path_label.setText("无")
        for widget in (
            self.edit_rules_button,
            self.open_root_button,
            self.refresh_button,
            self.completed_button,
            self.archive_button,
            self.copy_prompt_button,
            self.open_project_button,
        ):
            widget.setEnabled(False)
        self.empty_message.setText(message or "请选择已有项目组，或从左侧创建新项目。")
        self.content_stack.setCurrentIndex(0)

    def refresh_group(self) -> None:
        if not self.group:
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
            self.refresh_group()
            return
        self._refresh_project_state()

    def _group_selector_activated(self, index: int) -> None:
        if index == 1:
            self.group_selector.setCurrentIndex(0)
            self.choose_group_requested.emit()

    def _on_project_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if not current or not self.group:
            self.current_project = None
            self.archive_button.setEnabled(False)
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
        self._refresh_project_state()

    def _refresh_project_state(self) -> None:
        if not self.current_project:
            return
        task_path = self.current_project.path / "当前任务.md"
        task_content = ""
        if task_path.is_file():
            task_content = task_path.read_text(encoding="utf-8")
        self.task_preview.setPlainText(task_content)
        self.copy_prompt_button.setEnabled(bool(task_content.strip()))

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
        else:
            self.latest_feedback_label.setText("尚无反馈轮次")
        self.feedback_round_combo.blockSignals(False)
        self._update_feedback_actions()
        self._update_task_mode_state()

    def _on_task_mode_changed(self) -> None:
        self._update_task_mode_state()

    def _update_task_mode_state(self) -> None:
        feedback_mode = self.task_mode_group.checkedId() == 1
        self.round_widget.setVisible(feedback_mode)
        no_rounds = self.feedback_round_combo.count() == 0
        self.feedback_task_hint.setVisible(feedback_mode and no_rounds)
        self.generate_button.setEnabled(not feedback_mode or not no_rounds)

    def _generate_task(self) -> None:
        if not self.current_project:
            self.error_requested.emit("请先选择项目。")
            return
        try:
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
        self.toast_requested.emit("当前任务已生成")

    def _copy_prompt(self) -> None:
        if not self.current_project:
            return
        prompt = self.prompt_service.execution_prompt(self.current_project.name)
        QGuiApplication.clipboard().setText(prompt)
        self.toast_requested.emit("已复制")

    def _show_acceptance_prompt(self) -> None:
        if not self.current_project:
            return
        try:
            prompt = self.prompt_service.product_acceptance_prompt(self.current_project.path)
        except Exception as exc:
            self.error_requested.emit(f"无法生成产品验收 Prompt：{exc}")
            return
        self._prompt_dialog = PromptDialog("完整产品验收", prompt, self)
        self._prompt_dialog.exec()

    def _choose_feedback_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(self, "选择客户反馈文件")
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
        for path in paths:
            try:
                item = self.feedback_service.pending_from_file(path, self._pending_names())
            except Exception as exc:
                errors.append(f"{path.name or path}：{exc}")
                continue
            self.pending_feedback.append(item)
        self._refresh_pending_list()
        if errors:
            self.error_requested.emit("部分资料未加入：\n" + "\n".join(errors))

    def _append_pending(self, item: PendingFeedback) -> None:
        self.pending_feedback.append(item)
        self._refresh_pending_list()

    def _pending_names(self) -> set[str]:
        return {item.name for item in self.pending_feedback}

    def _remove_pending(self, item_id: str) -> None:
        self.pending_feedback = [
            item for item in self.pending_feedback if item.item_id != item_id
        ]
        self._refresh_pending_list()

    def _refresh_pending_list(self) -> None:
        while self.pending_layout.count():
            layout_item = self.pending_layout.takeAt(0)
            if layout_item.widget():
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
                self.pending_layout.addWidget(row)
        self.pending_count_label.setText(f"{len(self.pending_feedback)} 项")
        self._update_feedback_actions()

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
