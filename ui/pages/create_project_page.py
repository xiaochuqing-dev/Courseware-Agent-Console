from __future__ import annotations

import logging
import json
import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
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
from ui.widgets import Card, FlowLayout, configure_wrapped_list
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
        self.project_names_by_path: dict[str, str] = {}
        self.materials_by_project: dict[str, list[Path]] = {}
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
        mapping_title = QLabel("项目映射与首次材料")
        mapping_title.setObjectName("sectionTitle")
        mapping_title_row.addWidget(mapping_title)
        mapping_title_row.addStretch()

        self.mapping_up_button = QPushButton()
        self.mapping_up_button.setProperty("iconOnly", True)
        self.mapping_up_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
        )
        self.mapping_up_button.setToolTip("上移所选 JSON")
        self.mapping_up_button.clicked.connect(lambda: self._move_mapping(-1))
        mapping_title_row.addWidget(self.mapping_up_button)

        self.mapping_down_button = QPushButton()
        self.mapping_down_button.setProperty("iconOnly", True)
        self.mapping_down_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self.mapping_down_button.setToolTip("下移所选 JSON")
        self.mapping_down_button.clicked.connect(lambda: self._move_mapping(1))
        mapping_title_row.addWidget(self.mapping_down_button)

        self.mapping_remove_button = QPushButton()
        self.mapping_remove_button.setProperty("iconOnly", True)
        self.mapping_remove_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self.mapping_remove_button.setToolTip("删除所选 JSON")
        self.mapping_remove_button.clicked.connect(self._remove_mapping)
        mapping_title_row.addWidget(self.mapping_remove_button)
        self.mapping_edit_button = QPushButton("编辑名称")
        self.mapping_edit_button.setToolTip("编辑所选项目名称")
        self.mapping_edit_button.clicked.connect(self._edit_mapping_name)
        mapping_title_row.addWidget(self.mapping_edit_button)
        mapping_layout.addLayout(mapping_title_row)

        mapping_columns = QLabel("序号  |  项目名称（最终目录）  |  材料数量  |  原始 JSON")
        mapping_columns.setObjectName("fieldLabel")
        mapping_layout.addWidget(mapping_columns)

        self.mapping_list = QListWidget()
        self.mapping_list.setObjectName("mappingList")
        configure_wrapped_list(self.mapping_list, minimum_height=48)
        self.mapping_list.setMaximumHeight(160)
        mapping_layout.addWidget(self.mapping_list, 1)
        self.mapping_list.currentRowChanged.connect(self._refresh_material_panel)

        self.material_project_label = QLabel("请先选择一个项目映射")
        self.material_project_label.setObjectName("fieldLabel")
        self.material_project_label.setWordWrap(True)
        mapping_layout.addWidget(self.material_project_label)

        self.material_json_label = QLabel()
        self.material_json_label.setObjectName("mutedText")
        self.material_json_label.setWordWrap(True)
        mapping_layout.addWidget(self.material_json_label)

        self.material_list = QListWidget()
        self.material_list.setObjectName("materialList")
        self.material_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        configure_wrapped_list(self.material_list, minimum_height=42)
        self.material_list.setMinimumHeight(82)
        self.material_list.setMaximumHeight(100)

        material_buttons = QHBoxLayout()
        self.add_material_button = QPushButton("添加图片/材料")
        self.add_material_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self.add_material_button.clicked.connect(self._choose_material_files)
        material_buttons.addWidget(self.add_material_button)
        self.remove_material_button = QPushButton("移除所选")
        self.remove_material_button.clicked.connect(self._remove_selected_materials)
        material_buttons.addWidget(self.remove_material_button)
        self.clear_materials_button = QPushButton("清空材料")
        self.clear_materials_button.clicked.connect(self._clear_materials)
        material_buttons.addWidget(self.clear_materials_button)
        mapping_layout.addLayout(material_buttons)
        mapping_layout.addWidget(self.material_list, 1)
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
                self.mapping_up_button,
                self.mapping_down_button,
                self.mapping_remove_button,
                self.mapping_edit_button,
                self.material_list,
                self.add_material_button,
                self.remove_material_button,
                self.clear_materials_button,
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
            base_name = candidate.stem
            project_name = base_name
            counter = 2
            used_names = {
                value.casefold() for value in self.project_names_by_path.values()
            }
            used_directories = {
                self.project_service.sanitize_project_name(value).casefold()
                for value in self.project_names_by_path.values()
            }
            while (
                project_name.casefold() in used_names
                or self.project_service.sanitize_project_name(project_name).casefold()
                in used_directories
            ):
                project_name = f"{base_name}（{counter}）"
                counter += 1
            self.project_names_by_path[key] = project_name
            existing.add(key)
            added += 1
        self._refresh_mapping_list()
        return added, ignored

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def _current_json_path(self) -> Path | None:
        row = self.mapping_list.currentRow()
        if row < 0 or row >= len(self.json_files):
            return None
        return self.json_files[row]

    def _choose_material_files(self) -> None:
        json_path = self._current_json_path()
        if json_path is None:
            self._show_error("请先选择要绑定材料的项目映射。")
            return
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "添加首次制作图片/材料",
            "",
            (
                "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.svg);;"
                "文档 (*.pdf *.doc *.docx *.ppt *.pptx *.xls *.xlsx);;"
                "文本 (*.txt *.md *.csv);;所有文件 (*)"
            ),
        )
        if not selected:
            return
        added, ignored = self.add_material_files([Path(path) for path in selected])
        if ignored:
            self._set_json_status(
                f"已添加 {added} 个材料，已忽略 {ignored} 个重复材料。",
                warning=True,
            )

    def add_material_files(self, paths: list[Path]) -> tuple[int, int]:
        json_path = self._current_json_path()
        if json_path is None:
            self._show_error("请先选择要绑定材料的项目映射。")
            return 0, 0
        project_key = self._path_key(json_path)
        materials = self.materials_by_project.setdefault(project_key, [])
        existing = {self._path_key(path) for path in materials}
        added = 0
        ignored = 0
        for path in paths:
            candidate = Path(path)
            key = self._path_key(candidate)
            if key in existing:
                ignored += 1
                continue
            materials.append(candidate)
            existing.add(key)
            added += 1
        display_name = self.project_names_by_path.get(project_key, json_path.stem)
        logger.info(
            "Added initial materials; project=%s; added=%d; ignored_duplicates=%d",
            display_name,
            added,
            ignored,
        )
        self._refresh_mapping_list()
        self.mapping_list.setCurrentRow(
            next(
                (
                    index
                    for index, path in enumerate(self.json_files)
                    if self._path_key(path) == project_key
                ),
                -1,
            )
        )
        self._refresh_material_panel()
        self._hide_error()

        names: dict[str, int] = {json_path.name.casefold(): 1}
        conflicts: set[str] = set()
        for material in materials:
            name_key = material.name.casefold()
            if name_key in names:
                conflicts.add(material.name)
            names[name_key] = names.get(name_key, 0) + 1
        if conflicts:
            self._show_error(
                "材料名称冲突，将阻止创建："
                + "、".join(sorted(conflicts, key=str.casefold))
            )
        return added, ignored

    def _remove_selected_materials(self) -> None:
        json_path = self._current_json_path()
        if json_path is None:
            self._show_error("请先选择一个项目映射。")
            return
        selected_rows = sorted(
            {self.material_list.row(item) for item in self.material_list.selectedItems()},
            reverse=True,
        )
        if not selected_rows:
            self._show_error("请先选择要移除的材料。")
            return
        project_key = self._path_key(json_path)
        materials = self.materials_by_project.get(project_key, [])
        removed = 0
        for row in selected_rows:
            if 0 <= row < len(materials):
                materials.pop(row)
                removed += 1
        logger.info(
            "Removed initial material bindings; project=%s; count=%d",
            self.project_names_by_path.get(project_key, json_path.stem),
            removed,
        )
        self._hide_error()
        self._refresh_mapping_list()
        self._refresh_material_panel()

    def _clear_materials(self) -> None:
        json_path = self._current_json_path()
        if json_path is None:
            self._show_error("请先选择一个项目映射。")
            return
        project_key = self._path_key(json_path)
        count = len(self.materials_by_project.get(project_key, []))
        if not count:
            return
        self.materials_by_project[project_key] = []
        logger.info(
            "Cleared initial material bindings; project=%s; count=%d",
            self.project_names_by_path.get(project_key, json_path.stem),
            count,
        )
        self._hide_error()
        self._refresh_mapping_list()
        self._refresh_material_panel()

    @staticmethod
    def _format_file_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{int(value):,} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size:,} B"

    def _refresh_material_panel(self, _row: int | None = None) -> None:
        json_path = self._current_json_path()
        self.material_list.clear()
        if json_path is None:
            self.material_project_label.setText("请先选择一个项目映射")
            self.material_json_label.clear()
            return
        project_key = self._path_key(json_path)
        display_name = self.project_names_by_path.get(project_key, json_path.stem)
        materials = self.materials_by_project.get(project_key, [])
        self.material_project_label.setText(
            f"当前项目：{display_name} · 已绑定 {len(materials)} 个材料"
        )
        self.material_json_label.setText(f"当前 JSON：{json_path.name}")
        for material in materials:
            try:
                size_text = self._format_file_size(material.stat().st_size)
            except OSError:
                size_text = "文件不可用"
            item = QListWidgetItem(f"{material.name}  |  {size_text}")
            item.setToolTip(str(material.expanduser().resolve()))
            self.material_list.addItem(item)

    def project_materials(self) -> dict[str, list[Path]]:
        return {
            self._path_key(path): list(
                self.materials_by_project.get(self._path_key(path), [])
            )
            for path in self.json_files
        }

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
        json_path = self.json_files[row]
        project_key = self._path_key(json_path)
        material_count = len(self.materials_by_project.get(project_key, []))
        if material_count:
            answer = QMessageBox.question(
                self,
                "删除项目映射",
                (
                    f"项目“{self.project_names_by_path.get(project_key, json_path.stem)}”"
                    f"当前绑定了 {material_count} 个首次制作材料。\n"
                    "确认删除 JSON 映射并清除这些材料绑定吗？"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            logger.info(
                "Removed project material bindings with JSON mapping; project=%s; count=%d",
                self.project_names_by_path.get(project_key, json_path.stem),
                material_count,
            )
        self.json_files.pop(row)
        self.materials_by_project.pop(project_key, None)
        self.project_names_by_path.pop(project_key, None)
        self._refresh_mapping_list()
        if self.json_files:
            self.mapping_list.setCurrentRow(min(row, len(self.json_files) - 1))

    def _refresh_mapping_list(self) -> None:
        selected_path = self._current_json_path()
        selected_key = self._path_key(selected_path) if selected_path else None
        self.mapping_list.clear()
        for index, path in enumerate(self.json_files, start=1):
            key = self._path_key(path)
            display_name = self.project_names_by_path.setdefault(key, path.stem)
            material_count = len(self.materials_by_project.get(key, []))
            directory_name = self.project_service.sanitize_project_name(display_name)
            directory_hint = (
                "" if directory_name == display_name else f"（目录：{directory_name}）"
            )
            item = QListWidgetItem(
                f"{index}  |  {display_name}{directory_hint}"
                f"  |  材料 {material_count} 个  |  {path.name}"
            )
            item.setToolTip(
                f"项目名称：{display_name}\n最终目录：{directory_name}\n"
                f"原始 JSON：{path}\n首次材料：{material_count} 个"
            )
            self.mapping_list.addItem(item)
        if self.json_files:
            restored_row = next(
                (
                    index
                    for index, path in enumerate(self.json_files)
                    if self._path_key(path) == selected_key
                ),
                0,
            )
            self.mapping_list.setCurrentRow(restored_row)
        else:
            self._refresh_material_panel()
        self._update_json_summary()

    def _edit_mapping_name(self) -> None:
        row = self.mapping_list.currentRow()
        if row < 0 or row >= len(self.json_files):
            self._show_error("请先选择一个 JSON 映射。")
            return
        path = self.json_files[row]
        key = self._path_key(path)
        current = self.project_names_by_path.get(key, path.stem)
        value, accepted = QInputDialog.getText(
            self,
            "编辑项目名称",
            "项目名称",
            QLineEdit.EchoMode.Normal,
            current,
        )
        if not accepted:
            return
        name = value.strip()
        if not name:
            self._show_error("项目名称不能为空。")
            return
        self.project_names_by_path[key] = name
        self._hide_error()
        self._refresh_mapping_list()
        self.mapping_list.setCurrentRow(row)

    def project_names(self) -> list[str]:
        return [
            self.project_names_by_path.get(self._path_key(path), path.stem)
            for path in self.json_files
        ]

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
        names_ready = False
        if json_ready:
            try:
                self.project_service.prepare_project_names(
                    self.json_files, self.project_names()
                )
                names_ready = True
            except Exception:
                names_ready = False
        tools_ready = bool(
            self._tool_validation_result
            and self._validated_binding_key == self._binding_key()
        )
        self.create_button.setEnabled(
            bool(json_ready and names_ready and tools_ready and not self._creation_in_progress)
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
        try:
            self.project_service.prepare_project_names(
                self.json_files, self.project_names()
            )
        except Exception as exc:
            self._show_error(str(exc))
            return
        logger.info("Project creation started; project_count=%d", required)
        self._creation_in_progress = True
        self._set_creation_busy(True, "正在验证首次制作材料…")
        group_name = self.name_input.text()
        project_count = self.count_input.value()
        location = Path(self.location_input.text().strip())
        json_files = list(self.json_files)
        project_names = self.project_names()
        project_materials = self.project_materials()
        source_hashes = {
            self._path_key(path): self.project_service.file_sha256(path)
            for path in json_files
        }
        try:
            for path in json_files:
                json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            self._creation_in_progress = False
            self._set_creation_busy(False)
            self._show_error(f"JSON 文件无法解析：{exc}")
            return

        def operation(progress):
            return self.project_service.create_project_group(
                group_name=group_name,
                project_count=project_count,
                location=location,
                json_files=json_files,
                tool_binding=tool_binding,
                validation_result=validation_result,
                progress=progress,
                project_names=project_names,
                source_hashes=source_hashes,
                json_validation_complete=True,
                project_materials=project_materials,
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
