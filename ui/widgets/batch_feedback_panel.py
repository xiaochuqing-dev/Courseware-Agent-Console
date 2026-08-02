from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models import ProjectGroup
from services import (
    BatchFeedbackSaveResult,
    BatchFeedbackService,
    BatchPlanChangedError,
    BatchRoundTarget,
    BatchTaskGenerationResult,
    FeedbackService,
    PendingFeedback,
)
from ui.workers import BackgroundTaskRelay, BackgroundWorker

from .feedback_drop import FeedbackDropArea, PendingFeedbackRow
from .glass_check_box import GlassCheckBox
from .prompt_dialog import PromptDialog


HighContrastCheckBox = GlassCheckBox


class BatchFeedbackPanel(QWidget):
    error_requested = Signal(str)
    toast_requested = Signal(str)
    feedback_saved = Signal()
    project_requested = Signal(str, int)

    def __init__(
        self,
        batch_service: BatchFeedbackService,
        feedback_service: FeedbackService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.batch_service = batch_service
        self.feedback_service = feedback_service
        self.group: ProjectGroup | None = None
        self.selected_project_ids: set[str] = set()
        self.project_hints: dict[str, str] = {}
        self.pending_feedback: list[PendingFeedback] = []
        self.round_targets: tuple[BatchRoundTarget, ...] = ()
        self.saved_result: BatchFeedbackSaveResult | None = None
        self.task_result: BatchTaskGenerationResult | None = None
        self._operation_in_progress = False
        self._threads: set[QThread] = set()
        self._workers: set[BackgroundWorker] = set()
        self._relays: set[BackgroundTaskRelay] = set()
        self._prompt_dialog: PromptDialog | None = None
        self._project_checkboxes: dict[str, GlassCheckBox] = {}
        self.result_project_buttons: dict[str, QPushButton] = {}
        self._build_ui()
        self._update_actions()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        selection_header = QHBoxLayout()
        selection_title = QLabel("选择目标课件")
        selection_title.setObjectName("fieldLabel")
        selection_header.addWidget(selection_title)
        self.selected_count_label = QLabel("已选择 0 个课件")
        self.selected_count_label.setObjectName("mutedText")
        selection_header.addWidget(self.selected_count_label)
        selection_header.addStretch()
        self.select_all_button = QPushButton("全选")
        self.select_all_button.setProperty("role", "quiet")
        self.select_all_button.clicked.connect(lambda: self._set_all_selected(True))
        selection_header.addWidget(self.select_all_button)
        self.select_none_button = QPushButton("全不选")
        self.select_none_button.setProperty("role", "quiet")
        self.select_none_button.clicked.connect(lambda: self._set_all_selected(False))
        selection_header.addWidget(self.select_none_button)
        layout.addLayout(selection_header)

        self.round_rule_label = QLabel(
            "固定规则：每个选中课件独立创建各自下一轮，不要求轮次数字一致。"
        )
        self.round_rule_label.setObjectName("batchRoundRule")
        self.round_rule_label.setWordWrap(True)
        layout.addWidget(self.round_rule_label)

        self.preview_error_label = QLabel()
        self.preview_error_label.setObjectName("errorBanner")
        self.preview_error_label.setWordWrap(True)
        self.preview_error_label.hide()
        layout.addWidget(self.preview_error_label)

        self.project_table = QTableWidget(0, 5)
        self.project_table.setObjectName("batchProjectTable")
        self.project_table.setHorizontalHeaderLabels(
            ["选择", "课件名称", "当前最新轮次", "本次目标轮次", "本课件提示（可选）"]
        )
        self.project_table.verticalHeader().hide()
        self.project_table.setWordWrap(True)
        self.project_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.project_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.project_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        header = self.project_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 58)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.project_table.setMinimumHeight(176)
        self.project_table.setMaximumHeight(280)
        layout.addWidget(self.project_table)

        material_title = QLabel("统一反馈材料")
        material_title.setObjectName("fieldLabel")
        layout.addWidget(material_title)
        self.drop_area = FeedbackDropArea()
        self.drop_area.mime_received.connect(self._receive_mime_data)
        self.drop_area.browse_requested.connect(self._choose_files)
        layout.addWidget(self.drop_area)

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

        note_label = QLabel("批量补充说明（可选）")
        note_label.setObjectName("fieldLabel")
        layout.addWidget(note_label)
        self.batch_note_input = QPlainTextEdit()
        self.batch_note_input.setPlaceholderText("例如：Word 第一部分对应课件 A，第二部分对应课件 B")
        self.batch_note_input.setMinimumHeight(64)
        self.batch_note_input.setMaximumHeight(86)
        self.batch_note_input.textChanged.connect(self._update_actions)
        layout.addWidget(self.batch_note_input)

        self.operation_status_label = QLabel()
        self.operation_status_label.setObjectName("mutedText")
        self.operation_status_label.hide()
        layout.addWidget(self.operation_status_label)

        actions = QHBoxLayout()
        self.new_batch_button = QPushButton("开始新批次")
        self.new_batch_button.setProperty("role", "quiet")
        self.new_batch_button.clicked.connect(self.start_new_batch)
        self.new_batch_button.hide()
        actions.addWidget(self.new_batch_button)
        actions.addStretch()
        self.save_button = QPushButton("保存批量反馈")
        self.save_button.setProperty("role", "primary")
        self.save_button.clicked.connect(self._save_batch)
        actions.addWidget(self.save_button)
        self.generate_tasks_button = QPushButton("生成所选课件反馈任务")
        self.generate_tasks_button.clicked.connect(self._generate_tasks)
        actions.addWidget(self.generate_tasks_button)
        self.copy_instruction_button = QPushButton("复制批量执行指令")
        self.copy_instruction_button.clicked.connect(self._copy_instruction)
        actions.addWidget(self.copy_instruction_button)
        layout.addLayout(actions)

        self.result_label = QLabel("尚未保存批量反馈。")
        self.result_label.setObjectName("batchResult")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.result_label)
        self.result_projects = QWidget()
        self.result_projects_layout = QVBoxLayout(self.result_projects)
        self.result_projects_layout.setContentsMargins(0, 0, 0, 0)
        self.result_projects_layout.setSpacing(6)
        self.result_projects.hide()
        layout.addWidget(self.result_projects)
        self._refresh_materials()

    def set_group(self, group: ProjectGroup, preserve: bool = False) -> None:
        previous_ids = set(self.selected_project_ids) if preserve else set()
        previous_hints = dict(self.project_hints) if preserve else {}
        removed = previous_ids - {project.project_id for project in group.projects}
        self.group = group
        if not preserve:
            self._clear_state()
        self.selected_project_ids = previous_ids & {
            project.project_id for project in group.projects
        }
        self.project_hints = {
            project_id: hint
            for project_id, hint in previous_hints.items()
            if project_id in {project.project_id for project in group.projects}
        }
        self._populate_project_table()
        if removed:
            self.toast_requested.emit(
                f"刷新后已取消 {len(removed)} 个不存在的批量目标课件。"
            )
        self._refresh_preview()

    def clear_group(self) -> None:
        self.group = None
        self._clear_state()
        self._populate_project_table()
        self._refresh_preview()

    def has_unsaved_content(self) -> bool:
        if self.saved_result is not None:
            return False
        return bool(
            self.pending_feedback
            or self.batch_note_input.toPlainText().strip()
            or any(value.strip() for value in self.project_hints.values())
        )

    def discard_unsaved_content(self) -> None:
        if self.saved_result is None:
            self._clear_state()
            self._populate_project_table()
            self._refresh_preview()

    def start_new_batch(self) -> None:
        if self._operation_in_progress:
            return
        self._clear_state()
        self._populate_project_table()
        self._refresh_preview()

    def refresh_preview(self) -> None:
        self._refresh_preview()

    def _clear_state(self) -> None:
        self.selected_project_ids.clear()
        self.project_hints.clear()
        self.pending_feedback.clear()
        self.round_targets = ()
        self.saved_result = None
        self.task_result = None
        if hasattr(self, "batch_note_input"):
            self.batch_note_input.clear()
            self.result_label.setText("尚未保存批量反馈。")
            self.new_batch_button.hide()
            self._clear_result_projects()
            self._refresh_materials()

    def _populate_project_table(self) -> None:
        self.project_table.blockSignals(True)
        self.project_table.setRowCount(0)
        self._project_checkboxes.clear()
        if not self.group:
            self.project_table.blockSignals(False)
            return
        for row, project in enumerate(self.group.projects):
            self.project_table.insertRow(row)
            checkbox = GlassCheckBox()
            checkbox.setAccessibleName(f"选择课件 {project.display_name}")
            checkbox.setChecked(project.project_id in self.selected_project_ids)
            checkbox.toggled.connect(
                lambda selected, project_id=project.project_id: self._project_toggled(
                    project_id, selected
                )
            )
            self._project_checkboxes[project.project_id] = checkbox
            self.project_table.setCellWidget(row, 0, checkbox)

            name_item = QTableWidgetItem(project.display_name)
            name_item.setToolTip(project.display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, project.project_id)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.project_table.setItem(row, 1, name_item)
            latest = self.feedback_service.latest_round(project.path)
            latest_item = QTableWidgetItem("无" if latest is None else f"第{latest}轮")
            latest_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.project_table.setItem(row, 2, latest_item)
            target_item = QTableWidgetItem(f"第{(latest or 0) + 1}轮")
            target_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.project_table.setItem(row, 3, target_item)
            hint = QLineEdit()
            hint.setPlaceholderText("对应材料中的位置")
            hint.setText(self.project_hints.get(project.project_id, ""))
            hint.textChanged.connect(
                lambda value, project_id=project.project_id: self._hint_changed(
                    project_id, value
                )
            )
            self.project_table.setCellWidget(row, 4, hint)
        self.project_table.blockSignals(False)
        self.project_table.resizeRowsToContents()

    def _project_toggled(self, project_id: str, selected: bool) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        if selected:
            self.selected_project_ids.add(project_id)
        else:
            self.selected_project_ids.discard(project_id)
        self._refresh_preview()

    def _hint_changed(self, project_id: str, value: str) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        self.project_hints[project_id] = value
        self._update_actions()

    def _set_all_selected(self, selected: bool) -> None:
        if not self.group or self._operation_in_progress or self.saved_result:
            return
        self.selected_project_ids = (
            {project.project_id for project in self.group.projects} if selected else set()
        )
        for checkbox in self._project_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(selected)
            checkbox.blockSignals(False)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        selected_count = len(self.selected_project_ids)
        self.selected_count_label.setText(f"已选择 {selected_count} 个课件")
        if self.saved_result is not None:
            self._update_actions()
            return
        self.preview_error_label.hide()
        self.round_targets = ()
        if self.group and selected_count >= 2:
            try:
                self.round_targets = self.batch_service.preview_rounds(
                    self.group.root,
                    self.selected_project_ids,
                    BatchFeedbackService.STRATEGY_NEXT,
                )
            except Exception as exc:
                self.preview_error_label.setText(str(exc))
                self.preview_error_label.show()
        targets_by_id = {target.project_id: target for target in self.round_targets}
        for row in range(self.project_table.rowCount()):
            project_id = str(
                self.project_table.item(row, 1).data(Qt.ItemDataRole.UserRole) or ""
            )
            target = targets_by_id.get(project_id)
            latest_text = self.project_table.item(row, 2).text()
            latest = None if latest_text == "无" else int(latest_text[1:-1])
            self.project_table.item(row, 3).setText(
                f"第{target.target_round if target else (latest or 0) + 1}轮"
            )
            hint_input = self.project_table.cellWidget(row, 4)
            if hint_input is not None:
                hint_input.setEnabled(
                    project_id in self.selected_project_ids
                    and not self._operation_in_progress
                    and self.saved_result is None
                )
        self.project_table.resizeRowsToContents()
        self._update_actions()

    def _choose_files(self) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择统一反馈材料",
            "",
            "支持的反馈材料 (*.docx *.doc *.pdf *.txt *.png *.jpg *.jpeg)",
        )
        if selected:
            self.add_files([Path(path) for path in selected])

    def add_files(self, paths: list[Path]) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        errors: list[str] = []
        existing_paths = {
            os.path.normcase(str(item.source_path.resolve()))
            for item in self.pending_feedback
            if item.source_path is not None
        }
        existing_names = {
            item.name.casefold(): item.source_path for item in self.pending_feedback
        }
        fingerprints = {
            item.fingerprint for item in self.pending_feedback if item.fingerprint
        }
        for raw_path in paths:
            try:
                if raw_path.is_symlink():
                    raise ValueError("不支持符号链接，请选择真实反馈文件。")
                path_key = os.path.normcase(str(raw_path.resolve(strict=True)))
                if path_key in existing_paths:
                    continue
                item = self.feedback_service.pending_from_file(raw_path)
                if item.name.casefold() in existing_names:
                    raise ValueError("存在不同来源的同名材料，请先重命名源文件。")
                if item.fingerprint and item.fingerprint in fingerprints:
                    errors.append(f"{raw_path.name}：内容与待保存材料重复，已忽略。")
                    continue
            except Exception as exc:
                errors.append(f"{raw_path.name or raw_path}：{exc}")
                continue
            self.pending_feedback.append(item)
            existing_paths.add(path_key)
            existing_names[item.name.casefold()] = item.source_path
            fingerprints.add(item.fingerprint)
        self._refresh_materials()
        if errors:
            self.error_requested.emit("部分资料未加入：\n" + "\n".join(errors))

    def _receive_mime_data(self, mime) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        if mime.hasUrls():
            files = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
            if files:
                self.add_files(files)
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
                    item = self.feedback_service.pending_from_bytes(
                        name,
                        bytes(buffer.data()),
                        "image",
                        {current.name for current in self.pending_feedback},
                    )
                    self._append_pending(item)
                except Exception as exc:
                    self.error_requested.emit(f"无法接收剪贴板图片：{exc}")
                return
        if mime.hasText():
            try:
                item = self.feedback_service.pending_from_text(
                    mime.text(), {current.name for current in self.pending_feedback}
                )
                self._append_pending(item)
            except Exception as exc:
                self.error_requested.emit(f"无法接收剪贴板文字：{exc}")
            return
        self.error_requested.emit("剪贴板中没有可导入的图片、文字或文件。")

    def _append_pending(self, item: PendingFeedback) -> None:
        if item.fingerprint and any(
            current.fingerprint == item.fingerprint
            for current in self.pending_feedback
        ):
            self.error_requested.emit("该内容已在待保存列表中，未重复添加。")
            return
        self.pending_feedback.append(item)
        self._refresh_materials()

    def _refresh_materials(self) -> None:
        while self.pending_layout.count():
            item = self.pending_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self.pending_feedback:
            empty = QLabel("本批次材料会复制到每个已选课件自己的目标反馈轮次。")
            empty.setObjectName("mutedText")
            self.pending_layout.addWidget(empty)
        else:
            read_only = self.saved_result is not None
            for feedback in self.pending_feedback:
                row = PendingFeedbackRow(feedback, read_only=read_only)
                row.remove_requested.connect(self._remove_pending)
                row.preview_requested.connect(self._preview_pending)
                row.edit_requested.connect(self._edit_pending)
                self.pending_layout.addWidget(row)
        self.pending_count_label.setText(f"{len(self.pending_feedback)} 项")
        self._update_actions()

    def _remove_pending(self, item_id: str) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        item = next(
            (current for current in self.pending_feedback if current.item_id == item_id),
            None,
        )
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "确认移除待保存反馈",
            f"将从本批次移除：\n{item.name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.pending_feedback = [
            current for current in self.pending_feedback if current.item_id != item_id
        ]
        self._refresh_materials()

    def _preview_pending(self, item_id: str) -> None:
        item = next(
            (current for current in self.pending_feedback if current.item_id == item_id),
            None,
        )
        if item is None:
            return
        if item.kind == "text":
            try:
                text = (
                    item.content.decode("utf-8-sig")
                    if item.content is not None
                    else item.source_path.read_text(encoding="utf-8-sig")
                    if item.source_path is not None
                    else item.preview
                )
            except Exception as exc:
                self.error_requested.emit(f"无法预览 {item.name}：{exc}")
                return
            self._prompt_dialog = PromptDialog(item.name, text, self, "复制全文")
            self._prompt_dialog.exec()
        elif item.source_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(item.source_path)))

    def _edit_pending(self, item_id: str) -> None:
        if self._operation_in_progress or self.saved_result:
            return
        item = next(
            (current for current in self.pending_feedback if current.item_id == item_id),
            None,
        )
        if item is None or item.kind != "text":
            return
        original = (
            item.content.decode("utf-8-sig")
            if item.content is not None
            else item.preview
        )
        from PySide6.QtWidgets import QInputDialog

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
        self._refresh_materials()

    def _save_batch(self) -> None:
        if (
            self._operation_in_progress
            or self.saved_result
            or not self.group
            or len(self.round_targets) < 2
            or not self.pending_feedback
        ):
            return
        group_root = self.group.root
        project_ids = tuple(target.project_id for target in self.round_targets)
        strategy = BatchFeedbackService.STRATEGY_NEXT
        items = tuple(self.pending_feedback)
        note = self.batch_note_input.toPlainText()
        hints = dict(self.project_hints)
        expected = tuple(self.round_targets)
        self._set_busy(True, "正在预检全部课件…")

        def operation(progress):
            return self.batch_service.save_batch(
                group_root,
                project_ids,
                strategy,
                items,
                note,
                hints,
                expected,
                progress=progress,
            )

        self._run_background(
            operation,
            self._save_succeeded,
            self._save_failed,
            lambda: self._set_busy(False),
        )

    def _save_succeeded(self, result: object) -> None:
        if not isinstance(result, BatchFeedbackSaveResult):
            self.error_requested.emit("批量反馈保存结果格式无效。")
            return
        self.saved_result = result
        self.pending_feedback = [
            replace(item, status="批量已保存") for item in self.pending_feedback
        ]
        lines = ["批量反馈已保存", ""]
        lines.extend(
            f"✓ {target.display_name} → 第{target.target_round}轮"
            for target in result.targets
        )
        lines.extend(
            [
                "",
                "材料：" + "、".join(result.material_names),
                f"批次记录：{result.record_path}",
            ]
        )
        self.result_label.setText("\n".join(lines))
        self._refresh_result_projects(result.targets)
        self.new_batch_button.show()
        self._refresh_materials()
        self.feedback_saved.emit()
        self.toast_requested.emit("批量反馈已全部保存")

    def _clear_result_projects(self) -> None:
        self.result_project_buttons.clear()
        if not hasattr(self, "result_projects_layout"):
            return
        while self.result_projects_layout.count():
            item = self.result_projects_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.result_projects.hide()

    def _refresh_result_projects(
        self, targets: tuple[BatchRoundTarget, ...]
    ) -> None:
        self._clear_result_projects()
        for target in targets:
            row = QWidget()
            row.setObjectName("batchResultProjectRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 5, 8, 5)
            label = QLabel(
                f"{target.display_name} → 已创建第{target.target_round}轮"
            )
            label.setWordWrap(True)
            row_layout.addWidget(label, 1)
            button = QPushButton("查看项目")
            button.setProperty("role", "quiet")
            button.clicked.connect(
                lambda _checked=False,
                project_id=target.project_id,
                round_number=target.target_round: self.project_requested.emit(
                    project_id, round_number
                )
            )
            row_layout.addWidget(button)
            self.result_project_buttons[target.project_id] = button
            self.result_projects_layout.addWidget(row)
        self.result_projects.setVisible(bool(targets))

    def _save_failed(self, error: object) -> None:
        if isinstance(error, BatchPlanChangedError):
            self._refresh_preview()
        else:
            self._refresh_preview()
        self.error_requested.emit(f"批量反馈保存失败：{error}")

    def _generate_tasks(self) -> None:
        if self._operation_in_progress or not self.saved_result or self.task_result:
            return
        record_path = self.saved_result.record_path
        self._set_busy(True, "正在预检全部课件任务…")

        def operation(progress):
            return self.batch_service.generate_tasks(record_path, progress)

        self._run_background(
            operation,
            self._tasks_succeeded,
            self._tasks_failed,
            lambda: self._set_busy(False),
        )

    def _tasks_succeeded(self, result: object) -> None:
        if not isinstance(result, BatchTaskGenerationResult):
            self.error_requested.emit("批量任务生成结果格式无效。")
            return
        self.task_result = result
        current = self.result_label.text().rstrip()
        self.result_label.setText(
            current
            + f"\n\n✓ {len(result.project_task_paths)} 个项目反馈任务已生成"
            + f"\n✓ {result.batch_task_path.name} 已生成"
        )
        self.toast_requested.emit("所有课件反馈任务已生成")
        self._update_actions()

    def _tasks_failed(self, error: object) -> None:
        self.error_requested.emit(f"批量反馈任务生成失败：{error}")

    def _copy_instruction(self) -> None:
        if not self.saved_result:
            return
        try:
            instruction = self.batch_service.batch_execution_instruction(
                self.saved_result.record_path
            )
        except Exception as exc:
            self.copy_instruction_button.setEnabled(False)
            self.error_requested.emit(f"无法复制批量执行指令：{exc}")
            return
        QGuiApplication.clipboard().setText(instruction)
        self.toast_requested.emit("批量执行指令已复制")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._operation_in_progress = busy
        self.operation_status_label.setVisible(busy)
        if busy:
            self.operation_status_label.setText(message)
        self._update_actions()

    def _update_actions(self) -> None:
        locked = self._operation_in_progress or self.saved_result is not None
        selection_ready = len(self.round_targets) >= 2
        self.select_all_button.setEnabled(bool(self.group) and not locked)
        self.select_none_button.setEnabled(bool(self.group) and not locked)
        self.project_table.setEnabled(bool(self.group) and not locked)
        self.drop_area.setEnabled(bool(self.group) and not locked)
        self.batch_note_input.setEnabled(bool(self.group) and not locked)
        self.save_button.setEnabled(
            bool(
                self.group
                and selection_ready
                and self.pending_feedback
                and not self._operation_in_progress
                and self.saved_result is None
            )
        )
        self.generate_tasks_button.setEnabled(
            self.saved_result is not None
            and self.task_result is None
            and not self._operation_in_progress
        )
        copy_valid = bool(
            self.saved_result
            and self.task_result
            and not self._operation_in_progress
            and self.batch_service.is_batch_instruction_valid(
                self.saved_result.record_path
            )
        )
        self.copy_instruction_button.setEnabled(copy_valid)
        self.new_batch_button.setEnabled(
            self.saved_result is not None and not self._operation_in_progress
        )

    def _run_background(self, operation, succeeded, failed, finished) -> None:
        thread = QThread(self)
        worker = BackgroundWorker(operation)
        relay = BackgroundTaskRelay(succeeded, failed, finished, self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stage_changed.connect(self.operation_status_label.setText)
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
