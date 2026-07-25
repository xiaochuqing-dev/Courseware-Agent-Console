from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
)

from services import (
    AcceptanceService,
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
        acceptance_service: AcceptanceService | None = None,
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
        self.acceptance_service = acceptance_service or AcceptanceService(
            self.project_service, self.archive_service, self.feedback_service
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
            self.acceptance_service,
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
        self.home_page.group_switch_requested.connect(self._switch_project_group)
        self.home_page.delete_group_requested.connect(self.delete_project_group)
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
        target = Path(path).expanduser().resolve()
        if (
            self.home_page.group
            and self.home_page.group.root.resolve() != target
            and not self._confirm_pending_feedback_before_switch()
        ):
            self.home_page.set_available_groups(
                self.settings_service.registered_group_paths()
            )
            return False
        try:
            group = self.project_service.load_project_group(target)
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
        if persist:
            self.settings_service.save_recent_group_path(group.root)
        else:
            self.settings_service.register_project_group(group.root)
        preferred_project = self.settings_service.last_selected_project(group.root)
        self.home_page.set_available_groups(
            self.settings_service.registered_group_paths()
        )
        self.home_page.set_group(group, preferred_project)
        self.completed_page.set_context(group.root)
        self.workflow_page.set_context(group.root)
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
        if not self.acceptance_service.has_current_passing_report(project.path):
            self.show_error(
                "当前课件尚未通过有效的完整产品验收，不能标记完成或归档。"
                "请先执行“完整产品验收”；课件修改后需要重新验收。"
            )
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
            f"当前最新产品：{latest_product.parent.name}/{latest_product.name}\n"
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
        groups = self.settings_service.registered_group_paths()
        self.home_page.set_available_groups(groups)
        recent = self.settings_service.recent_group_path()
        candidates = []
        if recent:
            candidates.append(recent)
        candidates.extend(group for group in groups if group != recent)
        if not candidates:
            logger.info("No recent project group to restore")
            self.home_page.set_empty_state()
            return
        for candidate in candidates:
            if candidate.exists() and self.load_project_group(candidate, persist=False):
                return
        logger.warning("No registered project group could be restored")
        self.settings_service.clear_recent_group_path()
        self.home_page.set_empty_state("已登记的项目组均不存在或无效，请重新选择。")

    def _switch_project_group(self, path: Path) -> None:
        self.load_project_group(path)

    def _confirm_pending_feedback_before_switch(self) -> bool:
        if not self.home_page.pending_feedback:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("存在未保存反馈")
        box.setText(
            f"当前有 {len(self.home_page.pending_feedback)} 项反馈尚未保存。"
        )
        box.setInformativeText("切换项目组前请选择保存、放弃或取消切换。")
        save_button = box.addButton("保存为新反馈轮次并切换", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("放弃并切换", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        if box.clickedButton() is cancel_button:
            return False
        if box.clickedButton() is save_button:
            self.home_page._save_to_new_round()
            return not self.home_page.pending_feedback
        return box.clickedButton() is discard_button

    def delete_project_group(self, path: Path) -> None:
        root = Path(path).expanduser().resolve()
        registered = list(self.settings_service.registered_group_paths())
        matching_index = next(
            (index for index, item in enumerate(registered) if item == root),
            -1,
        )
        if matching_index < 0:
            self.show_error(f"项目组不在控制台列表中：{root}")
            return
        is_current = bool(
            self.home_page.group and self.home_page.group.root.resolve() == root
        )
        pending_count = len(self.home_page.pending_feedback) if is_current else 0
        try:
            group = self.project_service.load_project_group(root)
        except Exception as exc:
            self.show_error(f"无法读取待删除项目组：{exc}")
            return
        status = "进行中" if group.projects else "已完成或无进行中项目"
        choice = self._confirm_group_deletion(
            group.name, status, root, is_current, pending_count
        )
        if choice is None:
            return
        delete_files = choice == "delete"
        if delete_files and not self._confirm_local_file_deletion(group.name, root):
            return
        try:
            self.project_service.delete_project_group(root, delete_files)
        except Exception as exc:
            self.show_error(f"项目组删除失败，控制台记录和当前状态未改变：{exc}")
            return

        self.settings_service.remove_project_group(root)
        remaining = list(self.settings_service.registered_group_paths())
        if not is_current:
            self.home_page.set_available_groups(tuple(remaining))
            self.show_toast(
                f"已{'删除本地文件并' if delete_files else ''}从控制台移除 {group.name}"
            )
            return
        self.home_page.pending_feedback.clear()
        if remaining:
            next_index = min(matching_index, len(remaining) - 1)
            self.load_project_group(remaining[next_index])
        else:
            self.settings_service.clear_recent_group_path()
            self.home_page.set_available_groups(())
            self.home_page.set_empty_state("暂无项目组，请创建或导入项目组。")
            self.show_home_page()
        self.show_toast(
            f"已{'删除本地文件并' if delete_files else ''}从控制台移除 {group.name}"
        )

    def _confirm_group_deletion(
        self,
        group_name: str,
        status: str,
        root: Path,
        is_current: bool,
        pending_count: int,
    ) -> str | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("移除项目组")
        box.setText(f"项目组：{group_name}")
        box.setInformativeText(
            f"状态：{status}\n"
            f"完整路径：{root}\n"
            f"当前项目组：{'是' if is_current else '否'}\n"
            f"未保存反馈：{pending_count} 项\n"
            "正在执行的任务：无\n\n"
            "“仅从控制台移除”会保留全部本地文件，可稍后重新导入。\n"
            "“删除本地文件”会永久删除该目录及其内容。"
        )
        remove_button = box.addButton(
            "仅从控制台移除", QMessageBox.ButtonRole.AcceptRole
        )
        delete_button = box.addButton(
            "删除项目组及本地文件…", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_button:
            return None
        if clicked is delete_button:
            return "delete"
        return "remove" if clicked is remove_button else None

    def _confirm_local_file_deletion(self, group_name: str, root: Path) -> bool:
        typed, accepted = QInputDialog.getText(
            self,
            "二次确认永久删除",
            "此操作将永久删除该项目组对应的本地文件，无法通过控制台恢复。\n\n"
            f"完整路径：{root}\n\n请输入项目组名称“{group_name}”确认：",
        )
        if accepted and typed.strip() != group_name:
            self.show_error("输入的项目组名称不一致，已取消删除。")
        return bool(accepted and typed.strip() == group_name)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if self.page_stack.currentWidget() is self.home_page:
                self.home_page.refresh_current_project()
            elif self.page_stack.currentWidget() is self.completed_page:
                self.completed_page.refresh()
            elif self.page_stack.currentWidget() is self.workflow_page:
                self.workflow_page.refresh()
