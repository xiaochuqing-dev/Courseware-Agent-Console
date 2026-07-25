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
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import ArchiveService, PromptService
from ui.widgets import Card, PromptDialog, configure_wrapped_list


class WorkflowOptimizationPage(QWidget):
    back_requested = Signal()
    error_requested = Signal(str)
    toast_requested = Signal(str)

    def __init__(
        self,
        archive_service: ArchiveService,
        prompt_service: PromptService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.archive_service = archive_service
        self.prompt_service = prompt_service
        self.group_root: Path | None = None
        self._prompt_dialog: PromptDialog | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(28, 24, 28, 26)
        page_layout.setSpacing(16)

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
        refresh_button.setToolTip("刷新已完成项目")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        page_layout.addLayout(header)

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
        page_layout.addWidget(card, 1)

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
        page_layout.addLayout(footer)
        self._update_selection()

    def set_context(self, group_root: Path) -> None:
        self.group_root = Path(group_root).resolve()
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

    def selected_project_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for index in range(self.project_list.count()):
            item = self.project_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(paths)

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
