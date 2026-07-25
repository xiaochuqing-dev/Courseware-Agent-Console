from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from app import create_application  # noqa: E402
from services import (  # noqa: E402
    AcceptanceService,
    ArchiveService,
    FeedbackService,
    ProjectService,
    SettingsService,
    TaskService,
    ToolBinding,
)
from ui.main_window import MainWindow  # noqa: E402
from ui.widgets import AcceptanceDialog  # noqa: E402


def _save(widget: QWidget, path: Path) -> None:
    QGuiApplication.processEvents()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    if not image.save(str(path)):
        raise RuntimeError(f"截图保存失败：{path}")


def _binding(resource_root: Path) -> ToolBinding:
    tools = resource_root / "default_public_tools"
    return ToolBinding(
        tools / "WORKFLOW.md",
        tools / "template.html",
        tools / "validate-tool.js",
    )


def _assert_home_layout(window: MainWindow) -> None:
    home = window.home_page
    if home.work_scroll.horizontalScrollBar().maximum() != 0:
        raise RuntimeError("主页出现横向滚动。")
    for widget in (
        home.group_selector,
        home.current_project_label,
        home.generate_button,
        home.acceptance_button,
        home.feedback_drop_area,
    ):
        if widget.width() <= 0 or widget.height() <= 0:
            raise RuntimeError(f"控件尺寸无效：{widget.objectName()}")
    drop_top = home.feedback_drop_area.mapTo(window, QPoint(0, 0)).y()
    if drop_top >= window.height():
        raise RuntimeError("默认首屏看不到反馈导入入口。")


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_phase4_screens"])
    resource_root = ROOT / "resources"

    with tempfile.TemporaryDirectory(prefix="phase4-ui-") as temp:
        root = Path(temp)
        source = root / "真实需求.json"
        source.write_text(
            json.dumps(
                {
                    "title": "数轴上点的运动",
                    "steps": [{"title": "观察"}, {"title": "运动"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        project_service = ProjectService(resource_root)
        group = project_service.create_project_group(
            "真实工具绑定示例项目组",
            1,
            root,
            [source],
            _binding(resource_root),
        )
        project = group.projects[0]
        product = project.path / "工作文件" / "初始版本.html"
        product.write_bytes((group.root / "公共工具" / "template.html").read_bytes())

        feedback = FeedbackService()
        archive = ArchiveService()
        acceptance = AcceptanceService(project_service, archive, feedback)
        tasks = TaskService(resource_root)
        tasks.generate_first_build_task(project.path, "保持课堂投屏可读。")
        settings = SettingsService(
            QSettings(str(root / "phase4.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(
            project_service,
            tasks,
            settings,
            feedback,
            archive,
            acceptance_service=acceptance,
        )
        window.load_project_group(group.root)
        window.show()
        window.resize(1366, 768)
        app.processEvents()
        _assert_home_layout(window)
        _save(window, artifacts / "phase4-home-1366x768.png")

        window.show_create_page()
        create = window.create_page
        binding = _binding(resource_root)
        create.set_tool_paths(binding.workflow, binding.template, binding.validate)
        create.count_input.setValue(1)
        create.json_files = [source]
        create._refresh_mapping_list()
        window.resize(1280, 800)
        app.processEvents()
        _save(window, artifacts / "phase4-create-real-tools.png")

        window.show_home_page()
        image_path = root / "反馈长图.png"
        image = QImage(900, 1400, QImage.Format.Format_RGB32)
        image.fill(QColor("#c8eadc"))
        image.save(str(image_path), "PNG")
        pdf_path = root / "反馈材料.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=320, height=240)
        writer.add_blank_page(width=320, height=240)
        with pdf_path.open("wb") as handle:
            writer.write(handle)
        home = window.home_page
        home._add_feedback_files([image_path, pdf_path])
        home._append_pending(feedback.pending_from_text("动画速度放慢，公式字号增大。"))
        window.resize(1280, 900)
        home.work_scroll.verticalScrollBar().setValue(
            home.work_scroll.verticalScrollBar().maximum()
        )
        app.processEvents()
        _save(window, artifacts / "phase4-feedback-cards.png")

        home._save_to_new_round()
        report = acceptance.run(group.root, project.path)
        dialog = AcceptanceDialog(report, window)
        dialog.show()
        app.processEvents()
        _save(dialog, artifacts / "phase4-acceptance-results.png")
        dialog.close()

        for width, height in (
            (860, 560),
            (980, 680),
            (1280, 800),
            (1440, 900),
            (1920, 1080),
        ):
            window.resize(width, height)
            app.processEvents()
            _assert_home_layout(window)
        window.close()
        app.processEvents()

    print("阶段四界面截图与尺寸检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
