from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import ArchiveService, ProjectService
from ui.widgets import Card, ElidedLabel


class CompletedProjectsPage(QWidget):
    back_requested = Signal()
    error_requested = Signal(str)

    def __init__(
        self,
        project_service: ProjectService,
        archive_service: ArchiveService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.project_service = project_service
        self.archive_service = archive_service
        self.group_root: Path | None = None
        self.current_project: Path | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(34, 28, 34, 30)
        page_layout.setSpacing(18)

        header = QHBoxLayout()
        back_button = QPushButton()
        back_button.setProperty("role", "quiet")
        back_button.setProperty("iconOnly", True)
        back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        back_button.setToolTip("返回首页")
        back_button.clicked.connect(self.back_requested)
        header.addWidget(back_button)
        title = QLabel("已完成项目")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel("项目组"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(190)
        self.group_combo.currentTextChanged.connect(self._load_selected_group)
        header.addWidget(self.group_combo)
        refresh_button = QPushButton()
        refresh_button.setProperty("iconOnly", True)
        refresh_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        refresh_button.setToolTip("刷新归档列表")
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(refresh_button)
        page_layout.addLayout(header)

        content = QSplitter(Qt.Orientation.Horizontal)
        content.setChildrenCollapsible(False)
        list_card = Card()
        list_card.setMinimumWidth(240)
        list_layout = QVBoxLayout(list_card)
        list_layout.setContentsMargins(22, 20, 22, 20)
        list_title = QLabel("归档项目")
        list_title.setObjectName("sectionTitle")
        list_layout.addWidget(list_title)
        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.currentItemChanged.connect(self._on_project_selected)
        list_layout.addWidget(self.project_list, 1)
        content.addWidget(list_card)

        details_card = Card()
        details_layout = QVBoxLayout(details_card)
        details_layout.setContentsMargins(28, 24, 28, 24)
        details_layout.setSpacing(13)
        self.project_name_label = QLabel("选择一个已完成项目")
        self.project_name_label.setObjectName("sectionTitle")
        details_layout.addWidget(self.project_name_label)
        self.path_label = ElidedLabel()
        self.path_label.setObjectName("mutedText")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.path_label)
        self.latest_product_label = QLabel("最新产品：无")
        self.latest_product_label.setObjectName("mutedText")
        details_layout.addWidget(self.latest_product_label)
        details_layout.addStretch()

        self.open_project_button = QPushButton("打开项目文件夹")
        self.open_project_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.open_project_button.clicked.connect(self._open_project)
        details_layout.addWidget(self.open_project_button)
        self.open_products_button = QPushButton("打开工作文件目录")
        self.open_products_button.clicked.connect(self._open_products)
        details_layout.addWidget(self.open_products_button)
        self.open_record_button = QPushButton("打开项目记录")
        self.open_record_button.clicked.connect(self._open_record)
        details_layout.addWidget(self.open_record_button)
        content.addWidget(details_card)
        content.setStretchFactor(0, 4)
        content.setStretchFactor(1, 6)
        page_layout.addWidget(content, 1)
        self._set_actions_enabled(False)

    def set_context(self, group_root: Path) -> None:
        self.group_root = Path(group_root).resolve()
        self.refresh()

    def refresh(self) -> None:
        if not self.group_root:
            return
        selected = self.group_combo.currentText() or self.group_root.name
        group_names = list(self.archive_service.archived_group_names(self.group_root))
        if self.group_root.name not in group_names:
            group_names.append(self.group_root.name)
        group_names.sort()
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItems(group_names)
        self.group_combo.setCurrentText(selected if selected in group_names else self.group_root.name)
        self.group_combo.blockSignals(False)
        self._load_selected_group(self.group_combo.currentText())

    def _load_selected_group(self, group_name: str) -> None:
        if not self.group_root or not group_name:
            return
        projects = self.archive_service.archived_projects(self.group_root, group_name)
        self.current_project = None
        self.project_list.clear()
        for project in projects:
            item = QListWidgetItem(project.name)
            item.setData(Qt.ItemDataRole.UserRole, str(project))
            item.setToolTip(str(project))
            self.project_list.addItem(item)
        if self.project_list.count():
            self.project_list.setCurrentRow(0)
        else:
            self.project_name_label.setText("暂无已完成项目")
            self.path_label.setText("当前项目组还没有归档项目。")
            self.latest_product_label.setText("最新产品：无")
            self._set_actions_enabled(False)

    def _on_project_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if not current:
            self.current_project = None
            self._set_actions_enabled(False)
            return
        self.current_project = Path(current.data(Qt.ItemDataRole.UserRole))
        self.project_name_label.setText(self.current_project.name)
        self.path_label.setText(str(self.current_project))
        latest = self.archive_service.latest_product(self.current_project)
        self.latest_product_label.setText(
            f"最新产品：{latest.name}" if latest else "最新产品：无"
        )
        self._set_actions_enabled(True)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.open_project_button.setEnabled(enabled)
        self.open_products_button.setEnabled(enabled)
        self.open_record_button.setEnabled(enabled)

    def _open_project(self) -> None:
        if self.current_project:
            self._open_path(self.current_project)

    def _open_products(self) -> None:
        if self.current_project:
            self._open_path(self.archive_service.product_root(self.current_project))

    def _open_record(self) -> None:
        if self.current_project:
            self._open_path(self.current_project / "项目记录.md")

    def _open_path(self, path: Path) -> None:
        try:
            self.project_service.open_in_file_manager(path)
        except Exception as exc:
            self.error_requested.emit(f"无法打开：{exc}")
