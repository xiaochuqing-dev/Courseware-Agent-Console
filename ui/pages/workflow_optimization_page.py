from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import (
    ArchiveService,
    PromptService,
    WorkflowOptimizationService,
    WorkflowTaskResult,
)
from ui.workers import BackgroundTaskRelay, BackgroundWorker
from ui.widgets import Card, PromptDialog, configure_wrapped_list


class WorkflowOptimizationPage(QWidget):
    back_requested = Signal()
    error_requested = Signal(str)
    toast_requested = Signal(str)

    def __init__(
        self,
        archive_service: ArchiveService,
        prompt_service: PromptService,
        workflow_service: WorkflowOptimizationService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.archive_service = archive_service
        self.prompt_service = prompt_service
        self.workflow_service = workflow_service or WorkflowOptimizationService(
            prompt_service.resource_root
        )
        self.group_root: Path | None = None
        self.selected_materials: list[Path] = []
        self._prompt_dialog: PromptDialog | None = None
        self._generation_in_progress = False
        self._threads: set[QThread] = set()
        self._workers: set[BackgroundWorker] = set()
        self._relays: set[BackgroundTaskRelay] = set()
        self._build_ui()

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(28, 24, 28, 26)
        page_layout.setSpacing(14)

        header = QHBoxLayout()
        back_button = QPushButton()
        back_button.setProperty("role", "quiet")
        back_button.setProperty("iconOnly", True)
        back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        back_button.setToolTip("返回首页")
        back_button.clicked.connect(self.back_requested)
        header.addWidget(back_button)
        title = QLabel("工作流优化")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel("项目组"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(180)
        self.group_combo.currentTextChanged.connect(self._load_selected_group)
        header.addWidget(self.group_combo)
        refresh_button = QPushButton()
        refresh_button.setProperty("iconOnly", True)
        refresh_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        refresh_button.setToolTip("刷新当前页面")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        page_layout.addLayout(header)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("优化方式"))
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.review_mode_button = QPushButton("从项目复盘")
        self.manual_mode_button = QPushButton("人工提出优化")
        for index, button in enumerate(
            (self.review_mode_button, self.manual_mode_button)
        ):
            button.setCheckable(True)
            button.setProperty("taskMode", True)
            self.mode_group.addButton(button, index)
            mode_row.addWidget(button)
        self.review_mode_button.setChecked(True)
        self.mode_group.idClicked.connect(self._change_mode)
        mode_row.addStretch()
        page_layout.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_review_page())
        self.mode_stack.addWidget(self._build_manual_page())
        page_layout.addWidget(self.mode_stack, 1)
        self._update_selection()
        self._refresh_material_list()
        self._set_generation_busy(False)

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 21, 24, 21)
        card_layout.setSpacing(12)

        list_header = QHBoxLayout()
        list_title = QLabel("选择复盘样本")
        list_title.setObjectName("sectionTitle")
        list_header.addWidget(list_title)
        self.selection_label = QLabel("已选择 0 个项目")
        self.selection_label.setObjectName("mutedText")
        list_header.addWidget(self.selection_label)
        list_header.addStretch()
        select_all_button = QPushButton("全选")
        select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        list_header.addWidget(select_all_button)
        select_none_button = QPushButton("全不选")
        select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        list_header.addWidget(select_none_button)
        card_layout.addLayout(list_header)

        self.project_list = QListWidget()
        self.project_list.setObjectName("workflowProjectList")
        configure_wrapped_list(self.project_list, minimum_height=44)
        self.project_list.itemChanged.connect(self._update_selection)
        card_layout.addWidget(self.project_list, 1)

        self.empty_label = QLabel("当前项目组还没有已完成项目。")
        self.empty_label.setObjectName("mutedText")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        card_layout.addWidget(self.empty_label)
        layout.addWidget(card, 1)

        footer = QHBoxLayout()
        self.apply_button = QPushButton("复制优化实施 Prompt")
        self.apply_button.clicked.connect(self._copy_apply_prompt)
        footer.addWidget(self.apply_button)
        footer.addStretch()
        self.preview_button = QPushButton("预览分析 Prompt")
        self.preview_button.clicked.connect(self._preview_analysis_prompt)
        footer.addWidget(self.preview_button)
        self.copy_button = QPushButton("复制分析 Prompt")
        self.copy_button.setProperty("role", "primary")
        self.copy_button.clicked.connect(self._copy_analysis_prompt)
        footer.addWidget(self.copy_button)
        layout.addLayout(footer)
        return page

    def _build_manual_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 4)
        layout.setSpacing(12)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(10)

        title = QLabel("人工提出优化")
        title.setObjectName("sectionTitle")
        card_layout.addWidget(title)
        value_text = QLabel(
            "说明和材料会保存到项目组的固定任务文件；生成后只需复制一条短指令交给 Agent。"
        )
        value_text.setObjectName("mutedText")
        value_text.setWordWrap(True)
        card_layout.addWidget(value_text)

        description_label = QLabel("优化说明")
        description_label.setObjectName("fieldLabel")
        card_layout.addWidget(description_label)
        self.manual_description_input = QPlainTextEdit()
        self.manual_description_input.setObjectName("workflowDescriptionInput")
        self.manual_description_input.setPlaceholderText(
            "请描述当前发现的问题、希望达到的效果，以及需要注意的细节。"
        )
        self.manual_description_input.setMinimumHeight(130)
        self.manual_description_input.textChanged.connect(
            self._update_manual_action_state
        )
        card_layout.addWidget(self.manual_description_input)

        material_header = QHBoxLayout()
        material_title = QLabel("补充材料（可选）")
        material_title.setObjectName("fieldLabel")
        material_header.addWidget(material_title)
        self.material_count_label = QLabel("0 个文件")
        self.material_count_label.setObjectName("mutedText")
        material_header.addWidget(self.material_count_label)
        material_header.addStretch()
        self.choose_material_button = QPushButton("选择材料")
        self.choose_material_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        )
        self.choose_material_button.clicked.connect(self._choose_material_files)
        material_header.addWidget(self.choose_material_button)
        self.remove_material_button = QPushButton("移除所选")
        self.remove_material_button.clicked.connect(self._remove_selected_materials)
        material_header.addWidget(self.remove_material_button)
        self.clear_material_button = QPushButton("清空材料")
        self.clear_material_button.clicked.connect(self._clear_materials)
        material_header.addWidget(self.clear_material_button)
        card_layout.addLayout(material_header)

        self.material_list = QListWidget()
        self.material_list.setObjectName("workflowMaterialList")
        self.material_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.material_list.setMinimumHeight(116)
        self.material_list.itemSelectionChanged.connect(
            self._update_manual_action_state
        )
        card_layout.addWidget(self.material_list)
        self.material_empty_label = QLabel(
            "未选择补充材料。仅填写优化说明也可以生成任务。"
        )
        self.material_empty_label.setObjectName("mutedText")
        self.material_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.material_empty_label)

        self.generation_status_label = QLabel()
        self.generation_status_label.setObjectName("mutedText")
        self.generation_status_label.hide()
        card_layout.addWidget(self.generation_status_label)

        result_row = QHBoxLayout()
        result_column = QVBoxLayout()
        result_title = QLabel("当前优化任务")
        result_title.setObjectName("fieldLabel")
        result_column.addWidget(result_title)
        self.manual_result_path = QLabel("尚未生成")
        self.manual_result_path.setObjectName("mutedText")
        self.manual_result_path.setWordWrap(True)
        self.manual_result_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        result_column.addWidget(self.manual_result_path)
        result_row.addLayout(result_column, 1)
        self.manual_copy_execution_button = QPushButton("复制执行指令")
        self.manual_copy_execution_button.clicked.connect(
            self._copy_manual_execution_instruction
        )
        result_row.addWidget(self.manual_copy_execution_button)
        self.manual_generate_button = QPushButton("生成当前优化任务")
        self.manual_generate_button.setProperty("role", "primary")
        self.manual_generate_button.clicked.connect(self._generate_manual_task)
        result_row.addWidget(self.manual_generate_button)
        card_layout.addLayout(result_row)

        layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def set_context(self, group_root: Path) -> None:
        new_root = Path(group_root).resolve()
        if self.group_root != new_root:
            self.selected_materials.clear()
            self.manual_description_input.clear()
        self.group_root = new_root
        self.refresh()

    def refresh(self) -> None:
        if not self.group_root:
            return
        selected_group = self.group_combo.currentText() or self.group_root.name
        group_names = list(self.archive_service.archived_group_names(self.group_root))
        if self.group_root.name not in group_names:
            group_names.append(self.group_root.name)
        group_names.sort()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItems(group_names)
        self.group_combo.setCurrentText(
            selected_group if selected_group in group_names else self.group_root.name
        )
        self.group_combo.blockSignals(False)
        self._load_selected_group(self.group_combo.currentText())
        self._refresh_manual_task_state()

    def selected_project_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(paths)

    def add_material_files(self, paths: list[Path]) -> tuple[int, int]:
        existing = {
            self.workflow_service.path_key(path) for path in self.selected_materials
        }
        added = 0
        ignored = 0
        for path in paths:
            candidate = Path(path)
            key = self.workflow_service.path_key(candidate)
            if key in existing:
                ignored += 1
                continue
            self.selected_materials.append(candidate)
            existing.add(key)
            added += 1
        self._refresh_material_list()
        if ignored:
            self.toast_requested.emit(f"已忽略 {ignored} 个重复材料")
        return added, ignored

    def _change_mode(self, mode_id: int) -> None:
        self.mode_stack.setCurrentIndex(mode_id)
        self.group_combo.setEnabled(mode_id == 0)
        self.group_combo.setToolTip(
            "" if mode_id == 0 else "人工优化任务固定保存到当前项目组"
        )
        self._update_manual_action_state()

    def _load_selected_group(self, group_name: str) -> None:
        self.project_list.blockSignals(True)
        self.project_list.clear()
        projects = (
            self.archive_service.archived_projects(self.group_root, group_name)
            if self.group_root and group_name
            else ()
        )
        for project in projects:
            display_name = self.archive_service.archived_project_name(project)
            item = QListWidgetItem(display_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, str(project))
            item.setToolTip(f"{display_name}\n{project}")
            self.project_list.addItem(item)
        self.project_list.blockSignals(False)
        self.project_list.setVisible(bool(projects))
        self.empty_label.setVisible(not projects)
        self._update_selection()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.project_list.blockSignals(True)
        for index in range(self.project_list.count()):
            self.project_list.item(index).setCheckState(state)
        self.project_list.blockSignals(False)
        self._update_selection()

    def _update_selection(self) -> None:
        count = len(self.selected_project_paths())
        self.selection_label.setText(f"已选择 {count} 个项目")
        self.preview_button.setEnabled(count > 0)
        self.copy_button.setEnabled(count > 0)
        self.apply_button.setEnabled(self.group_root is not None)

    def _choose_material_files(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择补充材料",
            "",
            (
                "常用材料 (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.svg *.pdf "
                "*.html *.htm *.md *.doc *.docx *.ppt *.pptx *.xls *.xlsx "
                "*.txt *.csv *.json *.py *.js *.ts *.css);;所有文件 (*)"
            ),
        )
        if selected:
            self.add_material_files([Path(path) for path in selected])

    def _remove_selected_materials(self) -> None:
        rows = sorted(
            {self.material_list.row(item) for item in self.material_list.selectedItems()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self.selected_materials):
                self.selected_materials.pop(row)
        self._refresh_material_list()

    def _clear_materials(self) -> None:
        self.selected_materials.clear()
        self._refresh_material_list()

    def _refresh_material_list(self) -> None:
        self.material_list.clear()
        for path in self.selected_materials:
            try:
                size = path.stat().st_size
                detail = self._format_size(size)
            except OSError:
                detail = "文件当前不可用"
            item = QListWidgetItem(f"{path.name}  ·  {detail}")
            item.setToolTip(str(path))
            self.material_list.addItem(item)
        has_materials = bool(self.selected_materials)
        self.material_list.setVisible(has_materials)
        self.material_empty_label.setVisible(not has_materials)
        self.material_count_label.setText(f"{len(self.selected_materials)} 个文件")
        self._update_manual_action_state()

    def _generate_manual_task(self) -> None:
        if self._generation_in_progress:
            return
        if not self.group_root:
            self.error_requested.emit("请先在首页选择项目组。")
            return
        description = self.manual_description_input.toPlainText().strip()
        if not description:
            self.error_requested.emit("请填写优化说明。")
            return
        group_root = self.group_root
        materials = tuple(self.selected_materials)
        self._set_generation_busy(True, "正在准备生成…")

        def operation(progress):
            return self.workflow_service.generate_task(
                group_root, description, materials, progress
            )

        self._run_background(
            operation,
            self._manual_generation_succeeded,
            self._manual_generation_failed,
            self._manual_generation_finished,
        )

    def _manual_generation_succeeded(self, result: object) -> None:
        if not isinstance(result, WorkflowTaskResult):
            self.error_requested.emit("生成结果格式无效。")
            return
        self.manual_result_path.setText(str(result.task_path))
        self.manual_result_path.setToolTip(str(result.task_path))
        self.toast_requested.emit("当前优化任务已生成")
        self._refresh_manual_task_state()

    def _manual_generation_failed(self, error: object) -> None:
        self.error_requested.emit(f"生成当前优化任务失败：{error}")
        self._refresh_manual_task_state()

    def _manual_generation_finished(self) -> None:
        self._set_generation_busy(False)

    def _set_generation_busy(self, busy: bool, message: str = "") -> None:
        self._generation_in_progress = busy
        self.manual_description_input.setEnabled(not busy)
        self.choose_material_button.setEnabled(not busy)
        self.material_list.setEnabled(not busy)
        self.generation_status_label.setVisible(busy)
        if busy:
            self.generation_status_label.setText(message)
        self.manual_generate_button.setText(
            "正在生成…" if busy else "生成当前优化任务"
        )
        self._update_manual_action_state()

    def _update_manual_action_state(self) -> None:
        busy = self._generation_in_progress
        has_description = bool(self.manual_description_input.toPlainText().strip())
        has_materials = bool(self.selected_materials)
        self.manual_generate_button.setEnabled(
            self.group_root is not None and has_description and not busy
        )
        self.remove_material_button.setEnabled(
            bool(self.material_list.selectedItems()) and not busy
        )
        self.clear_material_button.setEnabled(has_materials and not busy)
        task_exists = bool(
            self.group_root
            and (
                self.group_root
                / self.workflow_service.DIRECTORY_NAME
                / self.workflow_service.CURRENT_TASK_NAME
            ).is_file()
        )
        self.manual_copy_execution_button.setEnabled(task_exists and not busy)

    def _refresh_manual_task_state(self) -> None:
        if not self.group_root:
            self.manual_result_path.setText("尚未生成")
            self._update_manual_action_state()
            return
        task_path = (
            self.group_root
            / self.workflow_service.DIRECTORY_NAME
            / self.workflow_service.CURRENT_TASK_NAME
        )
        self.manual_result_path.setText(str(task_path) if task_path.is_file() else "尚未生成")
        self.manual_result_path.setToolTip(str(task_path) if task_path.is_file() else "")
        self._update_manual_action_state()

    def _copy_manual_execution_instruction(self) -> None:
        if not self.group_root:
            self.error_requested.emit("请先在首页选择项目组。")
            return
        try:
            instruction = self.prompt_service.workflow_task_execution_instruction(
                self.group_root
            )
        except Exception as exc:
            self.error_requested.emit(f"无法复制执行指令：{exc}")
            self._refresh_manual_task_state()
            return
        QGuiApplication.clipboard().setText(instruction)
        self.toast_requested.emit("执行指令已复制")

    def _run_background(self, operation, succeeded, failed, finished) -> None:
        thread = QThread(self)
        worker = BackgroundWorker(operation)
        relay = BackgroundTaskRelay(succeeded, failed, finished, self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stage_changed.connect(self.generation_status_label.setText)
        worker.succeeded.connect(relay.on_succeeded)
        worker.failed.connect(relay.on_failed)
        worker.finished.connect(relay.on_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._threads.add(thread)
        self._workers.add(worker)
        self._relays.add(relay)

        def cleanup() -> None:
            self._threads.discard(thread)
            self._workers.discard(worker)
            self._relays.discard(relay)
            relay.deleteLater()

        thread.finished.connect(cleanup)
        thread.start()

    def _analysis_prompt(self) -> str | None:
        if not self.group_root:
            self.error_requested.emit("请先在首页选择项目组。")
            return None
        try:
            return self.prompt_service.workflow_optimization_prompt(
                self.group_root, self.selected_project_paths()
            )
        except Exception as exc:
            self.error_requested.emit(f"无法生成工作流优化 Prompt：{exc}")
            return None

    def _preview_analysis_prompt(self) -> None:
        prompt = self._analysis_prompt()
        if prompt is None:
            return
        self._prompt_dialog = PromptDialog(
            "工作流优化分析 Prompt",
            prompt,
            self,
            "复制分析 Prompt",
        )
        self._prompt_dialog.exec()

    def _copy_analysis_prompt(self) -> None:
        prompt = self._analysis_prompt()
        if prompt is None:
            return
        QGuiApplication.clipboard().setText(prompt)
        self.toast_requested.emit("工作流优化分析 Prompt 已复制")

    def _copy_apply_prompt(self) -> None:
        if not self.group_root:
            return
        try:
            prompt = self.prompt_service.workflow_apply_prompt(self.group_root)
        except Exception as exc:
            self.error_requested.emit(f"无法生成优化实施 Prompt：{exc}")
            return
        QGuiApplication.clipboard().setText(prompt)
        self.toast_requested.emit("优化实施 Prompt 已复制")

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
