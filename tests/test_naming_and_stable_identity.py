from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QSettings

from services import (
    ArchiveService,
    ProjectService,
    SettingsService,
    TaskService,
    read_courseware_meta,
    sanitize_project_name,
)
from services.identity_service import write_courseware_meta
from tests.helpers import tool_binding


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def _source(path: Path, title: str) -> Path:
    path.write_text(json.dumps({"title": title}, ensure_ascii=False), encoding="utf-8")
    return path


def _group(tmp_path: Path, names: tuple[str, ...] = ("正负球模型（有理数加法）",)):
    service = ProjectService(RESOURCE_ROOT)
    sources = [_source(tmp_path / f"{name}.json", name) for name in names]
    group = service.create_project_group(
        "命名验收", len(sources), tmp_path, sources, tool_binding(RESOURCE_ROOT)
    )
    return service, group, sources


def test_windows_safe_name_preserves_chinese_and_uses_stable_hash() -> None:
    assert sanitize_project_name(" 正负球模型（有理数加法） ") == "正负球模型（有理数加法）"
    assert sanitize_project_name('模型<>:"/\\|?*. ') == "模型_________"
    assert sanitize_project_name("CON") == "CON_"
    first = sanitize_project_name("很长的课题名称" * 20, 32)
    second = sanitize_project_name("很长的课题名称" * 20, 32)
    assert first == second
    assert len(first) <= 32


def test_json_stems_create_schema_v3_readable_projects_and_stable_ids(
    tmp_path: Path,
) -> None:
    service, group, sources = _group(tmp_path, ("立体表面最短路径", "数轴上点的运动"))
    assert [project.display_name for project in group.projects] == [path.stem for path in sources]
    manifest = service.read_manifest(group.root)
    assert manifest["schema_version"] == 3
    assert UUID(manifest["group_id"])
    for order, project in enumerate(group.projects, start=1):
        assert project.path.name == project.display_name
        config = service.read_project_config(project.path)
        assert UUID(config["project_id"])
        assert config["project_id"] == project.project_id
        assert config["order"] == order
        assert config["source_json"]["file_name"] == sources[order - 1].name


def test_duplicate_stems_are_deduplicated_without_overwrite(tmp_path: Path) -> None:
    service = ProjectService(RESOURCE_ROOT)
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    sources = [_source(left / "模型.json", "a"), _source(right / "模型.json", "b")]
    group = service.create_project_group(
        "重名", 2, tmp_path, sources, tool_binding(RESOURCE_ROOT)
    )
    assert [item.display_name for item in group.projects] == ["模型", "模型（2）"]
    assert all(item.path.is_dir() for item in group.projects)


def test_project_directory_rename_recovers_by_project_id(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path)
    original = group.projects[0]
    renamed = original.path.with_name("正负球课件")
    original.path.rename(renamed)

    loaded = service.load_project_group(group.root)
    recovered = loaded.projects[0]
    assert recovered.project_id == original.project_id
    assert recovered.path == renamed
    config = service.read_project_config(renamed)
    assert config["directory_name"] == "正负球课件"
    assert config["directory_rename_notice"]["old_name"] == original.directory_name

    resolved = service.resolve_directory_rename(group.root, original.project_id, False)
    assert resolved.display_name == original.display_name
    assert "directory_rename_notice" not in service.read_project_config(renamed)


def test_tasks_allocate_artifact_and_use_readable_product_names(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path)
    project = group.projects[0]
    tasks = TaskService(RESOURCE_ROOT)
    first = tasks.generate_first_build_task(project.path, "")
    first_text = first.read_text(encoding="utf-8")
    assert "预期输出：产品迭代/正负球模型（有理数加法）.html" in first_text
    assert "版本号：0" in first_text
    config = service.read_project_config(project.path)
    assert len(config["artifacts"]) == 1
    assert UUID(config["artifacts"][0]["artifact_id"])

    (project.path / "客户反馈" / "第2轮").mkdir()
    feedback = tasks.generate_feedback_task(project.path, 2, "")
    feedback_text = feedback.read_text(encoding="utf-8")
    assert "预期输出：产品迭代/正负球模型（有理数加法）（2）.html" in feedback_text
    assert "版本号：2" in feedback_text
    assert len(service.read_project_config(project.path)["artifacts"]) == 2


def test_html_rename_and_content_change_keep_artifact_identity(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path)
    project = group.projects[0]
    archive = ArchiveService(service)
    artifact = archive.allocate_artifact(project.path, 2, 2)
    product = project.path / "产品迭代" / artifact["expected_name"]
    product.write_text("<html><head></head><body>v2</body></html>", encoding="utf-8")
    write_courseware_meta(
        product,
        project.project_id,
        artifact["artifact_id"],
        2,
        2,
    )
    assert archive.latest_product(project.path) == product
    archive.reconcile_product_files(project.path)

    renamed = product.with_name("正负球最终调整.html")
    product.rename(renamed)
    assert archive.latest_product(project.path) == renamed
    notices = archive.reconcile_product_files(project.path)
    rename_notice = next(item for item in notices if item.kind == "renamed")
    archive.accept_product_rename(project.path, artifact["artifact_id"], renamed.name)
    assert not any(
        item.kind == "renamed" for item in archive.reconcile_product_files(project.path)
    )
    config = service.read_project_config(project.path)
    registered = config["artifacts"][0]
    assert rename_notice.old_name in registered["aliases"]
    assert registered["current_name"] == renamed.name

    text = renamed.read_text(encoding="utf-8").replace("v2", "v2 changed")
    renamed.write_text(text, encoding="utf-8")
    changed = archive.reconcile_product_files(project.path)
    assert any(item.kind == "content_changed" for item in changed)
    assert archive.latest_product(project.path) == renamed


def test_missing_meta_falls_back_then_manual_binding_restores_meta(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path)
    project = group.projects[0]
    archive = ArchiveService(service)
    artifact = archive.allocate_artifact(project.path, 0, 0)
    product = project.path / "产品迭代" / artifact["expected_name"]
    product.write_text("<html><head></head><body>courseware</body></html>", encoding="utf-8")
    assert archive.latest_product(project.path) == product

    custom = product.with_name("客户确认版.html")
    product.rename(custom)
    notices = archive.reconcile_product_files(project.path)
    notice = next(item for item in notices if item.kind == "unregistered")
    bound = archive.bind_product(project.path, notice.path, 0)
    meta = read_courseware_meta(custom)
    assert meta["courseware-project-id"] == project.project_id
    assert meta["courseware-artifact-id"] == bound["artifact_id"]
    assert archive.latest_product(project.path) == custom


def test_settings_persist_group_and_project_ids(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path)
    settings_file = tmp_path / "settings.ini"
    raw = QSettings(str(settings_file), QSettings.Format.IniFormat)
    settings = SettingsService(raw)
    settings.save_last_selected_project(group.root, group.projects[0].project_id)
    assert settings.last_selected_project(group.root) == group.projects[0].project_id
    stored = json.loads(raw.value(SettingsService.LAST_SELECTED_PROJECTS_KEY, type=str))
    record = next(iter(stored.values()))
    assert record == {
        "group_id": group.group_id,
        "project_id": group.projects[0].project_id,
    }


def test_legacy_project_and_product_migrate_with_backup_and_meta(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path, ("正方形纸片的翻折问题",))
    project = group.projects[0]
    legacy_path = group.root / "项目1"
    project.path.rename(legacy_path)
    (legacy_path / "项目配置.json").unlink()
    product = legacy_path / "产品迭代" / "初始版本.html"
    product.write_text("<html><head></head><body>legacy</body></html>", encoding="utf-8")
    manifest = service.read_manifest(group.root)
    manifest["schema_version"] = 2
    manifest.pop("projects", None)
    (group.root / service.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    result = service.migrate_legacy_group(group.root)
    assert result.backup_root.is_dir()
    migrated = service.load_project_group(group.root).projects[0]
    assert migrated.display_name == "正方形纸片的翻折问题"
    migrated_product = migrated.path / "产品迭代" / "正方形纸片的翻折问题.html"
    assert migrated_product.is_file()
    meta = read_courseware_meta(migrated_product)
    assert meta["courseware-project-id"] == migrated.project_id
    assert UUID(meta["courseware-artifact-id"])


def test_structure_only_migration_preserves_existing_stable_ids(tmp_path: Path) -> None:
    service, group, _ = _group(tmp_path, ("已有稳定身份",))
    project = group.projects[0]
    archive = ArchiveService(service)
    artifact = archive.allocate_artifact(project.path, 0, 0)
    product = project.path / "产品迭代" / artifact["expected_name"]
    product.write_text("<html><head></head><body>stable</body></html>", encoding="utf-8")
    write_courseware_meta(product, project.project_id, artifact["artifact_id"], 0, 0)
    (project.path / "工作文件").mkdir()

    service.migrate_legacy_group(group.root)

    migrated = service.load_project_group(group.root).projects[0]
    config = service.read_project_config(migrated.path)
    assert migrated.project_id == project.project_id
    assert config["artifacts"][0]["artifact_id"] == artifact["artifact_id"]
