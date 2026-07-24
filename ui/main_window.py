from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
)

from services import (
    ArchiveService,
    FeedbackService,
    InvalidProjectGroupError,
    NoProductVersionError,
    ProjectService,
    PromptService,
    SettingsService,
    TaskService,
)
from ui.pages import CompletedProjectsPage, CreateProjectPage, HomePage
from ui.widgets import BackgroundWidget, Toast
from ui.widgets.rules_editor_dialog import RulesEditorDialog


class MainWindow(QMainWindow):
    def __init__(
        self,
        project_service: ProjectService | None = None,
        task_service: TaskService | None = None,
        settings_service: SettingsService | None = None,
        feedback_service: FeedbackService | None = None,
        archive_service: ArchiveService | None = None,
        prompt_service: PromptService | None = None,
    ) -> None:
        super().__init__()
        self.project_service = project_service or ProjectService()
        self.task_service = task_service or TaskService()
        self.settings_service = settings_service or SettingsService()
        self.feedback_service = feedback_service or FeedbackService()
        self.archive_service = archive_service or ArchiveService()
        self.prompt_service = prompt_service or PromptService(
            self.task_service.resource_root, self.archive_service
        )
        self.setWindowTitle("课件 Agent 控制台")
        self.setMinimumSize(980, 680)
        self.resize(1240, 790)

        background = BackgroundWidget()
        background_layout = QVBoxLayout(background)
        background_layout.setContentsMargins(0, 0, 0, 0)

        self.page_stack = QStackedWidget()
        background_layout.addWidget(self.page_stack)
        self.setCentralWidget(background)

        self.home_page = HomePage(
            self.project_service,
            self.task_service,
            self.feedback_service,
            self.archive_service,
            self.prompt_service,
        )
        self.create_page = CreateProjectPage(self.project_service)
        self.completed_page = CompletedProjectsPage(
            self.project_service, self.archive_service
        )
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.create_page)
        self.page_stack.addWidget(self.completed_page)

        self.home_page.create_project_requested.connect(self.show_create_page)
        self.home_page.choose_group_requested.connect(self.choose_project_group)
        self.home_page.edit_rules_requested.connect(self.edit_rules)
        self.home_page.archive_requested.connect(self.archive_current_project)
        self.home_page.completed_projects_requested.connect(self.show_completed_projects)
        self.home_page.toast_requested.connect(self.show_toast)
        self.home_page.error_requested.connect(self.show_error)
        self.create_page.cancelled.connect(self.show_home_page)
        self.create_page.project_created.connect(self._project_created)
        self.create_page.open_existing_requested.connect(self._open_existing_group)
        self.completed_page.back_requested.connect(self.show_home_page)
        self.completed_page.error_requested.connect(self.show_error)

        self.toast = Toast(background)
        self._restore_recent_group()

    def show_home_page(self) -> None:
        self.page_stack.setCurrentWidget(self.home_page)
        self.home_page.refresh_current_project()

    def show_create_page(self) -> None:
        self.create_page.refresh_public_tools()
        self.page_stack.setCurrentWidget(self.create_page)

    def show_completed_projects(self) -> None:
        if not self.home_page.group:
            return
        self.completed_page.set_context(self.home_page.group.root)
        self.page_stack.setCurrentWidget(self.completed_page)

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
        self.completed_page.set_context(group.root)
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

    def archive_current_project(self) -> None:
        group = self.home_page.group
        project = self.home_page.current_project
        if not group or not project:
            return
        latest_product = self.archive_service.latest_product(project.path)
        if latest_product is None:
            self.show_error("当前项目没有可用产品版本。")
            return
        latest_round = self.feedback_service.latest_round(project.path)
        round_text = f"第{latest_round}轮" if latest_round else "无"
        answer = QMessageBox.question(
            self,
            "确认归档",
            "确认客户已经认可当前版本，并将项目归档？\n\n"
            f"当前项目：{project.name}\n"
            f"当前最新产品：产品迭代/{latest_product.name}\n"
            f"最新反馈轮次：{round_text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            destination = self.archive_service.archive_project(group.root, project.name)
        except NoProductVersionError:
            self.show_error("当前项目没有可用产品版本。")
            return
        except Exception as exc:
            self.show_error(str(exc))
            return
        self.load_project_group(group.root)
        self.completed_page.set_context(group.root)
        self.show_toast(f"{project.name} 已归档到 {destination.parent.name}")

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

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if self.page_stack.currentWidget() is self.home_page:
                self.home_page.refresh_current_project()
            elif self.page_stack.currentWidget() is self.completed_page:
                self.completed_page.refresh()
