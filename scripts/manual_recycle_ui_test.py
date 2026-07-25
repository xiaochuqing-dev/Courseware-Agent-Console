from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_application
from services import ProjectService, SettingsService, TaskService
from tests.helpers import tool_binding
from ui.main_window import MainWindow


def main() -> int:
    app = create_application(["manual-recycle-ui-test"])
    with tempfile.TemporaryDirectory(prefix="courseware-recycle-ui-") as temporary:
        workspace = Path(temporary)
        source = workspace / "删除弹窗回归.json"
        source.write_text(
            json.dumps({"name": "删除弹窗回归"}, ensure_ascii=False),
            encoding="utf-8",
        )
        service = ProjectService(ROOT / "resources")
        group = service.create_project_group(
            "删除弹窗回归项目组",
            1,
            workspace,
            [source],
            tool_binding(service.resource_root),
        )
        settings = SettingsService(
            QSettings(
                str(workspace / "manual-recycle-ui.ini"),
                QSettings.Format.IniFormat,
            )
        )
        window = MainWindow(service, TaskService(ROOT / "resources"), settings)
        window.setWindowTitle("课件 Agent 控制台 - 回收站弹窗回归")
        window.resize(1400, 900)
        window.load_project_group(group.root)
        window.show()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
