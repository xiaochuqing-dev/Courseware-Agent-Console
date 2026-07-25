import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from services import ProjectService, SettingsService, TaskService, ValidationError
from ui.main_window import MainWindow
from ui.pages.create_project_page import CreateProjectPage
from tests.helpers import tool_binding


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def resource_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources"


def make_json_files(root: Path, count: int = 6) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        path = root / f"{chr(ord('A') + index)}.json"
        path.write_text(json.dumps({"marker": index}), encoding="utf-8")
        paths.append(path)
    return paths


def test_json_dialog_appends_multiple_batches_and_ignores_duplicates(
    app: QApplication,
    resource_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = make_json_files(tmp_path / "sources")
    page = CreateProjectPage(ProjectService(resource_root))
    binding = tool_binding(resource_root)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    page.count_input.setValue(6)
    selections = iter(
        [
            ([str(path) for path in files[:3]], "JSON 文件 (*.json)"),
            ([str(path) for path in files[3:]], "JSON 文件 (*.json)"),
            ([str(files[0]), str(files[5])], "JSON 文件 (*.json)"),
        ]
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: next(selections))

    page._choose_json_files()
    assert page.json_files == files[:3]
    assert page.json_summary.text() == "已选择 3 / 6"
    assert page.json_status.text() == "还需要 3 个 JSON 文件。"

    page._choose_json_files()
    assert page.json_files == files
    assert page.json_summary.text() == "已选择 6 / 6"
    assert page.create_button.isEnabled()

    page._choose_json_files()
    assert page.json_files == files
    assert page.json_status.text() == "已添加 0 个文件，已忽略 2 个重复文件。"
    page.deleteLater()
    app.processEvents()


def test_json_mapping_order_delete_and_count_changes_preserve_files(
    app: QApplication, resource_root: Path, tmp_path: Path
) -> None:
    files = make_json_files(tmp_path / "mapping")
    page = CreateProjectPage(ProjectService(resource_root))
    binding = tool_binding(resource_root)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    page.count_input.setValue(6)
    page.add_json_files(files)

    page.mapping_list.setCurrentRow(5)
    page._move_mapping(-1)
    assert page.json_files == [*files[:4], files[5], files[4]]
    assert page.mapping_list.item(4).text().endswith("F.json")

    page.count_input.setValue(4)
    assert len(page.json_files) == 6
    assert "项目数量为 4" in page.json_status.text()
    assert not page.create_button.isEnabled()

    page.count_input.setValue(8)
    assert len(page.json_files) == 6
    assert page.json_status.text() == "还需要 2 个 JSON 文件。"

    page.mapping_list.setCurrentRow(4)
    page._remove_mapping()
    assert files[5] not in page.json_files
    assert len(page.json_files) == 5
    page.deleteLater()
    app.processEvents()


def test_real_six_project_creation_preserves_mapping(
    app: QApplication, resource_root: Path, tmp_path: Path
) -> None:
    files = make_json_files(tmp_path / "six-sources")
    page = CreateProjectPage(ProjectService(resource_root))
    binding = tool_binding(resource_root)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    page.name_input.setText("六项目热修复验收")
    page.count_input.setValue(6)
    page.location_input.setText(str(tmp_path))
    page.add_json_files(files[:3])
    page.add_json_files(files[3:])
    page.mapping_list.setCurrentRow(5)
    page._move_mapping(-1)
    expected_mapping = [*files[:4], files[5], files[4]]
    created: list[Path] = []
    page.project_created.connect(created.append)

    page._create_project_group()

    assert created == [tmp_path / "六项目热修复验收"]
    for index, source in enumerate(expected_mapping, start=1):
        copied_files = list(
            (created[0] / f"项目{index}" / "原始需求").iterdir()
        )
        assert copied_files == [
            created[0] / f"项目{index}" / "原始需求" / source.name
        ]
        assert copied_files[0].read_bytes() == source.read_bytes()
    page.deleteLater()
    app.processEvents()


def test_invalid_json_is_rejected_without_partial_directory(
    resource_root: Path, tmp_path: Path
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    service = ProjectService(resource_root)

    with pytest.raises(ValidationError, match="JSON 文件无法解析"):
        service.create_project_group(
            "非法JSON", 1, tmp_path, [invalid], tool_binding(resource_root)
        )

    assert not (tmp_path / "非法JSON").exists()


def test_one_batch_six_and_ten_cancelled_dialogs_keep_page_usable(
    app: QApplication,
    resource_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = make_json_files(tmp_path / "one-batch")
    page = CreateProjectPage(ProjectService(resource_root))
    binding = tool_binding(resource_root)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    page.count_input.setValue(6)
    page.add_json_files(files)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([], ""))

    for _ in range(10):
        page._choose_json_files()

    assert page.json_files == files
    assert page.create_button.isEnabled()
    page.name_input.setText("一次六选验收")
    page.location_input.setText(str(tmp_path))
    created: list[Path] = []
    page.project_created.connect(created.append)
    page._create_project_group()
    assert created == [tmp_path / "一次六选验收"]
    page.deleteLater()
    app.processEvents()


def test_create_page_switch_is_constant_time_and_reuses_one_page(
    app: QApplication, resource_root: Path, tmp_path: Path
) -> None:
    project_service = ProjectService(resource_root)
    settings = SettingsService(
        QSettings(str(tmp_path / "switch.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(project_service, TaskService(resource_root), settings)
    page_id = id(window.create_page)
    child_widget_count = len(window.create_page.findChildren(QWidget))

    for _ in range(20):
        window.show_create_page()
        app.processEvents()
        assert id(window.page_stack.currentWidget()) == page_id
        window.show_home_page()
        app.processEvents()

    assert len(window.create_page.findChildren(QWidget)) == child_widget_count
    window.close()
    app.processEvents()


def test_hidden_create_page_flow_layout_does_not_cover_form_controls(
    app: QApplication, resource_root: Path, tmp_path: Path
) -> None:
    settings = SettingsService(
        QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root),
        TaskService(resource_root),
        settings,
    )
    window.resize(1864, 1232)
    window.show()
    window.show_create_page()
    app.processEvents()

    page = window.create_page
    labels = list(page.tool_status_labels.values())
    assert all(label is not None for label in labels)
    assert all(label.geometry().height() < 100 for label in labels if label)
    assert len({label.geometry().topLeft() for label in labels if label}) == len(labels)

    center = page.name_input.mapToGlobal(page.name_input.rect().center())
    assert QApplication.widgetAt(center) is page.name_input
    import_center = page.import_button.mapToGlobal(page.import_button.rect().center())
    assert QApplication.widgetAt(import_center) is page.import_button
    window.close()
    app.processEvents()


def test_restart_restores_last_selected_project_and_invalid_name_falls_back(
    app: QApplication, resource_root: Path, tmp_path: Path
) -> None:
    files = make_json_files(tmp_path / "restore-sources", 3)
    project_service = ProjectService(resource_root)
    group = project_service.create_project_group(
        "恢复项目组", 3, tmp_path, files, tool_binding(resource_root)
    )
    ini_path = tmp_path / "restore.ini"
    settings = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    settings.save_recent_group_path(group.root)
    settings.save_last_selected_project(group.root, "项目3")

    first = MainWindow(project_service, TaskService(resource_root), settings)
    assert first.home_page.current_project is not None
    assert first.home_page.current_project.name == "项目3"
    first.home_page.project_list.setCurrentRow(1)
    first.close()
    app.processEvents()

    reopened_settings = SettingsService(
        QSettings(str(ini_path), QSettings.Format.IniFormat)
    )
    reopened = MainWindow(
        project_service,
        TaskService(resource_root),
        reopened_settings,
    )
    assert reopened.home_page.current_project is not None
    assert reopened.home_page.current_project.name == "项目2"
    reopened.close()
    app.processEvents()

    reopened_settings.save_last_selected_project(group.root, "已归档项目")
    fallback = MainWindow(
        project_service,
        TaskService(resource_root),
        reopened_settings,
    )
    assert fallback.home_page.current_project is not None
    assert fallback.home_page.current_project.name == "项目1"
    fallback.close()
    app.processEvents()


def test_empty_home_exposes_both_primary_actions(
    app: QApplication, resource_root: Path, tmp_path: Path
) -> None:
    settings = SettingsService(
        QSettings(str(tmp_path / "empty.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root),
        TaskService(resource_root),
        settings,
    )
    window.show()
    app.processEvents()

    assert window.home_page.empty_title.text() == "课件项目"
    assert window.home_page.empty_message.text() == "尚未选择项目组"
    assert window.home_page.empty_create_button.isVisible()
    assert window.home_page.empty_select_button.isVisible()
    assert not window.home_page.empty_completed_button.isVisible()
    window.close()
    app.processEvents()
