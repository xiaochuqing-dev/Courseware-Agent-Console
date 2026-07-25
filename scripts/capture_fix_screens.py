from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_application
from services import ProjectService, SettingsService, TaskService
from tests.helpers import tool_binding
from ui.main_window import MainWindow


ARTIFACTS = ROOT / "artifacts"


def capture(widget, name: str) -> None:
    widget.repaint()
    QTest.qWait(120)
    if not widget.grab().save(str(ARTIFACTS / name), "PNG"):
        raise RuntimeError(f"无法保存截图：{name}")


def main() -> int:
    ARTIFACTS.mkdir(exist_ok=True)
    app = create_application(["capture-fix-screens", "--allow-multiple-instances"])
    with tempfile.TemporaryDirectory(prefix="courseware-fix-capture-") as temporary:
        workspace = Path(temporary)
        source = workspace / "演示需求.json"
        source.write_text(
            json.dumps({"title": "目录与创建体验验收"}, ensure_ascii=False),
            encoding="utf-8",
        )
        service = ProjectService(ROOT / "resources")
        binding = tool_binding(service.resource_root)
        validation = service.validate_tool_binding(binding)
        group = service.create_project_group(
            "验收演示项目组",
            1,
            workspace,
            [source],
            binding,
            validation_result=validation,
        )
        project = group.projects[0].path
        (project / "产品迭代" / "初始版本.html").write_bytes(
            (group.root / "公共工具" / "template.html").read_bytes()
        )
        settings = SettingsService(
            QSettings(str(workspace / "capture.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(service, TaskService(ROOT / "resources"), settings)
        window.resize(1400, 900)
        window.show()
        window.show_create_page()
        page = window.create_page
        page.name_input.setText("新学期课件")
        page.count_input.setValue(1)
        page.location_input.setText(str(workspace))
        page.add_json_files([source])
        page.set_tool_paths(binding.workflow, binding.template, binding.validate)
        page._prevalidation_timer.stop()
        page._tool_validation_result = validation
        page._validated_binding_key = page._binding_key(binding)
        page._update_create_state()
        app.processEvents()
        capture(window, "fix-create-idle.png")

        page._tool_validation_result = None
        page._tool_validation_in_progress = True
        page.creation_status.setText("正在验证工具兼容性…")
        page.creation_status.show()
        page.progress_bar.show()
        page._update_create_state()
        capture(window, "fix-create-validating.png")

        page._tool_validation_in_progress = False
        page._tool_validation_result = validation
        page._validated_binding_key = page._binding_key(binding)
        page._creation_in_progress = True
        page._set_creation_busy(True, "正在复制公共工具和原始需求…")
        capture(window, "fix-create-building.png")
        page._creation_in_progress = False
        page._set_creation_busy(False)

        window.load_project_group(group.root)
        window.show_toast("项目组创建完成")
        capture(window, "fix-create-success.png")
        capture(window, "fix-optional-acceptance.png")

        structure = QMessageBox(window)
        structure.setWindowTitle("项目结构检查")
        structure.setIcon(QMessageBox.Icon.Information)
        structure.setText("项目1 使用标准业务目录")
        structure.setInformativeText(
            "原始需求/\n客户反馈/\n产品迭代/\n当前任务.md\n项目记录.md\n\n"
            "未发现 工作文件、最终交付 或 验收记录。"
        )
        structure.addButton("知道了", QMessageBox.ButtonRole.AcceptRole)
        structure.show()
        capture(structure, "fix-project-structure.png")
        structure.close()

        migration = QMessageBox(window)
        migration.setWindowTitle("迁移完成")
        migration.setIcon(QMessageBox.Icon.Information)
        migration.setText("旧项目结构已迁移为“产品迭代”结构。")
        migration.setInformativeText(
            "迁移未创建备份。\n"
            "同名文件未覆盖，冲突已使用来源后缀保留。"
        )
        migration.addButton("打开迁移报告", QMessageBox.ButtonRole.ActionRole)
        migration.addButton("完成", QMessageBox.ButtonRole.AcceptRole)
        migration.show()
        capture(migration, "fix-migration-result.png")
        migration.close()
        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
