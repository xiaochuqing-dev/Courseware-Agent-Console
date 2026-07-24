import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from services import ProjectService, SettingsService, TaskService
from ui.main_window import MainWindow


def create_group(tmp_path: Path):
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"title": "测试课件"}, ensure_ascii=False), encoding="utf-8")
    group = ProjectService(resource_root).create_project_group(
        "测试项目组", 1, tmp_path, [source]
    )
    return resource_root, group


def test_empty_special_requirement_still_generates_task(tmp_path: Path) -> None:
    resource_root, group = create_group(tmp_path)
    service = TaskService(resource_root)
    target = service.generate_first_build_task(group.projects[0].path, "")
    content = target.read_text(encoding="utf-8")

    assert "项目：项目1" in content
    assert "任务类型：首次制作" in content
    assert "## 特殊要求\n\n无" in content
    assert "AGENT任务规则.md" in content
    assert service.execution_prompt("项目1") == (
        "请执行“项目1/当前任务.md”中的当前任务，并严格遵循根目录 AGENT任务规则.md。"
    )


def test_recent_project_group_path_round_trip(tmp_path: Path) -> None:
    ini_path = tmp_path / "settings.ini"
    first = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    group_path = tmp_path / "九年级"
    first.save_recent_group_path(group_path)

    reopened = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    assert reopened.recent_group_path() == group_path.resolve()
    reopened.clear_recent_group_path()
    assert reopened.recent_group_path() is None


def test_main_window_restores_recent_project_group(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    resource_root, group = create_group(tmp_path)
    ini_path = tmp_path / "window-settings.ini"
    saved = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    saved.save_recent_group_path(group.root)

    restored = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    window = MainWindow(
        ProjectService(resource_root),
        TaskService(resource_root),
        restored,
    )
    assert window.home_page.group is not None
    assert window.home_page.group.root == group.root.resolve()
    assert window.home_page.project_list.count() == 1
    window.close()
    app.processEvents()

