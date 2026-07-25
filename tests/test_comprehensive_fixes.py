from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import send2trash
from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QMessageBox

from app import activate_window
from services import (
    ProjectCreationError,
    ProjectService,
    RecycleBinError,
    SettingsService,
    SingleInstanceController,
    TaskService,
    ToolBinding,
)
from services.process_utils import hidden_process_options
from tests.helpers import tool_binding
from ui.main_window import MainWindow
from ui.pages import CreateProjectPage
from ui.widgets import Toast, WrappedItemDelegate, configure_wrapped_list


@pytest.fixture
def resource_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources"


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def create_group(root: Path, resource_root: Path, name: str = "测试项目组"):
    source = root / f"{name}.json"
    source.write_text(json.dumps({"name": name}, ensure_ascii=False), encoding="utf-8")
    service = ProjectService(resource_root)
    group = service.create_project_group(
        name, 1, root, [source], tool_binding(resource_root)
    )
    return service, group


def wait_until(app: QApplication, predicate, timeout_ms: int = 8000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        app.processEvents()
        QTest.qWait(20)
        elapsed += 20
    assert predicate()


def test_schema_v3_has_stable_ids_and_single_product_directory(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root)
    manifest = service.read_manifest(group.root)
    assert manifest["schema_version"] == 3
    assert manifest["group_id"]
    assert manifest["product_directory"] == "产品迭代"
    assert "delivery_directory" not in manifest
    assert manifest["projects"][0]["project_id"] == group.projects[0].project_id
    project = group.projects[0].path
    project_config = service.read_project_config(project)
    assert project_config["display_name"] == "测试项目组"
    assert project_config["project_id"] == group.projects[0].project_id
    assert {path.name for path in project.iterdir() if path.is_dir()} == {
        "原始需求",
        "客户反馈",
        "产品迭代",
    }


def test_legacy_migration_without_backup_merges_and_preserves_all_acceptance_records(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root, "旧结构")
    project = group.projects[0].path
    product = project / "产品迭代"
    product.rename(project / "工作文件")
    (project / "工作文件" / "初始版本.html").write_text("working", encoding="utf-8")
    delivery = project / "最终交付"
    delivery.mkdir()
    (delivery / "初始版本.html").write_text("delivery", encoding="utf-8")
    old_reports = project / "验收记录"
    old_reports.mkdir()
    (old_reports / "验收-旧.md").write_text("旧验收结论", encoding="utf-8")
    full_second_record = "第二份完整验收记录" * 300
    (old_reports / "验收-补充.txt").write_text(full_second_record, encoding="utf-8")
    manifest = service.read_manifest(group.root)
    manifest.update(
        schema_version=1,
        product_directory="工作文件",
        delivery_directory="最终交付",
    )
    (group.root / service.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    result = service.migrate_legacy_group(group.root)

    migrated_group = service.load_project_group(group.root)
    migrated = migrated_group.projects[0].path
    assert (migrated / "产品迭代" / "旧结构.html").is_file()
    assert "working" in (migrated / "产品迭代" / "旧结构.html").read_text(encoding="utf-8")
    conflicts = list((migrated / "产品迭代").glob("初始版本-来自最终交付*.html"))
    assert len(conflicts) == 1
    assert conflicts[0].read_text(encoding="utf-8") == "delivery"
    assert not any((migrated / name).exists() for name in service.LEGACY_DIRECTORIES)
    project_record = (migrated / "项目记录.md").read_text(encoding="utf-8")
    assert "旧验收结论" in project_record
    assert full_second_record in project_record
    assert "验收-旧.md" in project_record
    assert "验收-补充.txt" in project_record
    assert service.read_manifest(group.root)["schema_version"] == 3
    assert list(tmp_path.glob("旧结构-迁移前备份-*")) == []
    assert list(tmp_path.glob(".旧结构.migrating-*")) == []
    assert list(tmp_path.glob(".旧结构.migration-original-*")) == []
    with pytest.raises(RuntimeError, match="已经是 schema v3"):
        service.migrate_legacy_group(group.root)


def test_migration_failure_keeps_original_without_backup_or_temporary_copy(
    tmp_path: Path, resource_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, group = create_group(tmp_path, resource_root, "迁移失败")
    manifest = service.read_manifest(group.root)
    manifest["schema_version"] = 1
    (group.root / service.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    original_project = group.projects[0].path
    sentinel = original_project / "原始需求" / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def fail_merge(*_args, **_kwargs):
        raise OSError("simulated migration failure")

    monkeypatch.setattr(service, "_merge_directory", fail_merge)
    (original_project / "工作文件").mkdir()
    with pytest.raises(ProjectCreationError, match="迁移未完成"):
        service.migrate_legacy_group(group.root)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob("迁移失败-迁移前备份-*")) == []
    assert list(tmp_path.glob(".迁移失败.migrating-*")) == []
    assert list(tmp_path.glob(".迁移失败.migration-original-*")) == []


def test_migration_permission_error_explains_that_the_folder_is_in_use(
    resource_root: Path,
) -> None:
    service = ProjectService(resource_root)
    message = service._migration_failure_message(
        PermissionError(13, "Access is denied"), True
    )

    assert "正在资源管理器中打开" in message
    assert "请关闭已打开的项目文件夹" in message
    assert "原项目保持不变" in message
    assert "未创建备份" in message
    assert "migration-original" not in message


def test_migration_folder_lock_keeps_original_and_cleans_working_copy(
    tmp_path: Path, resource_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, group = create_group(tmp_path, resource_root, "目录占用")
    manifest = service.read_manifest(group.root)
    manifest["schema_version"] = 1
    (group.root / service.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    sentinel = group.projects[0].path / "原始需求" / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    original_rename = Path.rename

    def reject_group_rename(path: Path, target: Path):
        if path == group.root:
            raise PermissionError(13, "Access is denied", str(path))
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", reject_group_rename)

    with pytest.raises(ProjectCreationError) as error:
        service.migrate_legacy_group(group.root)

    message = str(error.value)
    assert "正在资源管理器中打开" in message
    assert "原项目保持不变" in message
    assert "未创建备份" in message
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(tmp_path.glob("目录占用-迁移前备份-*")) == []
    assert list(tmp_path.glob(".目录占用.migrating-*")) == []
    assert list(tmp_path.glob(".目录占用.migration-original-*")) == []


def test_unreadable_legacy_acceptance_file_stops_before_replacing_original(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root, "验收不可读")
    project = group.projects[0].path
    old_reports = project / "验收记录"
    old_reports.mkdir()
    unreadable = old_reports / "验收-损坏.bin"
    unreadable.write_bytes(b"\xff\xfe\xfa")
    manifest = service.read_manifest(group.root)
    manifest["schema_version"] = 1
    (group.root / service.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ProjectCreationError, match="原项目保持不变"):
        service.migrate_legacy_group(group.root)

    assert unreadable.read_bytes() == b"\xff\xfe\xfa"
    assert list(tmp_path.glob("验收不可读-迁移前备份-*")) == []
    assert list(tmp_path.glob(".验收不可读.migrating-*")) == []
    assert list(tmp_path.glob(".验收不可读.migration-original-*")) == []


def test_default_window_size_is_taller_on_large_screens_and_adapts_to_small_ones() -> None:
    assert MainWindow._preferred_window_size(QSize(1600, 1000)) == QSize(1240, 920)
    assert MainWindow._preferred_window_size(QSize(1000, 700)) == QSize(976, 668)


def test_project_names_wrap_to_two_lines_before_eliding(app: QApplication) -> None:
    project_list = QListWidget()
    project_list.resize(240, 320)
    configure_wrapped_list(project_list)
    names = (
        "影子变化",
        "太阳光线下物体影子的变化规律。",
        "太阳光线下物体影子的变化规律与测量方法综合实践活动完整课题名称",
    )
    for name in names:
        item = QListWidgetItem(name)
        item.setToolTip(name)
        project_list.addItem(item)

    project_list.show()
    app.processEvents()

    assert isinstance(project_list.itemDelegate(), WrappedItemDelegate)
    assert project_list.wordWrap()
    assert project_list.textElideMode() == Qt.TextElideMode.ElideRight
    assert project_list.sizeHintForRow(1) >= project_list.sizeHintForRow(0)
    assert project_list.sizeHintForRow(2) == project_list.sizeHintForRow(1)
    assert project_list.item(1).text() == names[1]
    assert project_list.item(2).text() == names[2]
    assert project_list.item(2).toolTip() == names[2]

    project_list.close()


def test_multiple_renamed_project_directories_require_explicit_mapping(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root, "目录改名")
    project = group.projects[0].path
    (project / "原始需求").rename(project / "需求资料-临时")
    (project / "产品迭代").rename(project / "课件版本-临时")
    issue = service.inspect_project_structure(project)
    assert issue is not None
    assert set(issue.missing_directories) == {"原始需求", "产品迭代"}
    assert {path.name for path in issue.unexpected_directories} == {
        "需求资料-临时",
        "课件版本-临时",
    }

    service.repair_project_directories(
        project,
        {
            "原始需求": project / "需求资料-临时",
            "产品迭代": project / "课件版本-临时",
        },
    )

    assert service.inspect_project_structure(project) is None
    assert list((project / "原始需求").glob("*.json"))


def test_renamed_tool_source_files_are_warnings_and_deep_validation_is_reused(
    tmp_path: Path, resource_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    workflow = tools / "WORKFLOW.md"
    template = tools / "template (1).html"
    validator = tools / "validate-tool (2).js"
    shutil.copy2(resource_root / "default_public_tools" / "WORKFLOW.md", workflow)
    shutil.copy2(resource_root / "default_public_tools" / "template.html", template)
    shutil.copy2(resource_root / "default_public_tools" / "validate-tool.js", validator)
    binding = ToolBinding(workflow, template, validator)
    service = ProjectService(resource_root)
    import services.project_service as module

    original = module.run_hidden_process
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "run_hidden_process", counted)
    result = service.validate_tool_binding(binding)
    assert result.template_passed
    assert len(result.warnings) == 2
    assert len(calls) == 2
    assert service.validate_tool_binding(binding) is result
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    service.create_project_group(
        "复用验证", 1, tmp_path, [source], binding, validation_result=result
    )
    assert len(calls) == 2


def test_windows_subprocess_options_hide_child_console() -> None:
    options = hidden_process_options()
    assert options["creationflags"]
    assert options["startupinfo"].dwFlags


def test_recycle_failure_preserves_every_file_and_registry_can_remove_corrupt_group(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, group = create_group(tmp_path, resource_root, "损坏项目组")
    before = sorted(str(path.relative_to(group.root)) for path in group.root.rglob("*"))

    def denied(_path):
        error = PermissionError(13, "file is in use", str(group.root / "公共工具" / "template.html"))
        error.winerror = 5
        raise error

    monkeypatch.setattr(send2trash, "send2trash", denied)
    with pytest.raises(RecycleBinError) as captured:
        service.move_project_group_to_recycle_bin(group.root)
    assert captured.value.winerror == 5
    assert sorted(str(path.relative_to(group.root)) for path in group.root.rglob("*")) == before

    settings = SettingsService(QSettings(str(tmp_path / "groups.ini"), QSettings.Format.IniFormat))
    window = MainWindow(service, TaskService(resource_root), settings)
    settings.register_project_group(group.root)
    window.home_page.set_available_groups(settings.registered_group_paths())
    (group.root / "AGENT任务规则.md").unlink()
    monkeypatch.setattr(window, "_confirm_group_deletion", lambda *_args: "remove")
    window.delete_project_group(group.root)
    assert group.root.is_dir()
    assert settings.registered_group_paths() == ()
    window.close()


def test_missing_registry_cleanup_and_group_id_relocation(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root, "移动项目组")
    settings = SettingsService(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.save_recent_group_path(group.root)
    settings.save_last_selected_project(group.root, "项目1")
    old = group.root
    moved = tmp_path / "移动后的项目组"
    old.rename(moved)

    assert settings.prune_missing_groups() == (old,)
    assert settings.registered_group_paths() == ()
    assert settings.recent_group_path() is None
    settings.relocate_project_group(old, moved)
    assert settings.registered_group_paths() == (moved.resolve(),)
    assert settings.recent_group_path() == moved.resolve()
    assert settings.last_selected_project(moved) == "项目1"


def test_structure_notice_fingerprint_persists_and_clears(tmp_path: Path) -> None:
    ini_path = tmp_path / "structure-notices.ini"
    group_path = tmp_path / "项目组"
    settings = SettingsService(
        QSettings(str(ini_path), QSettings.Format.IniFormat)
    )

    settings.save_structure_notice_fingerprint(group_path, "folders:missing")
    reopened = SettingsService(
        QSettings(str(ini_path), QSettings.Format.IniFormat)
    )
    assert reopened.structure_notice_fingerprint(group_path) == "folders:missing"

    reopened.save_structure_notice_fingerprint(group_path, "")
    assert reopened.structure_notice_fingerprint(group_path) == ""


def test_create_page_starts_only_one_worker_for_ten_clicks(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProjectService(resource_root)
    page = CreateProjectPage(service)
    binding = tool_binding(resource_root)
    validation = service.validate_tool_binding(binding)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    page._prevalidation_timer.stop()
    page._tool_validation_result = validation
    page._validated_binding_key = page._binding_key(binding)
    source = tmp_path / "one.json"
    source.write_text("{}", encoding="utf-8")
    page.count_input.setValue(1)
    page.add_json_files([source])
    page.location_input.setText(str(tmp_path))
    calls = []

    def slow_create(**_kwargs):
        calls.append(1)
        time.sleep(0.2)
        return SimpleNamespace(root=tmp_path / "结果", projects=())

    monkeypatch.setattr(service, "create_project_group", slow_create)
    for _ in range(10):
        page._create_project_group()
    assert page._creation_in_progress
    assert not page.create_button.isEnabled()
    wait_until(app, lambda: not page._creation_in_progress)
    assert len(calls) == 1
    page.close()


def test_single_instance_activation_and_window_restore(app: QApplication) -> None:
    name = f"CoursewareAgentConsole.Test.{uuid4()}"
    primary = SingleInstanceController(name)
    secondary = SingleInstanceController(name)
    activated = []
    primary.activation_requested.connect(lambda: activated.append(True))
    assert primary.acquire()
    assert not secondary.acquire()
    wait_until(app, lambda: bool(activated), timeout_ms=2000)
    primary.release()

    window = SimpleNamespace(
        isMinimized=lambda: True,
        showNormal=lambda: activated.append("normal"),
        show=lambda: activated.append("show"),
        raise_=lambda: activated.append("raise"),
        activateWindow=lambda: activated.append("activate"),
    )
    activate_window(window)
    assert activated[-3:] == ["normal", "raise", "activate"]


def test_activation_refresh_is_suppressed_during_delete_and_error_dialog(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SettingsService(
        QSettings(str(tmp_path / "activation.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(ProjectService(resource_root), TaskService(resource_root), settings)
    refresh_calls = []
    monkeypatch.setattr(
        window.home_page, "refresh_current_project", lambda: refresh_calls.append(1)
    )
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    event = QEvent(QEvent.Type.ActivationChange)

    window._deletion_in_progress = True
    window.changeEvent(event)
    assert refresh_calls == []

    window._deletion_in_progress = False

    def warning(*_args):
        window.changeEvent(QEvent(QEvent.Type.ActivationChange))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", warning)
    window.show_error("项目组目录不存在")
    assert refresh_calls == []
    wait_until(app, lambda: not window._showing_error_dialog, timeout_ms=1000)
    window.close()


def test_missing_current_path_does_not_open_refresh_error_loop(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
) -> None:
    service, group = create_group(tmp_path, resource_root, "即将进入回收站")
    settings = SettingsService(
        QSettings(str(tmp_path / "missing.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(service, TaskService(resource_root), settings)
    assert window.load_project_group(group.root)
    errors = []
    window.home_page.error_requested.connect(errors.append)
    moved = tmp_path / "模拟回收站中的项目组"
    group.root.rename(moved)

    window.home_page.refresh_current_project()

    assert errors == []
    moved.rename(group.root)
    window.close()


def test_legacy_group_opens_without_modal_or_backup(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, legacy = create_group(tmp_path, resource_root, "旧项目直接打开")
    manifest = service.read_manifest(legacy.root)
    manifest["schema_version"] = 1
    manifest["product_directory"] = "工作文件"
    manifest["delivery_directory"] = "最终交付"
    (legacy.root / service.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    ini_path = tmp_path / "legacy-open.ini"
    settings = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    settings.save_recent_group_path(legacy.root)
    notices = []

    def fail_modal(*_args, **_kwargs):
        pytest.fail("项目结构异常不应打开阻塞弹窗")

    monkeypatch.setattr(QMessageBox, "question", fail_modal)
    monkeypatch.setattr(QMessageBox, "warning", fail_modal)
    monkeypatch.setattr(
        Toast,
        "show_message",
        lambda _self, text, duration_ms=1800: notices.append((text, duration_ms)),
    )
    window = MainWindow(service, TaskService(resource_root), settings)

    assert window.home_page.group is not None
    assert window.home_page.group.root == legacy.root
    assert any("顶部提示预览迁移" in text for text, _duration in notices)
    assert not window.home_page.notice_banner.isHidden()
    assert all(duration >= 3000 for text, duration in notices if "旧项目结构" in text)
    assert list(tmp_path.glob("旧项目直接打开-迁移前备份-*")) == []
    window.close()

    first_notice_count = sum("旧项目结构" in text for text, _duration in notices)
    reopened_settings = SettingsService(
        QSettings(str(ini_path), QSettings.Format.IniFormat)
    )
    reopened_window = MainWindow(
        service, TaskService(resource_root), reopened_settings
    )
    assert reopened_window.home_page.group is not None
    assert reopened_window.home_page.group.root == legacy.root
    assert sum("旧项目结构" in text for text, _duration in notices) == first_notice_count
    reopened_window.close()


def test_renamed_project_folder_only_shows_temporary_notice(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, group = create_group(tmp_path, resource_root, "目录改名提示")
    project = group.projects[0].path
    (project / "客户反馈").rename(project / "客户意见")
    settings = SettingsService(
        QSettings(str(tmp_path / "renamed.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(service, TaskService(resource_root), settings)
    notices = []

    def fail_modal(*_args, **_kwargs):
        pytest.fail("文件夹改名不应打开阻塞弹窗")

    monkeypatch.setattr(QMessageBox, "question", fail_modal)
    monkeypatch.setattr(QMessageBox, "warning", fail_modal)
    monkeypatch.setattr(
        window,
        "show_toast",
        lambda text, duration_ms=1800: notices.append((text, duration_ms)),
    )

    assert window.load_project_group(group.root)
    assert window.home_page.group is not None
    assert any("被改名或删除" in text for text, _duration in notices)
    assert all(duration >= 3000 for _text, duration in notices)

    initial_notice_count = len(notices)
    original = project / "原始需求"
    renamed_original = project / "需求资料"
    original.rename(renamed_original)
    window.home_page.refresh_current_project()
    window.home_page.refresh_current_project()

    assert len(notices) == initial_notice_count + 1

    (project / "客户意见").rename(project / "客户反馈")
    renamed_original.rename(original)
    window.home_page.refresh_current_project()
    assert settings.structure_notice_fingerprint(group.root) == ""

    original.rename(renamed_original)
    window.home_page.refresh_current_project()
    assert len(notices) == initial_notice_count + 2
    window.home_page.refresh_current_project()
    assert len(notices) == initial_notice_count + 2
    window.close()


def test_invalid_registered_group_is_skipped_without_modal(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, group = create_group(tmp_path, resource_root, "配置缺失")
    ini_path = tmp_path / "invalid.ini"
    settings = SettingsService(QSettings(str(ini_path), QSettings.Format.IniFormat))
    settings.save_recent_group_path(group.root)
    (group.root / service.MANIFEST_NAME).unlink()
    notices = []

    def fail_modal(*_args, **_kwargs):
        pytest.fail("损坏项目组不应打开阻塞弹窗")

    monkeypatch.setattr(QMessageBox, "question", fail_modal)
    monkeypatch.setattr(QMessageBox, "warning", fail_modal)
    monkeypatch.setattr(
        Toast,
        "show_message",
        lambda _self, text, duration_ms=1800: notices.append((text, duration_ms)),
    )
    window = MainWindow(service, TaskService(resource_root), settings)

    assert window.home_page.group is None
    assert any("控制台可以继续使用" in text for text, _duration in notices)
    window.close()

    first_notice_count = len(notices)
    reopened_settings = SettingsService(
        QSettings(str(ini_path), QSettings.Format.IniFormat)
    )
    reopened_window = MainWindow(
        service, TaskService(resource_root), reopened_settings
    )
    assert reopened_window.home_page.group is None
    assert len(notices) == first_notice_count
    reopened_window.close()


def test_toast_is_anchored_to_feedback_card_and_repositions(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
) -> None:
    service, group = create_group(tmp_path, resource_root, "提示位置")
    settings = SettingsService(
        QSettings(str(tmp_path / "toast.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(service, TaskService(resource_root), settings)
    window.resize(1400, 900)
    window.show()
    assert window.load_project_group(group.root)
    window.show_toast("项目文件夹被改名或删除，相关功能暂不可用。", 5000)
    QTest.qWait(80)

    parent = window.toast.parentWidget()
    anchor = window.home_page.feedback_card
    anchor_top_left = anchor.mapTo(parent, QPoint(0, 0))
    assert abs(window.toast.geometry().center().x() - (anchor_top_left.x() + anchor.width() // 2)) <= 1
    assert window.toast.x() >= anchor_top_left.x() + 24
    assert window.toast.y() == anchor_top_left.y() + 8

    window.resize(1180, 760)
    QTest.qWait(80)
    anchor_top_left = anchor.mapTo(parent, QPoint(0, 0))
    assert abs(window.toast.geometry().center().x() - (anchor_top_left.x() + anchor.width() // 2)) <= 1
    assert window.toast.x() >= anchor_top_left.x() + 24
    window.close()


def test_delete_fallback_opens_legacy_group_without_backup_prompt(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, current = create_group(tmp_path, resource_root, "当前组")
    _, legacy = create_group(tmp_path, resource_root, "待迁移组")
    manifest = service.read_manifest(legacy.root)
    manifest["schema_version"] = 1
    manifest["product_directory"] = "工作文件"
    manifest["delivery_directory"] = "最终交付"
    (legacy.root / service.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    settings = SettingsService(
        QSettings(str(tmp_path / "fallback.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(service, TaskService(resource_root), settings)
    assert window.load_project_group(current.root)
    settings.register_project_group(legacy.root)

    def fail_modal(*_args, **_kwargs):
        pytest.fail("删除后切换旧项目组不应打开阻塞弹窗")

    monkeypatch.setattr(QMessageBox, "question", fail_modal)
    monkeypatch.setattr(QMessageBox, "warning", fail_modal)

    window._complete_group_removal(current.root, 0, True, False)

    assert window.home_page.group is not None
    assert window.home_page.group.root == legacy.root
    assert legacy.root in settings.registered_group_paths()
    assert list(tmp_path.glob("待迁移组-迁移前备份-*")) == []
    window.close()


def test_recycle_confirmation_contains_no_backup_prompt(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, group = create_group(tmp_path, resource_root, "直接回收")
    settings = SettingsService(
        QSettings(str(tmp_path / "confirmation.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(service, TaskService(resource_root), settings)
    settings.register_project_group(group.root)
    window.home_page.set_available_groups(settings.registered_group_paths())
    monkeypatch.setattr(window, "_confirm_group_deletion", lambda *_args: "recycle")
    started = []
    monkeypatch.setattr(
        window,
        "_start_recycle_operation",
        lambda *args: started.append(args),
    )
    texts = []

    class FakeMessageBox:
        Icon = QMessageBox.Icon
        ButtonRole = QMessageBox.ButtonRole

        def __init__(self, _parent):
            self.confirm = object()
            self.cancel = object()
            self.clicked = None

        def setIcon(self, _icon):
            pass

        def setWindowTitle(self, text):
            texts.append(text)

        def setText(self, text):
            texts.append(text)

        def setInformativeText(self, text):
            texts.append(text)

        def addButton(self, text, _role):
            texts.append(text)
            return self.confirm if text == "确认移到回收站" else self.cancel

        def setDefaultButton(self, _button):
            pass

        def exec(self):
            self.clicked = self.confirm

        def clickedButton(self):
            return self.clicked

    import ui.main_window as main_window_module

    monkeypatch.setattr(main_window_module, "QMessageBox", FakeMessageBox)
    window.delete_project_group(group.root)

    assert started
    assert "备份" not in "\n".join(texts)
    assert "确认移到回收站" in texts
    window.close()
