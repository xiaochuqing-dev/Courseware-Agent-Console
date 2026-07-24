from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

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
from services.app_logging import LOGGER_NAME
from ui.pages import (
    CompletedProjectsPage,
    CreateProjectPage,
    HomePage,
    WorkflowOptimizationPage,
)
from ui.widgets import BackgroundWidget, Toast
from ui.widgets.rules_editor_dialog import RulesEditorDialog


logger = logging.getLogger(LOGGER_NAME)


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
        self.setMinimumSize(860, 560)
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
        self.workflow_page = WorkflowOptimizationPage(
            self.archive_service, self.prompt_service
        )
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.create_page)
        self.page_stack.addWidget(self.completed_page)
        self.page_stack.addWidget(self.workflow_page)

        self.home_page.create_project_requested.connect(self.show_create_page)
        self.home_page.choose_group_requested.connect(self.choose_project_group)
        self.home_page.edit_rules_requested.connect(self.edit_rules)
        self.home_page.archive_requested.connect(self.archive_current_project)
        self.home_page.completed_projects_requested.connect(self.show_completed_projects)
        self.home_page.workflow_optimization_requested.connect(
            self.show_workflow_optimization
        )
        self.home_page.toast_requested.connect(self.show_toast)
        self.home_page.error_requested.connect(self.show_error)
        self.home_page.project_selected.connect(self._remember_project_selection)
        self.create_page.cancelled.connect(self.show_home_page)
        self.create_page.project_created.connect(self._project_created)
        self.create_page.open_existing_requested.connect(self._open_existing_group)
        self.completed_page.back_requested.connect(self.show_home_page)
        self.completed_page.error_requested.connect(self.show_error)
        self.workflow_page.back_requested.connect(self.show_home_page)
        self.workflow_page.error_requested.connect(self.show_error)
        self.workflow_page.toast_requested.connect(self.show_toast)

        self.toast = Toast(background)
        self._restore_recent_group()

    def show_home_page(self) -> None:
        logger.info("Page switch: home")
        self.page_stack.setCurrentWidget(self.home_page)
        self.home_page.refresh_current_project()

    def show_create_page(self) -> None:
        started = perf_counter()
        self.page_stack.setCurrentWidget(self.create_page)
        logger.info(
            "Page switch: create project; elapsed_ms=%.2f",
            (perf_counter() - started) * 1000,
        )

    def show_completed_projects(self) -> None:
        if not self.home_page.group:
            return
        self.completed_page.set_context(self.home_page.group.root)
        self.page_stack.setCurrentWidget(self.completed_page)
        logger.info("Page switch: completed projects")

    def show_workflow_optimization(self) -> None:
        if not self.home_page.group:
            return
        self.workflow_page.set_context(self.home_page.group.root)
        self.page_stack.setCurrentWidget(self.workflow_page)
        logger.info("Page switch: workflow optimization")

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
            root = Path(path).expanduser().resolve()
            rules_path = root / "AGENT任务规则.md"
            if root.is_dir() and not rules_path.is_file():
                answer = QMessageBox.question(
                    self,
                    "任务规则缺失",
                    f"所选项目组缺少任务规则：\n{rules_path}\n\n"
                    "是否从内置模板重新创建后继续加载？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    try:
                        self.task_service.restore_default_rules(root)
                        group = self.project_service.load_project_group(root)
                    except Exception as recovery_error:
                        self.show_error(f"恢复任务规则失败：{recovery_error}")
                        return False
                else:
                    return False
            else:
                self.show_error(str(exc))
                return False
        preferred_project = self.settings_service.last_selected_project(group.root)
        self.home_page.set_group(group, preferred_project)
        self.completed_page.set_context(group.root)
        self.workflow_page.set_context(group.root)
        if persist:
            self.settings_service.save_recent_group_path(group.root)
        self.show_home_page()
        logger.info("Project group loaded; project_count=%d", len(group.projects))
        return True

    def edit_rules(self) -> None:
        if not self.home_page.group:
            return
        rules_path = self.home_page.group.root / "AGENT任务规则.md"
        if not rules_path.is_file():
            answer = QMessageBox.question(
                self,
                "任务规则缺失",
                f"任务规则文件不存在：\n{rules_path}\n\n是否从内置模板重新创建？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.task_service.restore_default_rules(self.home_page.group.root)
            except Exception as exc:
                self.show_error(f"无法恢复默认任务规则：{exc}")
                return
        try:
            dialog = RulesEditorDialog(
                self.home_page.group.root,
                self.task_service,
                self,
            )
        except Exception as exc:
            self.show_error(f"无法打开任务规则：{exc}")
            return
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

    def _remember_project_selection(self, group_path: Path, project_name: str) -> None:
        self.settings_service.save_last_selected_project(group_path, project_name)
        logger.info("Project selection saved")

    def _open_existing_group(self, path: Path) -> None:
        self.load_project_group(path)

    def _restore_recent_group(self) -> None:
        recent = self.settings_service.recent_group_path()
        if not recent:
            logger.info("No recent project group to restore")
            self.home_page.set_empty_state()
            return
        if not recent.exists():
            logger.warning("Recent project group no longer exists")
            self.settings_service.clear_recent_group_path()
            self.home_page.set_empty_state("最近使用的项目组已不存在，请重新选择。")
            return
        if not self.load_project_group(recent, persist=False):
            logger.warning("Recent project group could not be restored")
            self.settings_service.clear_recent_group_path()
            self.home_page.set_empty_state("最近使用的目录不是有效项目组，请重新选择。")

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if self.page_stack.currentWidget() is self.home_page:
                self.home_page.refresh_current_project()
            elif self.page_stack.currentWidget() is self.completed_page:
                self.completed_page.refresh()
            elif self.page_stack.currentWidget() is self.workflow_page:
                self.workflow_page.refresh()
