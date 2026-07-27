from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from app import create_application  # noqa: E402
from services import ProjectService, SettingsService, TaskService  # noqa: E402
from tests.helpers import tool_binding  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def save_widget(widget: QWidget, path: Path) -> None:
    QGuiApplication.processEvents()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"截图保存失败：{path}")


def wait_until(predicate, timeout_ms: int = 15000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QGuiApplication.processEvents()
        QTest.qWait(20)
        elapsed += 20
    if not predicate():
        raise RuntimeError("等待界面状态超时。")


def write_image(path: Path, color: str) -> Path:
    image = QImage(720, 420, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"测试图片写入失败：{path}")
    return path


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_initial_materials_screens"])
    resource_root = ROOT / "resources"

    with tempfile.TemporaryDirectory(prefix="initial-materials-ui-") as temp:
        root = Path(temp)
        sources: list[Path] = []
        for index, name in enumerate(("一元二次方程", "函数图像", "几何变换"), start=1):
            source = root / f"{name}.json"
            source.write_text(
                json.dumps(
                    {"title": name, "pages": [{"title": f"探究 {index}"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            sources.append(source)

        image_one = write_image(root / "教材截图.png", "#d8ecff")
        image_two = write_image(root / "参考图形.png", "#dcf5e8")
        image_three = write_image(root / "函数示意.png", "#fff0d6")
        pdf = root / "教学要求.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=720, height=420)
        with pdf.open("wb") as handle:
            writer.write(handle)

        project_service = ProjectService(resource_root)
        tasks = TaskService(resource_root)
        settings = SettingsService(
            QSettings(str(root / "materials.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(project_service, tasks, settings)
        window.resize(1440, 900)
        window.show()
        window.show_create_page()
        create = window.create_page
        create.name_input.setText("首次材料真实验收")
        create.location_input.setText(str(root))
        create.count_input.setValue(3)
        create.add_json_files(sources)
        binding = tool_binding(resource_root)
        create.set_tool_paths(binding.workflow, binding.template, binding.validate)
        wait_until(lambda: create._tool_validation_result is not None)
        save_widget(window, artifacts / "initial-materials-create-empty.png")

        create.mapping_list.setCurrentRow(0)
        create.add_material_files([image_one, image_two])
        create.content_scroll.verticalScrollBar().setValue(
            create.content_scroll.verticalScrollBar().maximum()
        )
        QGuiApplication.processEvents()
        save_widget(window, artifacts / "initial-materials-project-with-multiple.png")
        create.content_scroll.verticalScrollBar().setValue(0)
        QGuiApplication.processEvents()

        create.mapping_list.setCurrentRow(1)
        create.add_material_files([image_three, pdf])
        save_widget(window, artifacts / "initial-materials-multi-project-counts.png")

        conflict_first = root / "conflict-a" / "同名参考.png"
        conflict_second = root / "conflict-b" / "同名参考.png"
        conflict_first.parent.mkdir()
        conflict_second.parent.mkdir()
        write_image(conflict_first, "#ffd6d6")
        write_image(conflict_second, "#ffe4e4")
        create.mapping_list.setCurrentRow(0)
        create.add_material_files([conflict_first, conflict_second])
        if create.error_banner.isHidden() or "冲突" not in create.error_banner.text():
            raise RuntimeError("未显示材料同名冲突提示。")
        save_widget(window, artifacts / "initial-materials-conflict-warning.png")

        first_key = project_service.path_key(sources[0])
        create.materials_by_project[first_key] = [image_one, image_two]
        create._hide_error()
        create._refresh_mapping_list()

        create.mapping_list.setCurrentRow(2)
        create._move_mapping(-1)
        second_key = project_service.path_key(sources[1])
        create.project_names_by_path[second_key] = "函数图像（已改名）"
        create._refresh_mapping_list()
        expected_sources = [sources[0], sources[2], sources[1]]
        if create.json_files != expected_sources:
            raise RuntimeError("JSON 重排结果不正确。")
        if create.materials_by_project[first_key] != [image_one, image_two]:
            raise RuntimeError("JSON 重排后材料绑定丢失。")

        ui_tick: list[bool] = []
        created: list[Path] = []
        create.project_created.connect(created.append)
        QTimer.singleShot(0, lambda: ui_tick.append(True))
        create._create_project_group()
        wait_until(lambda: bool(created))
        if not ui_tick:
            raise RuntimeError("后台复制期间 GUI 事件循环未响应。")

        group = project_service.load_project_group(created[0])
        expected_counts = [2, 0, 2]
        if [project.display_name for project in group.projects] != [
            "一元二次方程",
            "几何变换",
            "函数图像（已改名）",
        ]:
            raise RuntimeError("创建后的项目顺序或改名结果不正确。")
        for project, source, expected_count in zip(
            group.projects, expected_sources, expected_counts
        ):
            config = project_service.read_project_config(project.path)
            if len(config["source_materials"]) != expected_count:
                raise RuntimeError(f"{project.display_name} 材料配置数量不正确。")
            copied = list((project.path / "原始需求").iterdir())
            if len(copied) != expected_count + 1:
                raise RuntimeError(f"{project.display_name} 原始需求文件数量不正确。")
            if (project.path / "原始需求" / source.name).read_bytes() != source.read_bytes():
                raise RuntimeError(f"{project.display_name} JSON 复制内容不一致。")
            tasks.generate_first_build_task(project.path, "")
            task = (project.path / "当前任务.md").read_text(encoding="utf-8")
            if "先枚举该目录中的全部文件" not in task:
                raise RuntimeError(f"{project.display_name} 首次任务未要求读取全部材料。")

        window.show_home_page()
        window.home_page.refresh_current_project()
        QGuiApplication.processEvents()
        save_widget(window, artifacts / "initial-materials-created-home.png")
        window.close()
        QGuiApplication.processEvents()

    print("首次制作材料真实 GUI 验收与截图通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
