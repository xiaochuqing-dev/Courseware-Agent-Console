from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import (
    ArchiveService,
    PromptService,
    WorkflowOptimizationInput,
    WorkflowOptimizationService,
    WorkflowProjectInfo,
    WorkflowTaskResult,
    WorkflowTaskValidationResult,
)
from ui.workers import BackgroundTaskRelay, BackgroundWorker
from ui.widgets import Card, GlassCheckBox, PromptDialog, configure_wrapped_list


class _WorkflowProjectRow(QWidget):
    toggled = Signal(object, bool)

    def __init__(
        self,
        project_path: Path,
        display_name: str,
        detail: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_path = Path(project_path).resolve()
        self.setMinimumHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{display_name}\n{detail}\n{self.project_path}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(4)
        self.checkbox = GlassCheckBox()
        self.checkbox.setAccessibleName(f"选择参考项目 {display_name}")
        self.checkbox.toggled.connect(
            lambda checked: self.toggled.emit(self.project_path, checked)
        )
        layout.addWidget(self.checkbox)
        self.name_label = QLabel(display_name)
        self.name_label.setToolTip(self.toolTip())
        layout.addWidget(self.name_label, 1)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.childAt(event.position().toPoint()) is not self.checkbox
        ):
            self.checkbox.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select):
            self.checkbox.toggle()
            event.accept()
            return
        super().keyPressEvent(event)


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
        self._project_rows: dict[str, _WorkflowProjectRow] = {}
        self._prompt_dialog: PromptDialog | None = None
        self._generation_in_progress = False
        self._loading_inputs = False
        self._context_initialized = False
        self._input_dirty = False
        self._last_archive_message = ""
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

        self.unified_scroll = self._build_unified_page()
        page_layout.addWidget(self.unified_scroll, 1)
        self._update_selection()
        self._refresh_material_list()
        self._set_generation_busy(False)

    def _build_unified_page(self) -> QScrollArea:
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

        title = QLabel("优化输入")
        title.setObjectName("sectionTitle")
        card_layout.addWidget(title)
        value_text = QLabel(
            "参考项目、优化说明和补充材料均为可选；至少提供一项，也可以任意组合。"
        )
        value_text.setObjectName("mutedText")
        value_text.setWordWrap(True)
        card_layout.addWidget(value_text)

        project_header = QHBoxLayout()
        project_title = QLabel("参考项目（可选）")
        project_title.setObjectName("fieldLabel")
        project_header.addWidget(project_title)
        self.selection_label = QLabel("已选择 0 个项目")
        self.selection_label.setObjectName("mutedText")
        project_header.addWidget(self.selection_label)
        project_header.addStretch()
        self.select_all_button = QPushButton("全选")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        project_header.addWidget(self.select_all_button)
        self.select_none_button = QPushButton("全不选")
        self.select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        project_header.addWidget(self.select_none_button)
        card_layout.addLayout(project_header)

        self.project_list = QListWidget()
        self.project_list.setObjectName("workflowProjectList")
        configure_wrapped_list(self.project_list, minimum_height=44)
        self.project_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.project_list.setMaximumHeight(240)
        card_layout.addWidget(self.project_list)

        self.empty_label = QLabel(
            "当前项目组还没有已完成项目，可填写优化说明或添加补充材料。"
        )
        self.empty_label.setObjectName("mutedText")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.hide()
        card_layout.addWidget(self.empty_label)

        description_label = QLabel("优化说明（可选）")
        description_label.setObjectName("fieldLabel")
        card_layout.addWidget(description_label)
        self.manual_description_input = QPlainTextEdit()
        self.manual_description_input.setObjectName("workflowDescriptionInput")
        self.manual_description_input.setPlaceholderText(
            "请描述当前发现的问题、希望达到的效果，以及需要注意的细节。"
        )
        self.manual_description_input.setMinimumHeight(130)
        self.manual_description_input.textChanged.connect(
            self._mark_input_changed
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
            "未选择补充材料。可只选择复盘项目或填写优化说明。"
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
        self.apply_button = QPushButton("查看当前任务")
        self.apply_button.clicked.connect(self._view_current_task)
        result_row.addWidget(self.apply_button)
        self.preview_button = QPushButton("复制执行指令")
        self.preview_button.clicked.connect(self._copy_execution_instruction)
        result_row.addWidget(self.preview_button)
        self.copy_button = QPushButton("生成当前优化任务")
        self.copy_button.setProperty("role", "primary")
        self.copy_button.clicked.connect(self._generate_task)
        result_row.addWidget(self.copy_button)
        card_layout.addLayout(result_row)

        layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(page)
        return scroll

    def set_context(self, group_root: Path) -> None:
        new_root = Path(group_root).resolve()
        if self.group_root != new_root:
            self.group_root = new_root
            self.selected_materials.clear()
            self._context_initialized = False
            self._input_dirty = False
            self._last_archive_message = ""
            self._loading_inputs = True
            self.manual_description_input.clear()
            self._loading_inputs = False
        self.refresh()

    def refresh(self) -> None:
        if not self.group_root:
            return
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem(self.group_root.name)
        self.group_combo.setCurrentIndex(0)
        self.group_combo.blockSignals(False)
        self._load_selected_group(self.group_combo.currentText())
        if not self._context_initialized:
            loaded = self.workflow_service.load_current_input(self.group_root)
            if loaded is not None:
                self._apply_input(loaded)
            self._context_initialized = True
            self._input_dirty = False
        self._refresh_material_list()
        self._refresh_manual_task_state()

    def selected_project_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            row = self.project_list.itemWidget(item)
            if isinstance(row, _WorkflowProjectRow) and row.is_checked():
                paths.append(row.project_path)
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
        if added:
            self._mark_input_changed()
        self._refresh_material_list()
        if ignored:
            self.toast_requested.emit(f"已忽略 {ignored} 个重复材料")
        return added, ignored

    def _load_selected_group(self, group_name: str) -> None:
        selected_keys = {
            self.workflow_service.path_key(path)
            for path in self.selected_project_paths()
        }
        if not self.group_root or not group_name:
            projects = ()
        else:
            try:
                projects = self.workflow_service.list_reference_projects(
                    self.group_root
                )
            except Exception as exc:
                projects = ()
                self.error_requested.emit(f"无法读取已完成项目：{exc}")
        self._rebuild_project_list(projects, selected_keys)

    def _rebuild_project_list(
        self,
        projects: tuple[WorkflowProjectInfo, ...],
        selected_keys: set[str],
    ) -> None:
        self.project_list.clear()
        self._project_rows.clear()
        for project in projects:
            latest = (
                project.latest_product_path.name
                if project.latest_product_path is not None
                else "无可用产品"
            )
            detail = f"已完成 · {project.feedback_summary} · 最新产品：{latest}"
            row = _WorkflowProjectRow(
                project.project_path,
                project.display_name,
                detail,
            )
            row.toggled.connect(self._project_toggled)
            key = self.workflow_service.path_key(project.project_path)
            self._project_rows[key] = row
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 44))
            item.setData(Qt.ItemDataRole.UserRole, str(project.project_path))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, project.display_name)
            item.setToolTip(row.toolTip())
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, row)
            row.checkbox.blockSignals(True)
            row.set_checked(key in selected_keys)
            row.checkbox.blockSignals(False)
        self.project_list.setFixedHeight(
            min(240, max(54, len(projects) * 48 + 14))
        )
        self.project_list.setVisible(bool(projects))
        self.empty_label.setVisible(not projects)
        self._update_selection()

    def _apply_input(self, workflow_input: WorkflowOptimizationInput) -> None:
        self._loading_inputs = True
        try:
            self.manual_description_input.setPlainText(
                workflow_input.user_description
            )
            self.selected_materials = list(workflow_input.material_paths)
            selected = {
                self.workflow_service.path_key(path)
                for path in workflow_input.selected_project_paths
            }
            for key, row in self._project_rows.items():
                row.checkbox.blockSignals(True)
                row.set_checked(key in selected)
                row.checkbox.blockSignals(False)
        finally:
            self._loading_inputs = False
        self._update_selection()

    def _project_toggled(self, _path: object, _checked: bool) -> None:
        self._update_selection()
        self._mark_input_changed()

    def _set_all_checked(self, checked: bool) -> None:
        self._loading_inputs = True
        try:
            for row in self._project_rows.values():
                row.set_checked(checked)
        finally:
            self._loading_inputs = False
        self._update_selection()
        self._mark_input_changed()

    def _update_selection(self) -> None:
        count = len(self.selected_project_paths())
        self.selection_label.setText(f"已选择 {count} 个项目")
        self._update_manual_action_state()

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
        removed = False
        for row in rows:
            if 0 <= row < len(self.selected_materials):
                self.selected_materials.pop(row)
                removed = True
        if removed:
            self._mark_input_changed()
        self._refresh_material_list()

    def _clear_materials(self) -> None:
        if not self.selected_materials:
            return
        self.selected_materials.clear()
        self._mark_input_changed()
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

    def _mark_input_changed(self) -> None:
        if self._loading_inputs:
            return
        if self.group_root and self.workflow_service.current_task_path(
            self.group_root
        ).is_file():
            self._input_dirty = True
        self._refresh_manual_task_state()

    def _has_any_input(self) -> bool:
        return bool(
            self.selected_project_paths()
            or self.manual_description_input.toPlainText().strip()
            or self.selected_materials
        )

    def _generate_task(self) -> None:
        if self._generation_in_progress:
            return
        if not self.group_root:
            self.error_requested.emit("请先在首页选择项目组。")
            return
        if not self._has_any_input():
            self.error_requested.emit(
                "请至少选择复盘项目、填写优化说明或添加补充材料中的一项。"
            )
            return
        workflow_input = WorkflowOptimizationInput(
            group_root=self.group_root,
            selected_project_paths=self.selected_project_paths(),
            user_description=self.manual_description_input.toPlainText(),
            material_paths=tuple(self.selected_materials),
        )
        self._set_generation_busy(True, "正在准备生成…")

        def operation(progress):
            return self.workflow_service.generate_task(workflow_input, progress)

        self._run_background(
            operation,
            self._manual_generation_succeeded,
            self._manual_generation_failed,
            self._manual_generation_finished,
        )

    def _generate_manual_task(self) -> None:
        self._generate_task()

    def _manual_generation_succeeded(self, result: object) -> None:
        if not isinstance(result, WorkflowTaskResult):
            self.error_requested.emit("生成结果格式无效。")
            return
        self.selected_materials = list(result.material_paths)
        self._input_dirty = False
        self._last_archive_message = (
            f"上一轮已归档：{result.archived_path}"
            if result.archived_path is not None
            else "本轮为首个优化任务，未产生历史归档。"
        )
        self._refresh_material_list()
        self.toast_requested.emit("当前优化任务已生成")
        self._refresh_manual_task_state()

    def _manual_generation_failed(self, error: object) -> None:
        self.error_requested.emit(f"生成当前优化任务失败：{error}")
        self._refresh_manual_task_state()

    def _manual_generation_finished(self) -> None:
        self._set_generation_busy(False)
        self._refresh_manual_task_state()

    def _set_generation_busy(self, busy: bool, message: str = "") -> None:
        self._generation_in_progress = busy
        for widget in (
            self.project_list,
            self.select_all_button,
            self.select_none_button,
            self.manual_description_input,
            self.choose_material_button,
            self.material_list,
        ):
            widget.setEnabled(not busy)
        self.group_combo.setEnabled(not busy)
        self.generation_status_label.setVisible(busy)
        if busy:
            self.generation_status_label.setText(message)
        self._update_manual_action_state()

    def _update_manual_action_state(self) -> None:
        busy = self._generation_in_progress
        has_materials = bool(self.selected_materials)
        has_input = self._has_any_input()
        try:
            validation = (
                self.workflow_service.validate_current_task(self.group_root)
                if self.group_root
                else WorkflowTaskValidationResult(False, False)
            )
        except Exception:
            validation = WorkflowTaskValidationResult(False, False)
        can_copy = validation.valid and not self._input_dirty and not busy
        can_generate = self.group_root is not None and has_input and not busy
        task_exists = validation.exists
        generate_text = (
            "正在生成…"
            if busy
            else "重新生成当前优化任务"
            if task_exists
            else "生成当前优化任务"
        )
        self.copy_button.setText(generate_text)
        self.copy_button.setEnabled(can_generate)
        self.remove_material_button.setEnabled(
            bool(self.material_list.selectedItems()) and not busy
        )
        self.clear_material_button.setEnabled(has_materials and not busy)
        self.preview_button.setEnabled(can_copy)
        self.apply_button.setEnabled(task_exists and not busy)

    def _refresh_manual_task_state(self) -> None:
        if not self.group_root:
            self.manual_result_path.setText("尚未生成")
            self._update_manual_action_state()
            return
        task_path = self.workflow_service.current_task_path(self.group_root)
        try:
            validation = self.workflow_service.validate_current_task(self.group_root)
        except Exception as exc:
            validation = WorkflowTaskValidationResult(
                False, False, f"任务校验失败：{exc}"
            )
        if not validation.exists:
            text = "尚未生成"
        elif self._input_dirty:
            text = f"输入已变化，需要重新生成 · {task_path.name}"
        else:
            text = f"{validation.status_text} · {task_path.name}"
        tooltip = str(task_path) if validation.exists else ""
        if self._last_archive_message:
            tooltip = f"{tooltip}\n{self._last_archive_message}".strip()
        self.manual_result_path.setText(text)
        self.manual_result_path.setToolTip(tooltip)
        self._update_manual_action_state()

    def _view_current_task(self) -> None:
        if not self.group_root:
            return
        task_path = self.workflow_service.current_task_path(self.group_root)
        try:
            content = task_path.read_text(encoding="utf-8")
        except Exception as exc:
            self.error_requested.emit(f"无法读取当前优化任务：{exc}")
            return
        validation = self.workflow_service.validate_current_task(self.group_root)
        copy_text = ""
        if validation.valid and not self._input_dirty:
            try:
                copy_text = self.prompt_service.workflow_task_execution_instruction(
                    self.group_root
                )
            except Exception as exc:
                self.error_requested.emit(f"无法生成执行指令：{exc}")
                return
        self._prompt_dialog = PromptDialog(
            "当前工作流优化任务",
            content,
            self,
            "复制执行指令",
            copy_text=copy_text,
        )
        if not copy_text:
            self._prompt_dialog.copy_button.setEnabled(False)
            self._prompt_dialog.copy_button.setText("任务已过期，不能复制")
            self._prompt_dialog.copy_button.setToolTip(
                "输入已变化" if self._input_dirty else validation.reason
            )
        self._prompt_dialog.exec()

    def _copy_execution_instruction(self) -> None:
        if not self.group_root:
            self.error_requested.emit("请先在首页选择项目组。")
            return
        if self._input_dirty:
            self.error_requested.emit("输入已变化，请重新生成当前优化任务。")
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

    def _copy_manual_execution_instruction(self) -> None:
        self._copy_execution_instruction()

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
