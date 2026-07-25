from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QMimeData, QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMessageBox, QWidget  # noqa: E402

from app import create_application  # noqa: E402
from services import (  # noqa: E402
    ArchiveService,
    FeedbackService,
    ProjectService,
    PromptService,
    SettingsService,
    TaskService,
)
from ui.main_window import MainWindow  # noqa: E402
from ui.widgets import PromptDialog  # noqa: E402
from ui.widgets.rules_editor_dialog import RulesEditorDialog  # noqa: E402


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


def _make_image(path: Path, color: str) -> None:
    image = QImage(720, 420, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"示例图片生成失败：{path}")


def _paste_image(home, path: Path) -> None:
    mime = QMimeData()
    mime.setImageData(QImage(str(path)))
    QGuiApplication.clipboard().setMimeData(mime)
    home.feedback_drop_area.setFocus()
    QTest.keyClick(
        home.feedback_drop_area,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.ControlModifier,
    )


def _paste_text(home, text: str) -> None:
    mime = QMimeData()
    mime.setText(text)
    QGuiApplication.clipboard().setMimeData(mime)
    home.feedback_drop_area.setFocus()
    QTest.keyClick(
        home.feedback_drop_area,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.ControlModifier,
    )


def _assert_layout(window: MainWindow) -> None:
    required = (
        window.home_page.generate_button,
        window.home_page.task_preview_button,
        window.home_page.open_project_button,
        window.home_page.feedback_drop_area,
        window.home_page.new_round_button,
    )
    for widget in required:
        if widget.width() <= 0 or widget.height() <= 0:
            raise RuntimeError(f"控件布局尺寸无效：{widget.objectName() or widget.text()}")
    if window.home_page.work_scroll.horizontalScrollBar().maximum() != 0:
        raise RuntimeError("主页出现了不应存在的横向滚动。")


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_phase3_screens"])

    with tempfile.TemporaryDirectory(prefix="phase3-regression-") as temp:
        preview_root = Path(temp)
        sources = preview_root / "六项目原始需求"
        sources.mkdir()
        json_files: list[Path] = []
        for index in range(1, 7):
            path = sources / f"课件需求-{index}.json"
            path.write_text(
                json.dumps(
                    {
                        "title": f"阶段三回归课件 {index}",
                        "pages": 8 + index,
                        "interaction": "参数可调并包含课堂演示动画",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            json_files.append(path)

        project_service = ProjectService(ROOT / "resources")
        task_service = TaskService(ROOT / "resources")
        feedback_service = FeedbackService()
        archive_service = ArchiveService()
        prompt_service = PromptService(ROOT / "resources", archive_service)
        group = project_service.create_project_group(
            "阶段三六项目完整回归项目组", 6, preview_root, json_files
        )
        settings = SettingsService(
            QSettings(str(preview_root / "phase3.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(
            project_service,
            task_service,
            settings,
            feedback_service,
            archive_service,
            prompt_service,
        )
        window.load_project_group(group.root)
        window.home_page.project_list.setCurrentRow(2)
        window.show()
        app.processEvents()
        home = window.home_page
        project3 = home.current_project
        if project3 is None:
            raise RuntimeError("项目3未加载。")

        home.requirements_input.setPlainText("保留薄荷绿视觉，并验证常用分辨率。")
        home._generate_task()
        products3 = project3.path / "产品迭代"
        (products3 / "初始版本.html").write_text(
            "<!doctype html><html><body>项目3初始版本</body></html>", encoding="utf-8"
        )
        home.refresh_current_project()

        window.resize(1366, 768)
        app.processEvents()
        _assert_layout(window)
        _save_exact(window, artifacts / "phase3-home-1366x768.png")

        window.show_create_page()
        window.resize(1280, 800)
        create = window.create_page
        create.name_input.setText("阶段三六项目完整回归项目组")
        create.count_input.setValue(6)
        create.location_input.setText(str(preview_root))
        create.json_files = list(json_files)
        create._refresh_mapping_list()
        app.processEvents()
        _save_exact(window, artifacts / "phase3-create-project.png")

        window.show_home_page()
        first_image = sources / "第一轮圈画-1.png"
        second_image = sources / "第一轮圈画-2.png"
        _make_image(first_image, "#bfe7d8")
        _make_image(second_image, "#d8eee6")
        _paste_image(home, first_image)
        _paste_image(home, second_image)
        _paste_text(home, "第1轮：动画速度放慢，公式字号增大。")
        home._save_to_new_round()
        home.feedback_task_button.click()
        home._generate_task()
        (products3 / "第1轮修改.html").write_text(
            "<!doctype html><html><body>项目3第1轮修改</body></html>", encoding="utf-8"
        )

        second_round_image = sources / "第二轮圈画.png"
        _make_image(second_round_image, "#cce4ef")
        _paste_image(home, second_round_image)
        _paste_text(home, "第2轮：保留配色，只调整交互节奏。")
        home.refresh_current_project()
        window.resize(1280, 900)
        home.work_scroll.verticalScrollBar().setValue(
            home.work_scroll.verticalScrollBar().maximum()
        )
        app.processEvents()
        _save_exact(window, artifacts / "phase3-feedback.png")

        home._save_to_new_round()
        home.requirements_input.setPlainText("第1轮确认的整体配色不要重新设计。")
        home._generate_task()
        (products3 / "第2轮修改.html").write_text(
            "<!doctype html><html><body>项目3第2轮修改</body></html>", encoding="utf-8"
        )
        home.refresh_current_project()
        if archive_service.latest_product(project3.path).name != "第2轮修改.html":
            raise RuntimeError("最新产品识别失败。")

        rules_dialog = RulesEditorDialog(group.root, task_service, window)
        rules_dialog.show()
        app.processEvents()
        _save_exact(rules_dialog, artifacts / "phase3-rule-editor.png")
        rules_dialog.close()

        acceptance_prompt = prompt_service.product_acceptance_prompt(project3.path)
        acceptance = PromptDialog(
            "完整产品验收",
            acceptance_prompt,
            window,
            "复制验收 Prompt",
        )
        acceptance.show()
        app.processEvents()
        _save_exact(acceptance, artifacts / "phase3-acceptance-prompt.png")
        acceptance.close()

        home.work_scroll.verticalScrollBar().setValue(0)
        window.resize(1920, 1080)
        app.processEvents()
        _assert_layout(window)
        _save_exact(window, artifacts / "phase3-full-regression.png")

        for width, height in (
            (980, 680),
            (1100, 720),
            (1280, 800),
            (1440, 900),
            (1920, 1080),
        ):
            window.resize(width, height)
            app.processEvents()
            _assert_layout(window)
        window.showMaximized()
        app.processEvents()
        _assert_layout(window)
        window.showNormal()

        window.resize(860, 560)
        app.processEvents()
        _save_exact(window, artifacts / "phase3-minimum-window.png")
        if window.devicePixelRatioF() >= 1.25:
            window.resize(980, 680)
            app.processEvents()
            if not window.grab().save(str(artifacts / "phase3-dpi-scaled.png")):
                raise RuntimeError("DPI 截图保存失败。")

        for project in group.projects[:2]:
            task_service.generate_first_build_task(project.path, "工作流复盘样本")
            (project.path / "产品迭代" / "初始版本.html").write_text(
                f"<!doctype html><html><body>{project.name}</body></html>", encoding="utf-8"
            )
            (project.path / "项目记录.md").write_text(
                "# 项目记录\n\n## 首次制作\n\n已完成制作与验证。\n",
                encoding="utf-8",
            )
            archive_service.archive_project(group.root, project.name)

        window.load_project_group(group.root)
        window.home_page.project_list.setCurrentRow(0)
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window.archive_current_project()

        window.show_completed_projects()
        window.resize(1280, 800)
        app.processEvents()
        if window.completed_page.project_list.count() != 3:
            raise RuntimeError("已完成项目扫描数量不正确。")
        _save_exact(window, artifacts / "phase3-completed-projects.png")

        window.show_workflow_optimization()
        window.workflow_page._set_all_checked(True)
        app.processEvents()
        selected = window.workflow_page.selected_project_paths()
        if len(selected) != 3:
            raise RuntimeError("工作流优化页没有选中三个归档项目。")
        workflow_prompt = prompt_service.workflow_optimization_prompt(group.root, selected)
        for path in selected:
            if str(path) not in workflow_prompt:
                raise RuntimeError(f"工作流优化 Prompt 缺少项目：{path}")
        _save_exact(window, artifacts / "phase3-workflow-optimization.png")
        window.close()

        print(
            "阶段三完整回归通过：6 个项目、2 轮反馈、产品验收、归档、"
            f"3 个已完成项目复盘；DPI={app.primaryScreen().devicePixelRatio():g}。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
