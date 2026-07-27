import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from services import ProjectService, TaskService, ValidationError
from tests.helpers import tool_binding
from ui.pages.create_project_page import CreateProjectPage


@pytest.fixture
def resource_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources"


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def write_json(path: Path, marker: str = "source") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"marker": marker}, ensure_ascii=False), encoding="utf-8")
    return path


def write_material(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def material_map(service: ProjectService, source: Path, *materials: Path):
    return {service.path_key(source): list(materials)}


def wait_until(app: QApplication, predicate, timeout_ms: int = 10000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        app.processEvents()
        QTest.qWait(20)
        elapsed += 20
    assert predicate()


def test_create_without_materials_records_empty_compatible_array(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")

    group = service.create_project_group(
        "无材料", 1, tmp_path, [source], tool_binding(resource_root)
    )

    config = service.read_project_config(group.projects[0].path)
    assert config["source_materials"] == []
    assert list((group.projects[0].path / "原始需求").iterdir()) == [
        group.projects[0].path / "原始需求" / source.name
    ]


def test_multiple_images_and_pdf_are_copied_and_recorded(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    materials = [
        write_material(tmp_path / "image-1.png", b"\x89PNG-first"),
        write_material(tmp_path / "image-2.jpg", b"\xff\xd8second"),
        write_material(tmp_path / "reference.pdf", b"%PDF-1.7 reference"),
    ]

    group = service.create_project_group(
        "多材料",
        1,
        tmp_path,
        [source],
        tool_binding(resource_root),
        project_materials=material_map(service, source, *materials),
    )

    project = group.projects[0]
    config = service.read_project_config(project.path)
    records = config["source_materials"]
    assert [record["file_name"] for record in records] == [path.name for path in materials]
    for source_material, record in zip(materials, records):
        copied = project.path / "原始需求" / source_material.name
        assert copied.read_bytes() == source_material.read_bytes()
        assert record["size"] == source_material.stat().st_size
        assert record["sha256"] == hashlib.sha256(source_material.read_bytes()).hexdigest().upper()
        assert record["source_id"]


def test_materials_stay_separate_across_projects_and_allow_empty_project(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    sources = [
        write_json(tmp_path / "json" / "one.json", "one"),
        write_json(tmp_path / "json" / "two.json", "two"),
        write_json(tmp_path / "json" / "three.json", "three"),
    ]
    first = write_material(tmp_path / "materials" / "first.png", b"first")
    second = write_material(tmp_path / "materials" / "second.pdf", b"second")
    bindings = {
        service.path_key(sources[0]): [first],
        service.path_key(sources[1]): [second],
        service.path_key(sources[2]): [],
    }

    group = service.create_project_group(
        "分项目材料",
        3,
        tmp_path,
        sources,
        tool_binding(resource_root),
        project_materials=bindings,
    )

    assert {path.name for path in (group.projects[0].path / "原始需求").iterdir()} == {
        "one.json",
        "first.png",
    }
    assert {path.name for path in (group.projects[1].path / "原始需求").iterdir()} == {
        "two.json",
        "second.pdf",
    }
    assert {path.name for path in (group.projects[2].path / "原始需求").iterdir()} == {
        "three.json"
    }


def test_duplicate_physical_material_is_ignored_in_order(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    material = write_material(tmp_path / "same.png", b"same")
    bindings = material_map(service, source, material, material, material)

    prepared, ignored = service.validate_project_materials([source], bindings)
    assert ignored == 2
    assert [item.file_name for item in prepared[service.path_key(source)]] == ["same.png"]

    group = service.create_project_group(
        "材料去重",
        1,
        tmp_path,
        [source],
        tool_binding(resource_root),
        project_materials=bindings,
    )
    assert len(service.read_project_config(group.projects[0].path)["source_materials"]) == 1


def test_same_name_from_different_sources_blocks_atomic_creation(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    first = write_material(tmp_path / "a" / "same.png", b"first")
    second = write_material(tmp_path / "b" / "same.png", b"second")

    with pytest.raises(ValidationError, match="同名首次制作材料"):
        service.create_project_group(
            "同名冲突",
            1,
            tmp_path,
            [source],
            tool_binding(resource_root),
            project_materials=material_map(service, source, first, second),
        )

    assert not (tmp_path / "同名冲突").exists()
    assert not list(tmp_path.glob(".同名冲突.creating-*"))


def test_material_name_matching_json_blocks_atomic_creation(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "json" / "source.json")
    material = write_material(tmp_path / "material" / "source.json", b"not the json")

    with pytest.raises(ValidationError, match="与 JSON 文件同名"):
        service.create_project_group(
            "JSON同名冲突",
            1,
            tmp_path,
            [source],
            tool_binding(resource_root),
            project_materials=material_map(service, source, material),
        )

    assert not (tmp_path / "JSON同名冲突").exists()
    assert not list(tmp_path.glob(".JSON同名冲突.creating-*"))


@pytest.mark.parametrize("kind", ["missing", "directory", "unreadable"])
def test_invalid_material_blocks_creation_without_partial_directory(
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / f"{kind}.json")
    material = tmp_path / f"{kind}.bin"
    expected = "不存在"
    if kind == "directory":
        material.mkdir()
        expected = "不是普通文件"
    elif kind == "unreadable":
        material.write_bytes(b"locked")
        expected = "无法读取"
        original_hash = service.file_sha256

        def fail_hash(path: Path) -> str:
            if Path(path).resolve() == material.resolve():
                raise PermissionError("denied")
            return original_hash(path)

        monkeypatch.setattr(service, "file_sha256", fail_hash)

    with pytest.raises(ValidationError, match=expected):
        service.create_project_group(
            f"无效材料-{kind}",
            1,
            tmp_path,
            [source],
            tool_binding(resource_root),
            project_materials=material_map(service, source, material),
        )

    assert not (tmp_path / f"无效材料-{kind}").exists()
    assert not list(tmp_path.glob(f".无效材料-{kind}.creating-*"))


def test_old_project_config_without_source_materials_remains_readable(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    group = service.create_project_group(
        "旧配置兼容", 1, tmp_path, [source], tool_binding(resource_root)
    )
    config_path = group.projects[0].path / service.PROJECT_CONFIG_NAME
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw.pop("source_materials")
    config_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    assert service.read_project_config(group.projects[0].path)["source_materials"] == []
    assert service.load_project_group(group.root).projects[0].project_id


def test_first_build_task_requires_all_original_materials(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    material = write_material(tmp_path / "reference.pdf", b"%PDF")
    group = service.create_project_group(
        "任务材料说明",
        1,
        tmp_path,
        [source],
        tool_binding(resource_root),
        project_materials=material_map(service, source, material),
    )

    task = TaskService(resource_root).generate_first_build_task(
        group.projects[0].path, ""
    ).read_text(encoding="utf-8")
    assert "先枚举该目录中的全部文件" in task
    assert "JSON 是结构化主需求" in task
    assert "无法读取的二进制材料" in task


def test_gui_material_binding_survives_reorder_and_rename(
    app: QApplication, tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    sources = [
        write_json(tmp_path / "one.json", "one"),
        write_json(tmp_path / "two.json", "two"),
    ]
    material = write_material(tmp_path / "reference.png", b"image")
    page = CreateProjectPage(service)
    page.count_input.setValue(2)
    page.add_json_files(sources)
    page.mapping_list.setCurrentRow(0)
    assert page.add_material_files([material]) == (1, 0)
    first_key = service.path_key(sources[0])

    page._move_mapping(1)
    page.project_names_by_path[first_key] = "重命名项目"
    page._refresh_mapping_list()

    assert page.json_files == [sources[1], sources[0]]
    assert page.materials_by_project[first_key] == [material]
    assert page.project_materials()[first_key] == [material]
    assert "材料 1 个" in page.mapping_list.item(1).text()
    assert "重命名项目" in page.mapping_list.item(1).text()
    page.close()
    app.processEvents()


def test_gui_delete_material_mapping_requires_confirmation(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    material = write_material(tmp_path / "reference.png", b"image")
    page = CreateProjectPage(service)
    page.count_input.setValue(1)
    page.add_json_files([source])
    page.add_material_files([material])
    answers = iter(
        [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: next(answers))

    page._remove_mapping()
    assert page.json_files == [source]
    assert page.materials_by_project[service.path_key(source)] == [material]

    page._remove_mapping()
    assert page.json_files == []
    assert service.path_key(source) not in page.materials_by_project
    page.close()
    app.processEvents()


def test_gui_duplicate_add_and_cancelled_dialog_keep_materials_unchanged(
    app: QApplication,
    tmp_path: Path,
    resource_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    material = write_material(tmp_path / "reference.png", b"image")
    page = CreateProjectPage(service)
    page.count_input.setValue(1)
    page.add_json_files([source])
    assert page.add_material_files([material, material]) == (1, 1)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: ([], ""))

    for _ in range(10):
        page._choose_material_files()

    assert page.project_materials()[service.path_key(source)] == [material]
    page.close()
    app.processEvents()


def test_large_material_copy_keeps_gui_event_loop_responsive(
    app: QApplication, tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    source = write_json(tmp_path / "source.json")
    large = write_material(tmp_path / "large.bin", b"L" * (8 * 1024 * 1024))
    page = CreateProjectPage(service)
    binding = tool_binding(resource_root)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    wait_until(app, lambda: page._tool_validation_result is not None)
    page.name_input.setText("大文件后台复制")
    page.location_input.setText(str(tmp_path))
    page.count_input.setValue(1)
    page.add_json_files([source])
    page.add_material_files([large])
    event_processed: list[bool] = []
    created: list[Path] = []
    page.project_created.connect(created.append)
    QTimer.singleShot(0, lambda: event_processed.append(True))

    page._create_project_group()
    wait_until(app, lambda: bool(created))

    assert event_processed == [True]
    copied = created[0] / source.stem / "原始需求" / large.name
    assert copied.stat().st_size == large.stat().st_size
    page.close()
    app.processEvents()
