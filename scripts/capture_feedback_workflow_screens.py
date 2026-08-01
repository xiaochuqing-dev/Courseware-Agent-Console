from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from app import create_application  # noqa: E402
from services import FeedbackService, ProjectService, SettingsService, TaskService  # noqa: E402
from tests.helpers import create_valid_product, tool_binding  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def save_widget(widget: QWidget, path: Path) -> None:
    toast = getattr(widget, "toast", None)
    if toast is not None:
        toast.hide()
    QGuiApplication.processEvents()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"截图保存失败：{path}")


def wait_until(predicate, timeout_ms: int = 20000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QGuiApplication.processEvents()
        QTest.qWait(20)
        elapsed += 20
    if not predicate():
        raise RuntimeError("等待后台操作超时。")


def show_feedback(home) -> None:
    QGuiApplication.processEvents()
    home.work_scroll.verticalScrollBar().setValue(max(0, home.feedback_card.y() - 8))
    QGuiApplication.processEvents()


def click_checkbox(panel, row: int) -> None:
    checkbox = panel.project_table.cellWidget(row, 0)
    QTest.mouseClick(
        checkbox,
        Qt.MouseButton.LeftButton,
        pos=checkbox.rect().center(),
    )
    QGuiApplication.processEvents()


def write_docx(path: Path) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            "<document><body>三个课件统一反馈</body></document>",
        )
    return path


def main() -> int:
    artifacts = ROOT / "artifacts" / "feedback_workflow_refactor"
    artifacts.mkdir(parents=True, exist_ok=True)
    app = create_application(["capture_feedback_workflow_screens"])
    resource_root = ROOT / "resources"

    with tempfile.TemporaryDirectory(prefix="feedback-workflow-gui-") as temporary:
        temp = Path(temporary)
        names = ("一元二次方程", "函数图像", "勾股定理")
        sources: list[Path] = []
        for index, name in enumerate(names, start=1):
            source = temp / f"{name}.json"
            source.write_text(
                json.dumps({"title": name, "pages": index + 4}, ensure_ascii=False),
                encoding="utf-8",
            )
            sources.append(source)

        project_service = ProjectService(resource_root)
        task_service = TaskService(resource_root)
        group = project_service.create_project_group(
            "反馈工作流重构 GUI 验收",
            3,
            temp,
            sources,
            tool_binding(resource_root),
            project_names=list(names),
        )
        for project in group.projects:
            create_valid_product(project_service, project)
        feedback = FeedbackService()
        feedback.save_pending(
            group.projects[0].path,
            1,
            [
                feedback.pending_from_bytes(
                    "第1轮-标题字号反馈.txt",
                    "第一轮：标题字号调大。\n".encode("utf-8"),
                    kind="text",
                )
            ],
        )
        feedback.save_pending(
            group.projects[0].path,
            2,
            [
                feedback.pending_from_bytes(
                    "第2轮-动画速度反馈.txt",
                    "第二轮：动画速度放慢，并保留第一轮已确认样式。\n".encode("utf-8"),
                    kind="text",
                )
            ],
        )
        feedback.save_pending(
            group.projects[1].path,
            1,
            [
                feedback.pending_from_bytes(
                    "第1轮-函数图像标注.txt",
                    "第一轮：补充函数图像标注。\n".encode("utf-8"),
                    kind="text",
                )
            ],
        )

        settings = SettingsService(
            QSettings(str(temp / "feedback-workflow.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(project_service, task_service, settings)
        window.resize(1280, 900)
        window.show()
        if not window.load_project_group(group.root):
            raise RuntimeError("GUI 验收项目组加载失败。")
        home = window.home_page

        home.project_list.setCurrentRow(2)
        show_feedback(home)
        save_widget(window, artifacts / "01-single-no-feedback.png")

        home.project_list.setCurrentRow(0)
        show_feedback(home)
        save_widget(window, artifacts / "02-single-two-rounds-dropdown.png")

        home.feedback_round_combo.setCurrentIndex(0)
        show_feedback(home)
        save_widget(window, artifacts / "03-single-round-1-materials.png")

        home.feedback_round_combo.setCurrentIndex(1)
        show_feedback(home)
        save_widget(window, artifacts / "04-single-round-2-materials.png")

        home._append_pending(
            feedback.pending_from_bytes(
                "待保存-关键公式调整.txt",
                "待保存：统一增大关键公式。\n".encode("utf-8"),
                kind="text",
            )
        )
        show_feedback(home)
        save_widget(window, artifacts / "05-single-pending-and-actions.png")

        QTest.mouseClick(home.new_round_button, Qt.MouseButton.LeftButton)
        QGuiApplication.processEvents()
        if home.feedback_round_combo.currentData() != 3 or not home.feedback_task_button.isChecked():
            raise RuntimeError("新轮次未自动绑定反馈修改上下文。")
        show_feedback(home)
        save_widget(window, artifacts / "06-single-new-round-auto-feedback-mode.png")

        QTest.mouseClick(home.batch_feedback_button, Qt.MouseButton.LeftButton)
        QGuiApplication.processEvents()
        page = window.batch_feedback_page
        panel = page.panel
        errors: list[str] = []
        page.error_requested.disconnect()
        page.error_requested.connect(errors.append)
        save_widget(window, artifacts / "07-batch-page-overview.png")

        click_checkbox(panel, 0)
        save_widget(window, artifacts / "08-batch-one-selected.png")

        click_checkbox(panel, 1)
        if len(panel.selected_project_ids) != 2:
            raise RuntimeError("真实复选框点击未更新选择状态。")
        save_widget(window, artifacts / "09-batch-checked-unchecked-contrast.png")

        click_checkbox(panel, 2)
        hints = ("Word 第一部分", "Word 第二部分", "Word 第三部分")
        for row, hint in enumerate(hints):
            panel.project_table.cellWidget(row, 4).setText(hint)
        panel.batch_note_input.setPlainText("三个课件分别对应 Word 的第一、第二、第三部分。")
        panel.add_files([write_docx(temp / "客户统一反馈.docx")])
        if [target.target_round for target in panel.round_targets] != [4, 2, 1]:
            raise RuntimeError("批量独立目标轮次预览不正确。")
        save_widget(window, artifacts / "10-batch-independent-target-rounds.png")

        QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
        wait_until(lambda: not panel._operation_in_progress)
        if errors:
            raise RuntimeError(errors[-1])
        if panel.saved_result is None:
            raise RuntimeError("批量反馈保存未成功。")
        page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
        QGuiApplication.processEvents()
        save_widget(window, artifacts / "11-batch-save-results.png")

        generated = panel.batch_service.generate_tasks(panel.saved_result.record_path)
        panel._tasks_succeeded(generated)
        QGuiApplication.processEvents()
        if panel.task_result is None or not panel.copy_instruction_button.isEnabled():
            raise RuntimeError("批量任务生成或复制状态不正确。")
        page.scroll.verticalScrollBar().setValue(page.scroll.verticalScrollBar().maximum())
        QGuiApplication.processEvents()
        save_widget(window, artifacts / "12-batch-tasks-ready.png")

        second_id = group.projects[1].project_id
        QTest.mouseClick(
            panel.result_project_buttons[second_id],
            Qt.MouseButton.LeftButton,
        )
        QGuiApplication.processEvents()
        if (
            window.page_stack.currentWidget() is not home
            or home.current_project.project_id != second_id
            or home.feedback_round_combo.currentData() != 2
        ):
            raise RuntimeError("批量结果未跳转到对应项目和轮次。")
        show_feedback(home)
        save_widget(window, artifacts / "13-batch-result-jump-to-single.png")

        for width, height in ((860, 560), (1100, 720), (1280, 900)):
            window.resize(width, height)
            QGuiApplication.processEvents()
            if home.work_scroll.horizontalScrollBar().maximum() != 0:
                raise RuntimeError(f"当前项目页在 {width}x{height} 出现横向滚动。")

        window.close()
        QGuiApplication.processEvents()

    print(f"反馈工作流 GUI 验收通过，已生成 13 张截图：{artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
