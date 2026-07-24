from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, Signal
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
    QScrollArea,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from services import ProjectCreationError, ProjectService, TargetExistsError
from ui.widgets import Card, FlowLayout


class CreateProjectPage(QWidget):
    cancelled = Signal()
    project_created = Signal(object)
    open_existing_requested = Signal(object)

    def __init__(self, project_service: ProjectService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.project_service = project_service
        self.json_files: list[Path] = []
        self._build_ui()
        self.refresh_public_tools()

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(34, 28, 34, 30)
        page_layout.setSpacing(20)

        header = QHBoxLayout()
        back_button = QPushButton()
        back_button.setProperty("role", "quiet")
        back_button.setProperty("iconOnly", True)
        back_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        back_button.setToolTip("返回首页")
        back_button.clicked.connect(self.cancelled)
        header.addWidget(back_button)

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
        choose_location_button = QPushButton("选择")
        choose_location_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        choose_location_button.clicked.connect(self._choose_location)
        location_row.addWidget(choose_location_button)
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

        import_button = QPushButton("选择 JSON 文件")
        import_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        import_button.clicked.connect(self._choose_json_files)
        form_layout.addWidget(import_button)

        tools_label = QLabel("公共工具")
        tools_label.setObjectName("fieldLabel")
        form_layout.addWidget(tools_label)
        self.tools_status_layout = FlowLayout(horizontal_spacing=18, vertical_spacing=6)
        form_layout.addLayout(self.tools_status_layout)
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

        footer = QHBoxLayout()
        footer.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.cancelled)
        footer.addWidget(cancel_button)
        create_button = QPushButton("创建项目组")
        create_button.setProperty("role", "primary")
        create_button.clicked.connect(self._create_project_group)
        footer.addWidget(create_button)
        page_layout.addLayout(footer)
        self._update_json_summary()

    def refresh_public_tools(self) -> None:
        while self.tools_status_layout.count():
            item = self.tools_status_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for name, exists in self.project_service.public_tools_status().items():
            label = QLabel(f"{'✓' if exists else '缺失'}  {name}")
            label.setObjectName("successText" if exists else "errorBanner")
            self.tools_status_layout.addWidget(label)

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
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "选择原始 JSON",
            "",
            "JSON 文件 (*.json)",
        )
        if selected:
            self.json_files = [Path(path) for path in selected]
            self._refresh_mapping_list()
            self._hide_error()

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
        self.json_summary.setText(
            f"已选择 {len(self.json_files)} / 需要 {self.count_input.value()}"
        )

    def _create_project_group(self) -> None:
        self._hide_error()
        try:
            group = self.project_service.create_project_group(
                group_name=self.name_input.text(),
                project_count=self.count_input.value(),
                location=Path(self.location_input.text().strip()),
                json_files=self.json_files,
            )
        except TargetExistsError as exc:
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
            return
        except ProjectCreationError as exc:
            self._show_error(str(exc))
            return
        self.project_created.emit(group.root)

    def _show_error(self, message: str) -> None:
        self.error_banner.setText(message)
        self.error_banner.show()

    def _hide_error(self) -> None:
        self.error_banner.hide()
        self.error_banner.clear()
