from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QEvent, QThread, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QVBoxLayout,
)

from services import (
    AcceptanceService,
    ArchiveService,
    FeedbackService,
    InvalidProjectGroupError,
    MigrationRequiredError,
    NoProductVersionError,
    ProjectService,
    RecycleBinError,
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
from ui.workers import BackgroundTaskRelay, BackgroundWorker


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
        self._deletion_in_progress = False
        self._migration_in_progress = False
        self._showing_error_dialog = False
        self._threads: set[QThread] = set()
        self._workers: set[BackgroundWorker] = set()
        self._relays: set[BackgroundTaskRelay] = set()
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
            "选择项目组或移动后的目录",
            str(start.parent if start and start.parent.exists() else Path.home()),
        )
        if selected:
            self.load_project_group(Path(selected))

    def load_project_group(
        self,
        path: Path,
        persist: bool = True,
        offer_recovery: bool = True,
    ) -> bool:
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
        except MigrationRequiredError:
            if offer_recovery:
                self._offer_legacy_migration(target)
            else:
                logger.info(
                    "Skipped automatic legacy migration prompt; root=%s", target
                )
            return False
        except InvalidProjectGroupError as exc:
            if offer_recovery:
                self._show_corrupt_group_options(target, str(exc))
            else:
                logger.warning(
                    "Skipped automatic damaged-group prompt; root=%s; error=%s",
                    target,
                    exc,
                )
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
        issues = self.project_service.inspect_group_structure(group.root)
        if issues:
            self._offer_project_structure_repair(issues)
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

    def _offer_legacy_migration(self, root: Path) -> None:
        if self._migration_in_progress:
            return
        answer = QMessageBox.question(
            self,
            "检测到旧项目结构",
            "检测到旧项目结构，是否备份并迁移为“产品迭代”结构？\n\n"
            f"项目组：{root}\n\n"
            "迁移前会在同级目录保留完整备份；同名文件不会覆盖。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._migration_in_progress = True
        dialog = QProgressDialog("正在准备迁移…", "", 0, 0, self)
        dialog.setWindowTitle("迁移项目结构")
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.show()

        def operation(progress):
            return self.project_service.migrate_legacy_group(root, progress)

        def succeeded(result) -> None:
            dialog.close()
            self.show_toast(f"迁移完成，备份已保存到 {result.backup_root.name}")
            self.load_project_group(result.group_root)

        def failed(exc: BaseException) -> None:
            dialog.close()
            self.show_error(str(exc))

        def finished() -> None:
            self._migration_in_progress = False

        self._run_background(
            operation,
            succeeded,
            failed,
            finished,
            stage_handler=dialog.setLabelText,
        )

    def _offer_project_structure_repair(self, issues) -> None:
        details = "\n".join(issue.summary() for issue in issues)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("项目目录名称发生变化")
        box.setText("检测到一个或多个标准目录缺失。")
        box.setInformativeText(
            details
            + "\n\n控制台不会猜测或自动改名。请选择每个标准目录实际对应的文件夹后再修复。"
        )
        repair_button = box.addButton("选择对应文件夹并修复", QMessageBox.ButtonRole.AcceptRole)
        open_button = box.addButton("查看目录", QMessageBox.ButtonRole.ActionRole)
        cancel_button = box.addButton("暂不处理", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        if box.clickedButton() is open_button:
            self.project_service.open_in_file_manager(issues[0].project_path)
            return
        if box.clickedButton() is not repair_button:
            return
        repairs: list[tuple[Path, dict[str, Path]]] = []
        for issue in issues:
            mapping: dict[str, Path] = {}
            for expected in issue.missing_directories:
                selected = QFileDialog.getExistingDirectory(
                    self,
                    f"为 {issue.project_path.name} 的“{expected}”选择被改名的文件夹",
                    str(issue.project_path),
                )
                if not selected:
                    return
                mapping[expected] = Path(selected)
            repairs.append((issue.project_path, mapping))
        confirmation = "\n".join(
            f"{project.name}：" + "；".join(f"{source.name} → {expected}" for expected, source in mapping.items())
            for project, mapping in repairs
        )
        if QMessageBox.question(
            self,
            "确认修复目录名称",
            confirmation + "\n\n只修改文件夹名称，不复制、删除或合并内容。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            for project, mapping in repairs:
                self.project_service.repair_project_directories(project, mapping)
        except Exception as exc:
            self.show_error(str(exc))
            return
        self.show_toast("项目目录名称已恢复为标准结构")
        if self.home_page.group:
            self.home_page.refresh_current_project()

    def _show_corrupt_group_options(self, root: Path, detail: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("项目组结构损坏")
        box.setText("项目组路径存在，但结构不完整。")
        box.setInformativeText(
            f"{detail}\n\n{root}\n\n控制台不会自动重建缺失文件，以免掩盖数据损坏。"
        )
        remove_button = box.addButton("从控制台移除", QMessageBox.ButtonRole.DestructiveRole)
        locate_button = box.addButton("选择移动后的目录", QMessageBox.ButtonRole.ActionRole)
        open_button = box.addButton("查看目录", QMessageBox.ButtonRole.ActionRole)
        repair_button = box.addButton("修复项目组", QMessageBox.ButtonRole.ActionRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is remove_button:
            self.settings_service.remove_project_group(root)
            self.home_page.set_available_groups(self.settings_service.registered_group_paths())
            self.show_toast(f"已从控制台移除 {root.name}")
        elif clicked is locate_button:
            self.choose_project_group()
        elif clicked is open_button and root.exists():
            self.project_service.open_in_file_manager(root)
        elif clicked is repair_button:
            rules = root / "AGENT任务规则.md"
            manifest = root / self.project_service.MANIFEST_NAME
            if not manifest.is_file():
                self.show_error("项目组配置缺失，无法安全推断工具绑定；请从备份恢复后重试。")
            elif not rules.is_file() and QMessageBox.question(
                self,
                "确认恢复任务规则",
                "将从当前内置模板恢复 AGENT任务规则.md。不会修改项目内容，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Yes:
                try:
                    self.task_service.restore_default_rules(root)
                    self.load_project_group(root)
                except Exception as exc:
                    self.show_error(f"修复项目组失败：{exc}")
            else:
                self.show_error("未发现可自动恢复的规则文件问题，请从备份恢复缺失内容。")

    def archive_current_project(self) -> None:
        group = self.home_page.group
        project = self.home_page.current_project
        if not group or not project:
            return
        try:
            self.project_service.validate_project_structure(group.root, project.path)
        except Exception as exc:
            self.show_error(f"项目目录损坏，暂时无法归档：{exc}")
            return
        latest_product = self.archive_service.latest_product(project.path)
        if latest_product is None:
            self.show_error("当前项目没有可用产品版本。")
            return
        acceptance = self.acceptance_service.latest_report(project.path)
        if not acceptance:
            answer = QMessageBox.question(
                self,
                "尚未执行完整验收",
                "当前项目尚未执行完整验收，是否仍要归档？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        latest_round = self.feedback_service.latest_round(project.path)
        round_text = f"第{latest_round}轮" if latest_round else "无"
        answer = QMessageBox.question(
            self,
            "确认归档",
            "确认客户已经认可当前版本，并将项目归档？\n\n"
            f"当前项目：{project.name}\n"
            f"当前最新产品：{latest_product.parent.name}/{latest_product.name}\n"
            f"最新反馈轮次：{round_text}\n"
            f"完整验收：{'已执行' if acceptance else '未执行（用户已确认继续）'}",
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
        if self._showing_error_dialog:
            logger.warning("Suppressed repeated error dialog: %s", message)
            return
        self._showing_error_dialog = True
        try:
            QMessageBox.warning(self, "操作未完成", message)
        finally:
            QTimer.singleShot(100, self._clear_error_dialog_guard)

    def _clear_error_dialog_guard(self) -> None:
        self._showing_error_dialog = False

    def _project_created(self, path: Path) -> None:
        if self.load_project_group(path):
            self.show_toast("项目组已创建")

    def _remember_project_selection(self, group_path: Path, project_name: str) -> None:
        self.settings_service.save_last_selected_project(group_path, project_name)
        logger.info("Project selection saved")

    def _open_existing_group(self, path: Path) -> None:
        self.load_project_group(path)

    def _restore_recent_group(self) -> None:
        missing = self.settings_service.prune_missing_groups()
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
            if missing:
                self.show_toast(f"已移除 {len(missing)} 个不存在的项目组记录。")
            return
        for candidate in candidates:
            if candidate.exists() and self.load_project_group(candidate, persist=False):
                if missing:
                    self.show_toast(f"已移除 {len(missing)} 个不存在的项目组记录。")
                return
        logger.warning("No registered project group could be restored")
        self.settings_service.clear_recent_group_path()
        self.home_page.set_empty_state("已登记的项目组均不存在或无效，请重新选择。")
        if missing:
            self.show_toast(f"已移除 {len(missing)} 个不存在的项目组记录。")

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
        if self._deletion_in_progress:
            return
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
        status = "目录存在" if root.exists() else "目录已不存在"
        choice = self._confirm_group_deletion(
            root.name, status, root, is_current, pending_count
        )
        if choice is None:
            return
        if choice == "remove":
            self._complete_group_removal(root, matching_index, is_current, False)
            return
        if not root.exists():
            self._complete_group_removal(root, matching_index, is_current, False)
            return
        confirmation = QMessageBox(self)
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setWindowTitle("确认移到回收站")
        confirmation.setText(f"确认将项目组“{root.name}”移到回收站？")
        confirmation.setInformativeText(
            f"完整路径：\n{root}\n\n"
            "项目组中的原始需求、反馈、产品迭代和项目记录会一起移动。"
        )
        confirm_button = confirmation.addButton(
            "确认移到回收站", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = confirmation.addButton(
            "取消", QMessageBox.ButtonRole.RejectRole
        )
        confirmation.setDefaultButton(cancel_button)
        confirmation.exec()
        if confirmation.clickedButton() is not confirm_button:
            return
        self._start_recycle_operation(root, matching_index, is_current)

    def _start_recycle_operation(
        self, root: Path, matching_index: int, is_current: bool
    ) -> None:
        if self._deletion_in_progress:
            return
        self._deletion_in_progress = True
        self.home_page.delete_group_button.setEnabled(False)
        self.show_toast("正在将项目组移到回收站…")
        logger.info("Recycle operation started; root=%s", root)

        def operation(_progress):
            self.project_service.move_project_group_to_recycle_bin(root)
            return root

        def succeeded(_result) -> None:
            logger.info("Recycle operation completed; root=%s", root)
            self._complete_group_removal(root, matching_index, is_current, True)

        def failed(exc: BaseException) -> None:
            logger.exception("Recycle operation failed; root=%s", root, exc_info=exc)
            self._deletion_in_progress = False
            self.home_page.delete_group_button.setEnabled(True)
            self._show_recycle_failure(root, matching_index, is_current, exc)

        def finished() -> None:
            self._deletion_in_progress = False
            if self.home_page.group:
                self.home_page.delete_group_button.setEnabled(True)

        self._run_background(operation, succeeded, failed, finished)

    def _complete_group_removal(
        self, root: Path, matching_index: int, is_current: bool, recycled: bool
    ) -> None:
        self.settings_service.remove_project_group(root)
        remaining = list(self.settings_service.registered_group_paths())
        if not is_current:
            self.home_page.set_available_groups(tuple(remaining))
            self.show_toast(
                f"已{'移到回收站并' if recycled else ''}从控制台移除 {root.name}"
            )
            return
        self.home_page.pending_feedback.clear()
        if remaining:
            start = min(matching_index, len(remaining) - 1)
            ordered = remaining[start:] + remaining[:start]
            loaded = any(
                self.load_project_group(
                    candidate,
                    offer_recovery=False,
                )
                for candidate in ordered
            )
            if not loaded:
                self.settings_service.clear_recent_group_path()
                self.home_page.set_available_groups(tuple(remaining))
                self.home_page.set_empty_state(
                    "剩余项目组需要迁移或修复，请稍后从下拉列表中主动选择。"
                )
                self.show_home_page()
        else:
            self.settings_service.clear_recent_group_path()
            self.home_page.set_available_groups(())
            self.home_page.set_empty_state("暂无项目组，请创建或导入项目组。")
            self.show_home_page()
        self.show_toast(
            f"已{'移到回收站并' if recycled else ''}从控制台移除 {root.name}"
        )

    def _show_recycle_failure(
        self,
        root: Path,
        matching_index: int,
        is_current: bool,
        exc: BaseException,
    ) -> None:
        blocked = exc.blocked_path if isinstance(exc, RecycleBinError) else root
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("暂时无法移到回收站")
        box.setText("项目组暂时无法移到回收站，原目录和控制台记录均未改变。")
        box.setInformativeText(
            f"Windows 正在占用或拒绝访问：\n{blocked}\n\n"
            "请关闭文件资源管理器预览窗格、浏览器课件页面、编辑器或其他控制台后重试。"
        )
        retry_button = box.addButton("重试", QMessageBox.ButtonRole.AcceptRole)
        remove_button = box.addButton("仅从控制台移除", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry_button)
        box.exec()
        if box.clickedButton() is retry_button:
            QTimer.singleShot(
                0,
                lambda: self._start_recycle_operation(
                    root, matching_index, is_current
                ),
            )
        elif box.clickedButton() is remove_button:
            self._complete_group_removal(root, matching_index, is_current, False)

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
            "“移到回收站”会整体移动根目录，仍可从 Windows 回收站恢复。"
        )
        remove_button = box.addButton(
            "仅从控制台移除", QMessageBox.ButtonRole.AcceptRole
        )
        delete_button = box.addButton(
            "移到回收站", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_button:
            return None
        if clicked is delete_button:
            return "recycle"
        return "remove" if clicked is remove_button else None

    def _run_background(
        self,
        operation,
        succeeded,
        failed,
        finished,
        stage_handler=None,
    ) -> None:
        thread = QThread(self)
        worker = BackgroundWorker(operation)
        relay = BackgroundTaskRelay(succeeded, failed, finished, self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if stage_handler is not None:
            worker.stage_changed.connect(stage_handler)
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

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if (
                self._deletion_in_progress
                or self._migration_in_progress
                or self._showing_error_dialog
            ):
                return
            if self.page_stack.currentWidget() is self.home_page:
                self.home_page.refresh_current_project()
            elif self.page_stack.currentWidget() is self.completed_page:
                self.completed_page.refresh()
            elif self.page_stack.currentWidget() is self.workflow_page:
                self.workflow_page.refresh()
