from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import send2trash
from PySide6.QtCore import QEvent, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

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


def test_schema_v2_has_stable_group_id_and_single_product_directory(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root)
    manifest = service.read_manifest(group.root)
    assert manifest["schema_version"] == 2
    assert manifest["group_id"]
    assert manifest["product_directory"] == "产品迭代"
    assert "delivery_directory" not in manifest
    project = group.projects[0].path
    assert {path.name for path in project.iterdir() if path.is_dir()} == {
        "原始需求",
        "客户反馈",
        "产品迭代",
    }


def test_legacy_migration_backs_up_merges_without_overwrite_and_is_repeat_safe(
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

    assert result.backup_root.is_dir()
    migrated = group.root / "项目1"
    assert (migrated / "产品迭代" / "初始版本.html").read_text(encoding="utf-8") == "working"
    conflicts = list((migrated / "产品迭代").glob("初始版本-来自最终交付*.html"))
    assert len(conflicts) == 1
    assert conflicts[0].read_text(encoding="utf-8") == "delivery"
    assert not any((migrated / name).exists() for name in service.LEGACY_DIRECTORIES)
    assert "旧验收结论" in (migrated / "项目记录.md").read_text(encoding="utf-8")
    assert service.read_manifest(group.root)["schema_version"] == 2
    with pytest.raises(RuntimeError, match="已经是 schema v2"):
        service.migrate_legacy_group(group.root)


def test_migration_failure_keeps_original_and_named_backup(
    tmp_path: Path, resource_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, group = create_group(tmp_path, resource_root, "迁移失败")
    manifest = service.read_manifest(group.root)
    manifest["schema_version"] = 1
    (group.root / service.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    sentinel = group.root / "项目1" / "原始需求" / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def fail_merge(*_args, **_kwargs):
        raise OSError("simulated migration failure")

    monkeypatch.setattr(service, "_merge_directory", fail_merge)
    (group.root / "项目1" / "工作文件").mkdir()
    with pytest.raises(ProjectCreationError, match="迁移失败"):
        service.migrate_legacy_group(group.root)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    backups = list(tmp_path.glob("迁移失败-迁移前备份-*"))
    assert len(backups) == 1
    assert (backups[0] / "项目1" / "原始需求" / "sentinel.txt").is_file()


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


def test_delete_fallback_never_opens_legacy_backup_prompt(
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
    prompts = []
    monkeypatch.setattr(
        window, "_offer_legacy_migration", lambda path: prompts.append(path)
    )

    window._complete_group_removal(current.root, 0, True, False)

    assert prompts == []
    assert window.home_page.group is None
    assert legacy.root in settings.registered_group_paths()
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
