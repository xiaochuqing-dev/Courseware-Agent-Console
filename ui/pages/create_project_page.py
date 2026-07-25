from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import (
    ProjectCreationError,
    ProjectService,
    TargetExistsError,
    ToolBinding,
    ToolValidationResult,
)
from services.app_logging import LOGGER_NAME
from ui.widgets import Card, FlowLayout
from ui.workers import BackgroundTaskRelay, BackgroundWorker


logger = logging.getLogger(LOGGER_NAME)


class CreateProjectPage(QWidget):
    cancelled = Signal()
    project_created = Signal(object)
    open_existing_requested = Signal(object)

    def __init__(self, project_service: ProjectService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.project_service = project_service
        self.json_files: list[Path] = []
        self.tool_inputs: dict[str, QLineEdit] = {}
        self.tool_status_labels: dict[str, QLabel] = {}
        self._creation_in_progress = False
        self._tool_validation_in_progress = False
        self._tool_validation_result: ToolValidationResult | None = None
        self._validated_binding_key: tuple[str, str, str] | None = None
        self._validation_generation = 0
        self._threads: set[QThread] = set()
        self._workers: set[BackgroundWorker] = set()
        self._relays: set[BackgroundTaskRelay] = set()
        self._form_controls: list[QWidget] = []
        self._prevalidation_timer = QTimer(self)
        self._prevalidation_timer.setSingleShot(True)
        self._prevalidation_timer.setInterval(250)
        self._prevalidation_timer.timeout.connect(self._start_tool_prevalidation)
        self._build_ui()
        self.refresh_public_tools()

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(34, 28, 34, 30)
        page_layout.setSpacing(20)

        header = QHBoxLayout()
        self.back_button = QPushButton()
        self.back_button.setProperty("role", "quiet")
        self.back_button.setProperty("iconOnly", True)
        self.back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.back_button.setToolTip("返回首页")
        self.back_button.clicked.connect(self._cancel_requested)
        header.addWidget(self.back_button)

        title = QLabel("创建项目组")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        page_layout.addLayout(header)

        self.content_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        content = self.content_layout
        content.setSpacing(18)

        form_card = Card()
        form_card.setMinimumWidth(320)
        form_card.setMinimumHeight(330)
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(24, 22, 24, 22)
        form_layout.setSpacing(17)

        form_title = QLabel("基本信息")
        form_title.setObjectName("sectionTitle")
        form_layout.addWidget(form_title)

        fields = QFormLayout()
        fields.setHorizontalSpacing(18)
        fields.setVerticalSpacing(14)
        fields.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.name_input = QLineEdit("九年级")
        self.name_input.setPlaceholderText("例如：九年级-暑期")
        fields.addRow("项目目录名称", self.name_input)

        self.count_input = QSpinBox()
        self.count_input.setRange(1, 500)
        self.count_input.setValue(3)
        self.count_input.valueChanged.connect(self._update_json_summary)
        fields.addRow("项目数量", self.count_input)

        location_row = QHBoxLayout()
        self.location_input = QLineEdit(self._default_desktop_path())
        location_row.addWidget(self.location_input, 1)
        self.choose_location_button = QPushButton("选择")
        self.choose_location_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.choose_location_button.clicked.connect(self._choose_location)
        location_row.addWidget(self.choose_location_button)
        fields.addRow("创建位置", location_row)
        form_layout.addLayout(fields)

        json_header = QHBoxLayout()
        json_label = QLabel("原始 JSON")
        json_label.setObjectName("fieldLabel")
        json_header.addWidget(json_label)
        json_header.addStretch()
        self.json_summary = QLabel()
        self.json_summary.setObjectName("mutedText")
        json_header.addWidget(self.json_summary)
        form_layout.addLayout(json_header)

        self.import_button = QPushButton("添加 JSON 文件")
        self.import_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self.import_button.clicked.connect(self._choose_json_files)
        form_layout.addWidget(self.import_button)

        self.json_status = QLabel()
        self.json_status.setObjectName("jsonStatus")
        self.json_status.setWordWrap(True)
        form_layout.addWidget(self.json_status)

        tools_label = QLabel("真实公共工具")
        tools_label.setObjectName("fieldLabel")
        form_layout.addWidget(tools_label)

        tools_hint = QLabel("创建前必须分别选择 workflow、template、validate；不会使用内置默认文件。")
        tools_hint.setObjectName("mutedText")
        tools_hint.setWordWrap(True)
        form_layout.addWidget(tools_hint)

        tool_names = {
            "workflow": ("workflow", "Markdown 文件 (*.md);;所有文件 (*)"),
            "template": ("template", "HTML 文件 (*.html *.htm);;所有文件 (*)"),
            "validate": ("validate", "JavaScript 文件 (*.js);;所有文件 (*)"),
        }
        for role, (label_text, file_filter) in tool_names.items():
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            label.setFixedWidth(62)
            row.addWidget(label)
            path_input = QLineEdit()
            path_input.setPlaceholderText(f"选择真实 {label_text} 文件")
            path_input.textChanged.connect(self._tools_changed)
            row.addWidget(path_input, 1)
            choose = QPushButton("选择")
            choose.clicked.connect(
                lambda _checked=False, key=role, filters=file_filter: self._choose_tool_file(
                    key, filters
                )
            )
            row.addWidget(choose)
            form_layout.addLayout(row)
            status = QLabel("未选择")
            status.setObjectName("jsonStatus")
            status.setWordWrap(True)
            form_layout.addWidget(status)
            self.tool_inputs[role] = path_input
            self.tool_status_labels[role] = status
            self._form_controls.append(choose)
        form_layout.addStretch()

        content.addWidget(form_card, 5)

        mapping_card = Card()
        mapping_card.setMinimumWidth(280)
        mapping_card.setMinimumHeight(320)
        mapping_layout = QVBoxLayout(mapping_card)
        mapping_layout.setContentsMargins(24, 22, 24, 22)
        mapping_layout.setSpacing(14)

        mapping_title_row = QHBoxLayout()
        mapping_title = QLabel("JSON → 项目映射")
        mapping_title.setObjectName("sectionTitle")
        mapping_title_row.addWidget(mapping_title)
        mapping_title_row.addStretch()

        up_button = QPushButton()
        up_button.setProperty("iconOnly", True)
        up_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        up_button.setToolTip("上移所选 JSON")
        up_button.clicked.connect(lambda: self._move_mapping(-1))
        mapping_title_row.addWidget(up_button)

        down_button = QPushButton()
        down_button.setProperty("iconOnly", True)
        down_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        down_button.setToolTip("下移所选 JSON")
        down_button.clicked.connect(lambda: self._move_mapping(1))
        mapping_title_row.addWidget(down_button)

        remove_button = QPushButton()
        remove_button.setProperty("iconOnly", True)
        remove_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        remove_button.setToolTip("删除所选 JSON")
        remove_button.clicked.connect(self._remove_mapping)
        mapping_title_row.addWidget(remove_button)
        mapping_layout.addLayout(mapping_title_row)

        self.mapping_list = QListWidget()
        self.mapping_list.setObjectName("mappingList")
        self.mapping_list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        mapping_layout.addWidget(self.mapping_list, 1)
        content.addWidget(mapping_card, 4)

        scroll_content = QWidget()
        scroll_content.setObjectName("page")
        scroll_content.setLayout(content)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_scroll.setWidget(scroll_content)
        page_layout.addWidget(self.content_scroll, 1)

        self.error_banner = QLabel()
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        page_layout.addWidget(self.error_banner)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        page_layout.addWidget(self.progress_bar)

        self.creation_status = QLabel()
        self.creation_status.setObjectName("jsonStatus")
        self.creation_status.setWordWrap(True)
        self.creation_status.hide()
        page_layout.addWidget(self.creation_status)

        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._cancel_requested)
        footer.addWidget(self.cancel_button)
        self.create_button = QPushButton("创建项目组")
        self.create_button.setProperty("role", "primary")
        self.create_button.clicked.connect(self._create_project_group)
        footer.addWidget(self.create_button)
        page_layout.addLayout(footer)
        self._form_controls.extend(
            [
                self.name_input,
                self.count_input,
                self.location_input,
                self.choose_location_button,
                self.import_button,
                self.mapping_list,
                *self.tool_inputs.values(),
            ]
        )
        self._update_json_summary()

    def refresh_public_tools(self) -> None:
        for role, path_input in self.tool_inputs.items():
            value = path_input.text().strip()
            label = self.tool_status_labels[role]
            if not value:
                label.setText("未选择，当前不能创建项目组。")
                label.setProperty("status", "warning")
            else:
                path = Path(value).expanduser()
                if not path.is_file():
                    label.setText(f"路径无效：{path}")
                    label.setProperty("status", "warning")
                elif path.stat().st_size == 0:
                    label.setText(f"文件为空：{path}")
                    label.setProperty("status", "warning")
                else:
                    label.setText(f"已选择：{path.name} · {path.stat().st_size:,} B")
                    label.setProperty("status", "normal")
            label.style().unpolish(label)
            label.style().polish(label)
        self._update_create_state()

    def _tools_changed(self) -> None:
        self._validation_generation += 1
        self._tool_validation_result = None
        self._validated_binding_key = None
        self.refresh_public_tools()
        if self._basic_tools_ready() and not self._creation_in_progress:
            self.creation_status.setText("正在等待验证公共工具…")
            self.creation_status.show()
            self._prevalidation_timer.start()
        else:
            self._prevalidation_timer.stop()

    def _choose_tool_file(self, role: str, file_filter: str) -> None:
        current = self.tool_inputs[role].text().strip()
        selected, _ = QFileDialog.getOpenFileName(
            self,
            f"选择真实 {role} 文件",
            str(Path(current).parent) if current else "",
            file_filter,
        )
        if selected:
            self.tool_inputs[role].setText(selected)

    def set_tool_paths(self, workflow: Path, template: Path, validate: Path) -> None:
        self.tool_inputs["workflow"].setText(str(workflow))
        self.tool_inputs["template"].setText(str(template))
        self.tool_inputs["validate"].setText(str(validate))
        self.refresh_public_tools()

    def _binding_key(self, binding: ToolBinding | None = None) -> tuple[str, str, str] | None:
        value = binding or self._tool_binding()
        if value is None:
            return None
        return tuple(
            os.path.normcase(str(path.expanduser().resolve()))
            for path in (value.workflow, value.template, value.validate)
        )

    def _basic_tools_ready(self) -> bool:
        return bool(self.tool_inputs) and all(
            field.text().strip()
            and Path(field.text().strip()).expanduser().is_file()
            and Path(field.text().strip()).expanduser().stat().st_size > 0
            for field in self.tool_inputs.values()
        )

    def _start_tool_prevalidation(self) -> None:
        if self._tool_validation_in_progress or self._creation_in_progress:
            return
        binding = self._tool_binding()
        key = self._binding_key(binding)
        if binding is None or key is None or not self._basic_tools_ready():
            return
        generation = self._validation_generation
        self._tool_validation_in_progress = True
        self._tool_validation_result = None
        self.creation_status.setText("正在验证工具兼容性…")
        self.creation_status.show()
        self.progress_bar.show()
        self._update_create_state()

        def operation(progress):
            return self.project_service.validate_tool_binding(binding, progress)

        def succeeded(result: ToolValidationResult) -> None:
            if generation != self._validation_generation or key != self._binding_key():
                return
            self._tool_validation_result = result
            self._validated_binding_key = key
            details = [
                "✓ workflow 可读取",
                "✓ template 可读取",
                "✓ validate 语法通过",
                "✓ template 已通过 validate",
                "✓ 三份工具可用于创建",
            ]
            if result.warnings:
                details.extend(f"提示：{warning}" for warning in result.warnings)
            self.creation_status.setText("\n".join(details))
            for label in self.tool_status_labels.values():
                label.setProperty("status", "normal")

        def failed(exc: BaseException) -> None:
            if generation != self._validation_generation:
                return
            self._tool_validation_result = None
            self._validated_binding_key = None
            self.creation_status.setText(f"公共工具验证未通过：{exc}")
            self._show_error(str(exc))

        def finished() -> None:
            self._tool_validation_in_progress = False
            self.progress_bar.hide()
            if generation != self._validation_generation and self._basic_tools_ready():
                self._prevalidation_timer.start()
            self._update_create_state()

        self._run_background(operation, succeeded, failed, finished)

    def _tool_binding(self) -> ToolBinding | None:
        values = {role: field.text().strip() for role, field in self.tool_inputs.items()}
        if not all(values.values()):
            return None
        return ToolBinding(
            workflow=Path(values["workflow"]),
            template=Path(values["template"]),
            validate=Path(values["validate"]),
        )

    def _default_desktop_path(self) -> str:
        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
        return desktop or str(Path.home() / "Desktop")

    def _choose_location(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择创建位置",
            self.location_input.text().strip() or self._default_desktop_path(),
        )
        if selected:
            self.location_input.setText(selected)

    def _choose_json_files(self) -> None:
        logger.info("Opening JSON file selector")
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择原始 JSON",
            "",
            "JSON 文件 (*.json)",
        )
        logger.info("JSON file selector returned %d file(s)", len(selected))
        if not selected:
            return
        added, ignored = self.add_json_files([Path(path) for path in selected])
        self._hide_error()
        if ignored:
            self._set_json_status(
                f"已添加 {added} 个文件，已忽略 {ignored} 个重复文件。",
                warning=True,
            )

    def add_json_files(self, paths: list[Path]) -> tuple[int, int]:
        existing = {self._path_key(path) for path in self.json_files}
        added = 0
        ignored = 0
        for path in paths:
            candidate = Path(path)
            key = self._path_key(candidate)
            if key in existing:
                ignored += 1
                continue
            self.json_files.append(candidate)
            existing.add(key)
            added += 1
        self._refresh_mapping_list()
        return added, ignored

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def _move_mapping(self, offset: int) -> None:
        row = self.mapping_list.currentRow()
        new_row = row + offset
        if row < 0 or new_row < 0 or new_row >= len(self.json_files):
            return
        self.json_files[row], self.json_files[new_row] = (
            self.json_files[new_row],
            self.json_files[row],
        )
        self._refresh_mapping_list()
        self.mapping_list.setCurrentRow(new_row)

    def _remove_mapping(self) -> None:
        row = self.mapping_list.currentRow()
        if row < 0 or row >= len(self.json_files):
            return
        self.json_files.pop(row)
        self._refresh_mapping_list()
        if self.json_files:
            self.mapping_list.setCurrentRow(min(row, len(self.json_files) - 1))

    def _refresh_mapping_list(self) -> None:
        self.mapping_list.clear()
        for index, path in enumerate(self.json_files, start=1):
            item = QListWidgetItem(f"项目{index}    →    {path.name}")
            item.setToolTip(str(path))
            self.mapping_list.addItem(item)
        self._update_json_summary()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if event.size().width() < 1050
            else QBoxLayout.Direction.LeftToRight
        )
        if self.content_layout.direction() != direction:
            self.content_layout.setDirection(direction)

    def _update_json_summary(self) -> None:
        selected = len(self.json_files)
        required = self.count_input.value()
        self.json_summary.setText(f"已选择 {selected} / {required}")
        if selected < required:
            missing = required - selected
            self._set_json_status(f"还需要 {missing} 个 JSON 文件。")
        elif selected > required:
            self._set_json_status(
                f"当前已选择 {selected} 个 JSON，但项目数量为 {required}，"
                "请删除多余文件或调整项目数量。",
                warning=True,
            )
        else:
            self._set_json_status("JSON 数量与项目数量一致。")
        self._update_create_state()

    def _update_create_state(self) -> None:
        json_ready = len(self.json_files) == self.count_input.value()
        tools_ready = bool(
            self._tool_validation_result
            and self._validated_binding_key == self._binding_key()
        )
        self.create_button.setEnabled(
            bool(json_ready and tools_ready and not self._creation_in_progress)
        )

    def _set_json_status(self, message: str, warning: bool = False) -> None:
        self.json_status.setText(message)
        self.json_status.setProperty("status", "warning" if warning else "normal")
        self.json_status.style().unpolish(self.json_status)
        self.json_status.style().polish(self.json_status)

    def _create_project_group(self) -> None:
        if self._creation_in_progress:
            return
        self._hide_error()
        selected = len(self.json_files)
        required = self.count_input.value()
        if selected < required:
            self._show_error(f"还需要选择 {required - selected} 个 JSON 文件。")
            return
        if selected > required:
            self._show_error(
                f"当前已选择 {selected} 个 JSON，但项目数量为 {required}，"
                "请删除多余文件或调整项目数量。"
            )
            return
        tool_binding = self._tool_binding()
        if tool_binding is None:
            self._show_error(
                "缺少真实公共工具。请分别选择 workflow、template、validate 文件；"
                "当前不会回退到内置默认文件。"
            )
            return
        if self._tool_validation_in_progress:
            self._show_error("正在验证公共工具，请稍候…")
            return
        validation_result = self._tool_validation_result
        if validation_result is None or self._validated_binding_key != self._binding_key(tool_binding):
            self._show_error("公共工具尚未完成验证，请稍候或重新选择文件。")
            self._prevalidation_timer.start()
            return
        logger.info("Project creation started; project_count=%d", required)
        self._creation_in_progress = True
        self._set_creation_busy(True, "正在检查项目名称和 JSON 映射…")
        group_name = self.name_input.text()
        project_count = self.count_input.value()
        location = Path(self.location_input.text().strip())
        json_files = list(self.json_files)

        def operation(progress):
            return self.project_service.create_project_group(
                group_name=group_name,
                project_count=project_count,
                location=location,
                json_files=json_files,
                tool_binding=tool_binding,
                validation_result=validation_result,
                progress=progress,
            )

        def succeeded(group) -> None:
            logger.info("Project creation succeeded; project_count=%d", len(group.projects))
            self.project_created.emit(group.root)

        def failed(exc: BaseException) -> None:
            if isinstance(exc, TargetExistsError):
                self._show_target_exists(exc)
                return
            logger.error("Project creation failed: %s", type(exc).__name__, exc_info=exc)
            self._show_error(str(exc))

        def finished() -> None:
            self._creation_in_progress = False
            self._set_creation_busy(False)

        self._run_background(operation, succeeded, failed, finished)

    def _show_target_exists(self, exc: TargetExistsError) -> None:
        logger.warning("Project creation stopped because target already exists")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("目标目录已存在")
        box.setText(str(exc))
        box.setInformativeText("不会覆盖任何已有内容。")
        open_button = box.addButton("打开现有项目组", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("返回修改", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_button:
            self.open_existing_requested.emit(exc.path)

    def _set_creation_busy(self, busy: bool, message: str = "") -> None:
        for control in self._form_controls:
            control.setEnabled(not busy)
        self.back_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.create_button.setText("正在创建…" if busy else "创建项目组")
        self.progress_bar.setVisible(busy)
        if busy:
            self.creation_status.setText(message)
            self.creation_status.show()
        self._update_create_state()

    def _cancel_requested(self) -> None:
        if self._creation_in_progress:
            self._show_error("项目组正在创建，请等待当前任务完成。")
            return
        self.cancelled.emit()

    def _run_background(self, operation, succeeded, failed, finished) -> None:
        thread = QThread(self)
        worker = BackgroundWorker(operation)
        relay = BackgroundTaskRelay(succeeded, failed, finished, self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stage_changed.connect(self.creation_status.setText)
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

    def _show_error(self, message: str) -> None:
        self.error_banner.setText(message)
        self.error_banner.show()

    def _hide_error(self) -> None:
        self.error_banner.hide()
        self.error_banner.clear()
