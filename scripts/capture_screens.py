from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402

from app import create_application  # noqa: E402
from services import (  # noqa: E402
    ProjectService,
    SettingsService,
    TaskService,
    ToolBinding,
)
from tests.helpers import tool_binding  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    root = ROOT
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_screens"])

    with tempfile.TemporaryDirectory(prefix="phase1-preview-", dir=artifacts) as temp:
        preview_root = Path(temp)
        sources = preview_root / "sources"
        sources.mkdir()
        json_files: list[Path] = []
        for index in range(1, 5):
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

        project_service = ProjectService(root / "resources")
        task_service = TaskService(root / "resources")
        binding = tool_binding(root / "resources")
        group = project_service.create_project_group(
            "九年级示例项目组", 4, preview_root, json_files, binding
        )
        settings = SettingsService(
            QSettings(str(preview_root / "preview.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(project_service, task_service, settings)
        window.resize(1280, 800)
        window.load_project_group(group.root)
        window.show()
        app.processEvents()
        if not window.grab().save(str(artifacts / "phase1-home.png")):
            raise RuntimeError("首页截图保存失败")

        window.show_create_page()
        window.create_page.name_input.setText("九年级-暑期")
        window.create_page.count_input.setValue(4)
        window.create_page.location_input.setText(str(preview_root))
        window.create_page.json_files = list(json_files)
        window.create_page.set_tool_paths(
            binding.workflow, binding.template, binding.validate
        )
        window.create_page._refresh_mapping_list()
        app.processEvents()
        if not window.grab().save(str(artifacts / "phase1-create-project.png")):
            raise RuntimeError("创建项目页截图保存失败")
        window.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
