from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from app import create_application  # noqa: E402
from services import ProjectService, SettingsService, TaskService, ToolBinding  # noqa: E402
from tests.helpers import create_valid_product, tool_binding  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _save_exact(widget: QWidget, path: Path) -> None:
    toast = getattr(widget, "toast", None)
    if toast is not None:
        toast.hide()
    QGuiApplication.processEvents()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    if not image.save(str(path)):
        raise RuntimeError(f"截图保存失败：{path}")


def _make_image(path: Path) -> None:
    image = QImage(960, 540, QImage.Format.Format_RGB32)
    image.fill(QColor("#CFE8DE"))
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"材料图片生成失败：{path}")


def _wait_until(app, predicate, timeout_ms: int = 15000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        app.processEvents()
        QTest.qWait(20)
        elapsed += 20
    if not predicate():
        raise RuntimeError("等待后台生成超时。")


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_manual_workflow_screens"])

    with tempfile.TemporaryDirectory(prefix="manual-workflow-gui-") as temp:
        root = Path(temp)
        source = root / "课件需求.json"
        source.write_text(
            json.dumps(
                {"title": "人工工作流优化 GUI 验收", "pages": 8},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        resource_root = ROOT / "resources"
        binding = tool_binding(resource_root)
        project_service = ProjectService(resource_root)
        task_service = TaskService(resource_root)
        group = project_service.create_project_group(
            "人工优化验收 项目组",
            1,
            root,
            [source],
            binding,
        )
        settings = SettingsService(
            QSettings(str(root / "gui.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(project_service, task_service, settings)
        window.load_project_group(group.root)
        window.show()
        window.resize(1366, 860)
        window.show_workflow_optimization()
        page = window.workflow_page
        app.processEvents()
        _save_exact(window, artifacts / "manual-workflow-empty-materials.png")

        image_material = root / "页面参考.png"
        _make_image(image_material)
        pdf_material = root / "验收说明.pdf"
        pdf_material.write_bytes(b"%PDF-1.7\nmanual workflow acceptance")
        markdown_material = root / "补充要求.md"
        markdown_material.write_text(
            "保留原有制作与反馈流程。", encoding="utf-8"
        )
        page.add_material_files(
            [image_material, pdf_material, markdown_material, image_material]
        )
        page.manual_description_input.setPlainText(
            "模板中的操作按钮层级不够明确。\n"
            "希望增强主操作与次操作的区分，同时保持现有课件兼容。\n"
            "只修改与按钮层级直接相关的公共工具。"
        )
        app.processEvents()
        _save_exact(window, artifacts / "manual-workflow-multiple-materials.png")

        page._generate_task()
        _wait_until(app, lambda: not page._generation_in_progress)
        if not page.preview_button.isEnabled():
            raise RuntimeError("优化任务生成后复制执行指令按钮未启用。")
        page.preview_button.click()
        expected_workflow_task = (
            group.root / "工作流优化" / "当前优化任务.md"
        ).resolve()
        if QGuiApplication.clipboard().text() != (
            f"请读取并完整执行以下任务文件：\n{expected_workflow_task}"
        ):
            raise RuntimeError("工作流优化执行指令不正确。")
        app.processEvents()
        _save_exact(window, artifacts / "manual-workflow-generated.png")
        unified_scroll = page.unified_scroll
        for width, height in ((860, 560), (1100, 720), (1366, 860)):
            window.resize(width, height)
            app.processEvents()
            if unified_scroll.horizontalScrollBar().maximum() != 0:
                raise RuntimeError(f"工作流优化页面在 {width}x{height} 出现横向滚动。")
            for button in (
                page.choose_material_button,
                page.copy_button,
                page.preview_button,
            ):
                if button.width() <= 0 or button.height() <= 0:
                    raise RuntimeError(f"人工优化按钮布局无效：{button.text()}")
        if any(
            hasattr(page, name)
            for name in ("mode_stack", "review_mode_button", "manual_mode_button")
        ):
            raise RuntimeError("工作流优化页仍残留模式切换控件。")
        window.resize(1366, 860)
        app.processEvents()

        window.show_home_page()
        home = window.home_page
        home.first_build_button.click()
        home._generate_task()
        if not home.first_execute_button.isEnabled():
            raise RuntimeError("首次制作执行指令按钮未启用。")
        QTest.mouseClick(
            home.first_execute_button,
            Qt.MouseButton.LeftButton,
            pos=home.first_execute_button.rect().center(),
        )
        expected_project_task = (group.projects[0].path / "当前任务.md").resolve()
        if QGuiApplication.clipboard().text() != (
            f"请读取并完整执行以下任务文件：\n{expected_project_task}"
        ):
            raise RuntimeError("首次制作执行指令不正确。")
        app.processEvents()
        _save_exact(window, artifacts / "home-first-build-execution-instruction.png")

        create_valid_product(project_service, group.projects[0])
        feedback_root = group.projects[0].path / "客户反馈" / "第1轮"
        feedback_root.mkdir()
        (feedback_root / "反馈.txt").write_text(
            "主按钮需要更醒目。", encoding="utf-8"
        )
        home.refresh_current_project()
        home.feedback_task_button.click()
        home._generate_task()
        if not home.feedback_execute_button.isEnabled():
            raise RuntimeError("反馈修改执行指令按钮未启用。")
        feedback_clicks: list[bool] = []
        home.feedback_execute_button.clicked.connect(
            lambda: feedback_clicks.append(True)
        )
        QTest.mouseClick(
            home.feedback_execute_button,
            Qt.MouseButton.LeftButton,
            pos=home.feedback_execute_button.rect().center(),
        )
        app.processEvents()
        if feedback_clicks != [True]:
            raise RuntimeError("反馈修改执行指令按钮未命中真实鼠标点击。")
        actual_feedback_instruction = home._current_execution_instruction()
        expected_feedback_instruction = (
            f"请读取并完整执行以下任务文件：\n{expected_project_task}"
        )
        if actual_feedback_instruction != expected_feedback_instruction:
            raise RuntimeError("反馈修改执行指令不正确。")
        app.processEvents()
        _save_exact(window, artifacts / "home-feedback-execution-instruction.png")

        window.close()
        app.processEvents()
        print("人工工作流优化 GUI 验收通过，已生成 5 张截图。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
