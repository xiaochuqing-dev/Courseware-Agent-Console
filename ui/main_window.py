from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
)

from services import (
    InvalidProjectGroupError,
    ProjectService,
    SettingsService,
    TaskService,
)
from ui.pages import CreateProjectPage, HomePage
from ui.widgets import BackgroundWidget, Toast
from ui.widgets.rules_editor_dialog import RulesEditorDialog


class MainWindow(QMainWindow):
    def __init__(
        self,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        settings_service: SettingsService | None = None,
    ) -> None:
        super().__init__()
        self.project_service = project_service or ProjectService()
        self.task_service = task_service or TaskService()
        self.settings_service = settings_service or SettingsService()
        self.setWindowTitle("课件 Agent 控制台")
        self.setMinimumSize(980, 680)
        self.resize(1240, 790)

        background = BackgroundWidget()
        background_layout = QVBoxLayout(background)
        background_layout.setContentsMargins(0, 0, 0, 0)

        self.page_stack = QStackedWidget()
        background_layout.addWidget(self.page_stack)
        self.setCentralWidget(background)

        self.home_page = HomePage(self.project_service, self.task_service)
        self.create_page = CreateProjectPage(self.project_service)
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.create_page)

        self.home_page.create_project_requested.connect(self.show_create_page)
        self.home_page.choose_group_requested.connect(self.choose_project_group)
        self.home_page.edit_rules_requested.connect(self.edit_rules)
        self.home_page.toast_requested.connect(self.show_toast)
        self.home_page.error_requested.connect(self.show_error)
        self.create_page.cancelled.connect(self.show_home_page)
        self.create_page.project_created.connect(self._project_created)
        self.create_page.open_existing_requested.connect(self._open_existing_group)

        self.toast = Toast(background)
        self._restore_recent_group()

    def show_home_page(self) -> None:
        self.page_stack.setCurrentWidget(self.home_page)

    def show_create_page(self) -> None:
        self.create_page.refresh_public_tools()
        self.page_stack.setCurrentWidget(self.create_page)

    def choose_project_group(self) -> None:
        start = self.settings_service.recent_group_path()
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择项目组",
            str(start.parent if start and start.parent.exists() else Path.home()),
        )
        if selected:
            self.load_project_group(Path(selected))

    def load_project_group(self, path: Path, persist: bool = True) -> bool:
        try:
            group = self.project_service.load_project_group(path)
        except InvalidProjectGroupError as exc:
            self.show_error(str(exc))
            return False
        self.home_page.set_group(group)
        if persist:
            self.settings_service.save_recent_group_path(group.root)
        self.show_home_page()
        return True

    def edit_rules(self) -> None:
        if not self.home_page.group:
            return
        dialog = RulesEditorDialog(
            self.home_page.group.root,
            self.task_service,
            self,
        )
        if dialog.exec():
            self.show_toast("任务规则已保存")

    def show_toast(self, message: str) -> None:
        self.toast.show_message(message)

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作未完成", message)

    def _project_created(self, path: Path) -> None:
        if self.load_project_group(path):
            self.show_toast("项目组已创建")

    def _open_existing_group(self, path: Path) -> None:
        self.load_project_group(path)

    def _restore_recent_group(self) -> None:
        recent = self.settings_service.recent_group_path()
        if not recent:
            self.home_page.set_empty_state()
            return
        if not recent.exists():
            self.settings_service.clear_recent_group_path()
            self.home_page.set_empty_state("最近使用的项目组已不存在，请重新选择。")
            return
        if not self.load_project_group(recent, persist=False):
            self.settings_service.clear_recent_group_path()
            self.home_page.set_empty_state("最近使用的目录不是有效项目组，请重新选择。")

