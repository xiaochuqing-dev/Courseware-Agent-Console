from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from app import create_application  # noqa: E402
from services import ProjectService, SettingsService, TaskService  # noqa: E402
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
    if not image.save(str(path)):
        raise RuntimeError(f"截图保存失败：{path}")


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_hotfix_screens"])

    with tempfile.TemporaryDirectory(prefix="p0-hotfix-") as temp:
        preview_root = Path(temp)
        source_root = preview_root / "sources"
        source_root.mkdir()
        json_files: list[Path] = []
        for index, name in enumerate("ABCDEF", start=1):
            path = source_root / f"{name}.json"
            path.write_text(
                json.dumps(
                    {"title": f"热修复验收课件 {index}", "pages": 8 + index},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            json_files.append(path)

        settings = SettingsService(
            QSettings(str(preview_root / "hotfix.ini"), QSettings.Format.IniFormat)
        )
        project_service = ProjectService(ROOT / "resources")
        window = MainWindow(
            project_service,
            TaskService(ROOT / "resources"),
            settings,
        )
        window.resize(1280, 800)
        window.show()
        app.processEvents()
        save_widget(window, artifacts / "hotfix-empty-home.png")

        window.show_create_page()
        create = window.create_page
        create.name_input.setText("P0六项目验收")
        create.count_input.setValue(6)
        create.location_input.setText(str(preview_root))
        create.location_input.setCursorPosition(0)
        create.add_json_files(json_files[:3])
        app.processEvents()
        save_widget(window, artifacts / "hotfix-create-project-3-of-6.png")

        create.add_json_files(json_files[3:])
        app.processEvents()
        save_widget(window, artifacts / "hotfix-create-project-6-of-6.png")

        created: list[Path] = []
        create.project_created.connect(created.append)
        create._create_project_group()
        app.processEvents()
        if created != [preview_root / "P0六项目验收"]:
            raise RuntimeError("项目组没有按预期创建。")
        if window.home_page.current_project is None:
            raise RuntimeError("创建后首页没有自动选中项目。")
        save_widget(window, artifacts / "hotfix-project-created.png")
        window.close()

    print("P0 截图验收通过：空首页、3/6、6/6、创建后首页共 4 张。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
