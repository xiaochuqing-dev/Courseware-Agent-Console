import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox

from services import ProjectService, SettingsService, TaskService
from ui.main_window import MainWindow
from tests.helpers import tool_binding


def create_group(tmp_path: Path):
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"title": "测试课件"}, ensure_ascii=False), encoding="utf-8")
    group = ProjectService(resource_root).create_project_group(
        "测试项目组", 1, tmp_path, [source], tool_binding(resource_root)
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
    assert service.execution_prompt("项目1") == "执行项目1当前任务。"


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


def test_main_window_refreshes_active_and_completed_lists_after_archive(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    sources: list[Path] = []
    for index in range(1, 4):
        source = tmp_path / f"source-{index}.json"
        source.write_text(json.dumps({"index": index}), encoding="utf-8")
        sources.append(source)
    project_service = ProjectService(resource_root)
    group = project_service.create_project_group(
        "归档刷新", 3, tmp_path, sources, tool_binding(resource_root)
    )
    project3 = group.projects[2]
    (project3.path / "工作文件" / "初始版本.html").write_text(
        "product", encoding="utf-8"
    )
    settings = SettingsService(
        QSettings(str(tmp_path / "archive.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(project_service, TaskService(resource_root), settings)
    window.load_project_group(group.root)
    window.home_page.project_list.setCurrentRow(2)
    monkeypatch.setattr(
        window.acceptance_service,
        "has_current_passing_report",
        lambda _path: True,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window.archive_current_project()

    names = [
        window.home_page.project_list.item(index).text()
        for index in range(window.home_page.project_list.count())
    ]
    assert names == ["项目1", "项目2"]
    window.show_completed_projects()
    assert window.completed_page.project_list.count() == 1
    assert window.completed_page.project_list.item(0).text() == "项目3"
    window.close()
    app.processEvents()
