from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QMimeData, QSettings, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

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


def _save_window(window: MainWindow, path: Path) -> None:
    window.repaint()
    app = window.windowHandle().screen() if window.windowHandle() else None
    del app
    if not window.grab().save(str(path)):
        raise RuntimeError(f"截图保存失败：{path.name}")


def _make_feedback_image(path: Path, color: str) -> None:
    image = QImage(720, 420, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"示例图片生成失败：{path}")


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_phase2_screens"])

    with tempfile.TemporaryDirectory(prefix="phase2-preview-", dir=artifacts) as temp:
        preview_root = Path(temp)
        sources = preview_root / "sources"
        sources.mkdir()
        json_files: list[Path] = []
        for index in range(1, 4):
            path = sources / f"课件需求-{index}.json"
            path.write_text(
                json.dumps(
                    {"title": f"示例课件 {index}", "pages": 8 + index},
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
            "九年级示例项目组", 3, preview_root, json_files
        )
        settings = SettingsService(
            QSettings(str(preview_root / "preview.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(
            project_service,
            task_service,
            settings,
            feedback_service,
            archive_service,
            prompt_service,
        )
        window.resize(1280, 840)
        window.load_project_group(group.root)
        window.home_page.project_list.setCurrentRow(2)
        project = window.home_page.current_project
        if project is None:
            raise RuntimeError("项目3未加载")
        products = project.path / "产品迭代"
        (products / "初始版本.html").write_text(
            "<!doctype html><html><body>初始版本</body></html>", encoding="utf-8"
        )
        window.home_page.refresh_current_project()
        window.show()
        app.processEvents()
        _save_window(window, artifacts / "phase2-home.png")

        first_image = sources / "微信圈画-1.png"
        second_image = sources / "微信圈画-2.png"
        _make_feedback_image(first_image, "#bfe7d8")
        _make_feedback_image(second_image, "#d8eee6")
        home = window.home_page
        for image_path in (first_image, second_image):
            image_mime = QMimeData()
            image_mime.setImageData(QImage(str(image_path)))
            QGuiApplication.clipboard().setMimeData(image_mime)
            home.feedback_drop_area.setFocus()
            QTest.keyClick(
                home.feedback_drop_area,
                Qt.Key.Key_V,
                Qt.KeyboardModifier.ControlModifier,
            )
        text_mime = QMimeData()
        text_mime.setText("第1轮：动画速度放慢，公式字号增大。")
        QGuiApplication.clipboard().setMimeData(text_mime)
        QTest.keyClick(
            home.feedback_drop_area,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        home._save_to_new_round()
        home.feedback_task_button.click()
        home._generate_task()
        (products / "第1轮修改.html").write_text(
            "<!doctype html><html><body>第1轮修改</body></html>", encoding="utf-8"
        )

        second_round_image = sources / "第二轮圈画.png"
        _make_feedback_image(second_round_image, "#cce4ef")
        file_mime = QMimeData()
        file_mime.setUrls([QUrl.fromLocalFile(str(second_round_image))])
        QGuiApplication.clipboard().setMimeData(file_mime)
        QTest.keyClick(
            home.feedback_drop_area,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        second_text_mime = QMimeData()
        second_text_mime.setText("第2轮：保留配色，只调整交互节奏。")
        QGuiApplication.clipboard().setMimeData(second_text_mime)
        QTest.keyClick(
            home.feedback_drop_area,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier,
        )
        home.refresh_current_project()
        app.processEvents()
        _save_window(window, artifacts / "phase2-feedback.png")
        window.resize(980, 680)
        app.processEvents()
        _save_window(window, artifacts / "phase2-compact.png")
        window.resize(1280, 840)
        app.processEvents()

        home._save_to_new_round()
        home.requirements_input.setPlainText("第1轮确认的整体配色不要重新设计。")
        home._generate_task()
        (products / "第2轮修改.html").write_text(
            "<!doctype html><html><body>第2轮修改</body></html>", encoding="utf-8"
        )
        home.refresh_current_project()
        assert archive_service.latest_product(project.path).name == "第2轮修改.html"

        acceptance = PromptDialog(
            "完整产品验收",
            prompt_service.product_acceptance_prompt(project.path),
            window,
        )
        acceptance.show()
        app.processEvents()
        if not acceptance.grab().save(str(artifacts / "phase2-acceptance.png")):
            raise RuntimeError("产品验收截图保存失败")
        acceptance.close()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window.archive_current_project()
        expected = (
            preview_root
            / "已完成项目"
            / "九年级示例项目组"
            / "项目3"
        )
        if not expected.is_dir():
            raise RuntimeError("项目3没有移动到预期归档目录")
        window.show_completed_projects()
        app.processEvents()
        _save_window(window, artifacts / "phase2-archive.png")
        window.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
