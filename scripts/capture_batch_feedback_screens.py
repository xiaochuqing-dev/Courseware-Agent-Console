from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMessageBox, QWidget  # noqa: E402

from app import create_application  # noqa: E402
from services import ProjectService, SettingsService, TaskService  # noqa: E402
from tests.helpers import tool_binding  # noqa: E402
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
        raise RuntimeError("等待批量反馈后台操作超时。")


def write_docx(path: Path) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            "<document><body>三个课件的统一反馈材料</body></document>",
        )
    return path


def write_image(path: Path) -> Path:
    image = QImage(960, 540, QImage.Format.Format_RGB32)
    image.fill(QColor("#d5eee5"))
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"图片材料生成失败：{path}")
    return path


def select_only(panel, rows: tuple[int, ...]) -> None:
    panel._set_all_selected(False)
    for row in rows:
        panel.project_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    QGuiApplication.processEvents()


def show_feedback_top(home) -> None:
    QGuiApplication.processEvents()
    home.work_scroll.verticalScrollBar().setValue(max(0, home.feedback_card.y() - 8))
    QGuiApplication.processEvents()


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_batch_feedback_screens"])
    resource_root = ROOT / "resources"

    with tempfile.TemporaryDirectory(prefix="batch-feedback-gui-") as temporary:
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
            "批量反馈 GUI 验收",
            3,
            temp,
            sources,
            tool_binding(resource_root),
            project_names=list(names),
        )
        for number in (1,):
            root = group.projects[0].path / "客户反馈" / f"第{number}轮"
            root.mkdir()
            (root / "历史反馈.txt").write_text("历史", encoding="utf-8")
        for number in (1, 2, 3):
            root = group.projects[1].path / "客户反馈" / f"第{number}轮"
            root.mkdir()
            (root / "历史反馈.txt").write_text("历史", encoding="utf-8")

        settings = SettingsService(
            QSettings(str(temp / "batch-gui.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(project_service, task_service, settings)
        window.resize(1440, 900)
        window.show()
        if not window.load_project_group(group.root):
            raise RuntimeError("GUI 验收项目组加载失败。")
        home = window.home_page
        home.batch_feedback_mode_button.click()
        panel = home.batch_feedback_panel
        show_feedback_top(home)
        save_widget(window, artifacts / "batch-feedback-empty.png")

        select_only(panel, (0, 1))
        if [target.target_round for target in panel.round_targets] != [2, 4]:
            raise RuntimeError("两个不同历史轮次的目标预览不正确。")
        show_feedback_top(home)
        save_widget(window, artifacts / "batch-feedback-different-rounds-preview.png")

        select_only(panel, (1, 2))
        if [target.target_round for target in panel.round_targets] != [4, 1]:
            raise RuntimeError("有历史/无历史课件的目标预览不正确。")
        if panel.strategy_combo.model().item(1).isEnabled():
            raise RuntimeError("存在无历史课件时追加模式仍可用。")
        show_feedback_top(home)
        save_widget(window, artifacts / "batch-feedback-missing-round-preview.png")

        select_only(panel, (0, 1, 2))
        hints = ("对应 Word 第一部分", "对应 Word 第二部分", "对应 Word 第三部分")
        for row, hint in enumerate(hints):
            panel.project_table.cellWidget(row, 3).setText(hint)
        panel.batch_note_input.setPlainText("Word 按顺序分为三个课件部分，图片是统一样式参考。")
        word = write_docx(temp / "客户统一修改意见.docx")
        image = write_image(temp / "参考截图.png")
        panel.add_files([word, image, word])
        if len(panel.pending_feedback) != 2:
            raise RuntimeError("批量材料保序去重结果不正确。")
        show_feedback_top(home)
        save_widget(window, artifacts / "batch-feedback-word-image-hints.png")

        panel.save_button.click()
        wait_until(lambda: not panel._operation_in_progress)
        if panel.saved_result is None:
            raise RuntimeError("批量反馈保存未成功。")
        expected_rounds = [2, 4, 1]
        if [target.target_round for target in panel.saved_result.targets] != expected_rounds:
            raise RuntimeError("三项目独立目标轮次保存结果不正确。")
        home.work_scroll.verticalScrollBar().setValue(
            home.work_scroll.verticalScrollBar().maximum()
        )
        QGuiApplication.processEvents()
        save_widget(window, artifacts / "batch-feedback-save-success.png")

        panel.generate_tasks_button.click()
        wait_until(lambda: not panel._operation_in_progress)
        if panel.task_result is None or not panel.copy_instruction_button.isEnabled():
            raise RuntimeError("批量反馈任务生成后复制按钮未启用。")
        panel.copy_instruction_button.click()
        expected_instruction = (
            "请读取并完整执行以下批量任务文件：\n\n"
            f"{panel.task_result.batch_task_path}"
        )
        if QGuiApplication.clipboard().text() != expected_instruction:
            raise RuntimeError("批量执行指令剪贴板内容不正确。")
        QGuiApplication.processEvents()
        save_widget(window, artifacts / "batch-feedback-tasks-and-copy.png")

        panel.start_new_batch()
        select_only(panel, (0, 1))
        first_dir = temp / "conflict-a"
        second_dir = temp / "conflict-b"
        first_dir.mkdir()
        second_dir.mkdir()
        (first_dir / "同名意见.txt").write_text("A", encoding="utf-8")
        (second_dir / "同名意见.txt").write_text("B", encoding="utf-8")
        conflict_screenshot = artifacts / "batch-feedback-same-name-conflict.png"

        def capture_modal() -> None:
            modal = QApplication.activeModalWidget()
            if isinstance(modal, QMessageBox):
                save_widget(modal, conflict_screenshot)
                modal.accept()
                return
            QTimer.singleShot(30, capture_modal)

        from PySide6.QtWidgets import QApplication

        QTimer.singleShot(30, capture_modal)
        panel.add_files([first_dir / "同名意见.txt", second_dir / "同名意见.txt"])
        if not conflict_screenshot.is_file():
            raise RuntimeError("同名冲突错误截图未生成。")

        home.single_feedback_mode_button.click()
        show_feedback_top(home)
        save_widget(window, artifacts / "batch-feedback-single-mode-regression.png")
        single_pending_before = len(home.pending_feedback)
        batch_pending_before = len(panel.pending_feedback)
        for _ in range(5):
            home.batch_feedback_mode_button.click()
            home.project_list.setCurrentRow(1)
            home.single_feedback_mode_button.click()
            home.project_list.setCurrentRow(0)
        if len(home.pending_feedback) != single_pending_before:
            raise RuntimeError("模式或项目切换污染了单项目待保存状态。")
        if len(panel.pending_feedback) != batch_pending_before:
            raise RuntimeError("模式或项目切换清空了批量待保存状态。")

        for width, height in ((860, 560), (1100, 720), (1440, 900)):
            window.resize(width, height)
            home.batch_feedback_mode_button.click()
            show_feedback_top(home)
            QGuiApplication.processEvents()
            if home.work_scroll.horizontalScrollBar().maximum() != 0:
                raise RuntimeError(f"首页在 {width}x{height} 出现横向滚动。")
            if panel.project_table.horizontalScrollBar().maximum() != 0:
                raise RuntimeError(f"批量课件表格在 {width}x{height} 出现横向滚动。")
            for button in (
                panel.save_button,
                panel.generate_tasks_button,
                panel.copy_instruction_button,
            ):
                if button.width() <= 0 or button.height() <= 0:
                    raise RuntimeError(f"批量操作按钮布局无效：{button.text()}")

        window.close()
        QGuiApplication.processEvents()

    print("批量反馈与 Word 支持 GUI 验收通过，已生成 8 张截图。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
