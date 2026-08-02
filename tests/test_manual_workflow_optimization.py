import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from services import (
    ArchiveService,
    ProjectService,
    PromptService,
    SettingsService,
    TaskService,
    WorkflowOptimizationError,
    WorkflowOptimizationInput,
    WorkflowOptimizationService,
    WorkflowProjectInfo,
)
from tests.helpers import create_valid_product, tool_binding
from ui.main_window import MainWindow
from ui.pages.workflow_optimization_page import WorkflowOptimizationPage
from ui.widgets import GlassCheckBox


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def workflow_group(tmp_path: Path):
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    source = tmp_path / "项目源.json"
    source.write_text(
        json.dumps({"title": "工作流优化测试"}, ensure_ascii=False),
        encoding="utf-8",
    )
    project_service = ProjectService(resource_root)
    group = project_service.create_project_group(
        "中文 空格项目组",
        1,
        tmp_path,
        [source],
        tool_binding(resource_root),
    )
    return resource_root, project_service, group


def workflow_input(
    group_root: Path,
    *,
    projects: tuple[Path, ...] = (),
    description: str = "",
    materials: tuple[Path, ...] = (),
) -> WorkflowOptimizationInput:
    return WorkflowOptimizationInput(
        group_root=group_root,
        selected_project_paths=projects,
        user_description=description,
        material_paths=materials,
    )


def write_material(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def archive_only_project(
    project_service: ProjectService,
    group,
) -> Path:
    project = group.projects[0]
    create_valid_product(project_service, project)
    return ArchiveService().archive_project(
        group.root, project.project_id or project.name
    )


def wait_until(
    app: QApplication, predicate, timeout_ms: int = 10000
) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        app.processEvents()
        QTest.qWait(20)
        elapsed += 20
    assert predicate()


def test_description_only_generates_fixed_task_and_empty_material_directory(
    workflow_group,
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)

    result = service.generate_task(
        workflow_input(group.root, description="减少模板中不必要的动效。")
    )

    expected = group.root / "工作流优化" / "当前优化任务.md"
    assert result.task_path == expected.resolve()
    assert result.material_paths == ()
    assert (group.root / "工作流优化" / "补充材料").is_dir()
    assert not list((group.root / "工作流优化" / "补充材料").iterdir())
    content = expected.read_text(encoding="utf-8")
    assert "减少模板中不必要的动效。" in content
    assert "本轮未选择参考项目。" in content
    assert "本轮未提供补充材料。" in content
    assert str(group.root / "公共工具" / "WORKFLOW.md") in content
    assert str(group.root / "公共工具" / "template.html") in content
    assert str(group.root / "公共工具" / "validate-tool.js") in content


def test_project_only_generates_with_read_only_reference(workflow_group) -> None:
    resource_root, project_service, group = workflow_group
    archived = archive_only_project(project_service, group)
    service = WorkflowOptimizationService(resource_root)

    result = service.generate_task(
        workflow_input(group.root, projects=(archived,))
    )

    content = result.task_path.read_text(encoding="utf-8")
    assert str(archived.resolve()) in content
    assert "未单独填写，请根据参考项目或材料分析。" in content
    assert "只读边界：仅用于分析" in content
    assert service.validate_current_task(group.root).valid


def test_material_only_copies_in_order_and_task_lists_sha256(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    materials = (
        write_material(tmp_path / "参考图.png", b"png"),
        write_material(tmp_path / "说明 文档.pdf", b"%PDF"),
        write_material(tmp_path / "notes.md", b"# notes"),
    )

    result = service.generate_task(
        workflow_input(group.root, materials=materials)
    )

    assert [path.name for path in result.material_paths] == [
        path.name for path in materials
    ]
    for source, copied in zip(materials, result.material_paths):
        assert copied.read_bytes() == source.read_bytes()
    content = result.task_path.read_text(encoding="utf-8")
    positions = [content.index(f"- 文件名：{path.name}") for path in materials]
    assert positions == sorted(positions)
    assert content.count("- SHA-256：") == len(materials)


def test_projects_description_and_materials_generate_together(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, project_service, group = workflow_group
    archived = archive_only_project(project_service, group)
    material = write_material(tmp_path / "补充要求.md", b"# detail")
    service = WorkflowOptimizationService(resource_root)

    result = service.generate_task(
        workflow_input(
            group.root,
            projects=(archived,),
            description="同时参考历史项目和补充材料。",
            materials=(material,),
        )
    )

    content = result.task_path.read_text(encoding="utf-8")
    assert "同时参考历史项目和补充材料。" in content
    assert str(archived.resolve()) in content
    assert material.name in content
    snapshot = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    assert len(snapshot["input_context"]["reference_projects"]) == 1
    assert len(snapshot["input_context"]["materials"]) == 1


@pytest.mark.parametrize(
    ("include_project", "include_description", "include_material"),
    (
        (True, True, False),
        (True, False, True),
        (False, True, True),
    ),
)
def test_each_two_input_combination_generates(
    workflow_group,
    tmp_path: Path,
    include_project: bool,
    include_description: bool,
    include_material: bool,
) -> None:
    resource_root, project_service, group = workflow_group
    archived = (
        archive_only_project(project_service, group)
        if include_project
        else None
    )
    material = (
        write_material(tmp_path / "双输入材料.md", b"# pair")
        if include_material
        else None
    )
    description = "双输入组合说明。" if include_description else ""
    result = WorkflowOptimizationService(resource_root).generate_task(
        workflow_input(
            group.root,
            projects=(archived,) if archived is not None else (),
            description=description,
            materials=(material,) if material is not None else (),
        )
    )

    content = result.task_path.read_text(encoding="utf-8")
    if archived is not None:
        assert str(archived.resolve()) in content
    if description:
        assert description in content
    if material is not None:
        assert material.name in content
    assert WorkflowOptimizationService(resource_root).validate_current_task(
        group.root
    ).valid


def test_all_three_inputs_empty_are_rejected(workflow_group) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)

    with pytest.raises(WorkflowOptimizationError, match="至少选择参考项目"):
        service.generate_task(workflow_input(group.root))

    assert not (group.root / "工作流优化" / "当前优化任务.md").exists()


def test_illegal_reference_project_is_rejected(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    outside = tmp_path / "伪造已完成项目"
    outside.mkdir()
    (outside / "项目记录.md").write_text("fake", encoding="utf-8")

    with pytest.raises(WorkflowOptimizationError, match="已完成项目"):
        WorkflowOptimizationService(resource_root).generate_task(
            workflow_input(group.root, projects=(outside,))
        )


def test_duplicate_physical_material_is_deduplicated_preserving_order(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    first = write_material(tmp_path / "first.txt", b"first")
    second = write_material(tmp_path / "second.txt", b"second")

    result = service.generate_task(
        workflow_input(
            group.root,
            description="验证材料去重。",
            materials=(first, first, second, first),
        )
    )

    assert [path.name for path in result.material_paths] == ["first.txt", "second.txt"]


def test_same_name_different_sources_blocks_without_replacing_current_task(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    original = service.generate_task(
        workflow_input(group.root, description="保留的旧任务。")
    )
    original_content = original.task_path.read_text(encoding="utf-8")
    first = write_material(tmp_path / "a" / "same.txt", b"first")
    second = write_material(tmp_path / "b" / "same.txt", b"second")

    with pytest.raises(WorkflowOptimizationError, match="同名冲突"):
        service.generate_task(
            workflow_input(
                group.root,
                description="不应成功。",
                materials=(first, second),
            )
        )

    assert original.task_path.read_text(encoding="utf-8") == original_content


def test_material_matching_managed_file_name_is_rejected(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    material = write_material(tmp_path / "当前优化任务.md", b"reserved")

    with pytest.raises(WorkflowOptimizationError, match="控制台管理文件同名"):
        service.generate_task(
            workflow_input(
                group.root,
                description="不应覆盖管理文件。",
                materials=(material,),
            )
        )


@pytest.mark.parametrize("kind", ["missing", "directory", "unreadable"])
def test_invalid_material_blocks_generation_without_partial_task(
    workflow_group,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old = service.generate_task(
        workflow_input(group.root, description="旧任务仍应有效。")
    )
    old_content = old.task_path.read_text(encoding="utf-8")
    old_snapshot = old.snapshot_path.read_text(encoding="utf-8")
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

    with pytest.raises(WorkflowOptimizationError, match=expected):
        service.generate_task(
            workflow_input(
                group.root,
                description="新任务不应生成。",
                materials=(material,),
            )
        )

    assert old.task_path.read_text(encoding="utf-8") == old_content
    assert old.snapshot_path.read_text(encoding="utf-8") == old_snapshot


def test_previous_task_snapshot_and_materials_are_archived_together(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old_material = write_material(tmp_path / "old.pdf", b"old")
    first = service.generate_task(
        workflow_input(
            group.root,
            description="第一轮任务。",
            materials=(old_material,),
        )
    )
    first_snapshot = first.snapshot_path.read_text(encoding="utf-8")
    new_material = write_material(tmp_path / "new.png", b"new")

    second = service.generate_task(
        workflow_input(
            group.root,
            description="第二轮任务。",
            materials=(new_material,),
        )
    )

    assert second.archived_path is not None
    assert second.archived_path.parent == (
        group.root / "工作流优化" / "历史优化任务"
    )
    assert "第一轮任务。" in (
        second.archived_path / "当前优化任务.md"
    ).read_text(encoding="utf-8")
    assert (
        second.archived_path / "当前优化任务快照.json"
    ).read_text(encoding="utf-8") == first_snapshot
    assert (second.archived_path / "补充材料" / old_material.name).read_bytes() == b"old"
    assert "第二轮任务。" in first.task_path.read_text(encoding="utf-8")
    assert [path.name for path in (group.root / "工作流优化" / "补充材料").iterdir()] == [
        new_material.name
    ]


def test_copy_failure_keeps_previous_task_snapshot_and_materials(
    workflow_group, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old_material = write_material(tmp_path / "old.txt", b"old")
    old = service.generate_task(
        workflow_input(
            group.root,
            description="复制失败前的任务。",
            materials=(old_material,),
        )
    )
    old_content = old.task_path.read_text(encoding="utf-8")
    old_snapshot = old.snapshot_path.read_text(encoding="utf-8")
    new_material = write_material(tmp_path / "new.txt", b"new")

    def fail_copy(_source, _destination):
        raise OSError("copy failed")

    monkeypatch.setattr(
        "services.workflow_optimization_service.shutil.copy2", fail_copy
    )
    with pytest.raises(WorkflowOptimizationError, match="生成当前优化任务失败"):
        service.generate_task(
            workflow_input(
                group.root,
                description="不应替换旧任务。",
                materials=(new_material,),
            )
        )

    assert old.task_path.read_text(encoding="utf-8") == old_content
    assert old.snapshot_path.read_text(encoding="utf-8") == old_snapshot
    assert (group.root / "工作流优化" / "补充材料" / old_material.name).read_bytes() == b"old"


def test_archive_failure_rolls_back_current_task_snapshot_and_materials(
    workflow_group, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old_material = write_material(tmp_path / "before.txt", b"before")
    old = service.generate_task(
        workflow_input(
            group.root,
            description="归档失败前的任务。",
            materials=(old_material,),
        )
    )
    old_content = old.task_path.read_text(encoding="utf-8")
    old_snapshot = old.snapshot_path.read_text(encoding="utf-8")
    new_material = write_material(tmp_path / "after.txt", b"after")
    original_move = service._move_path

    def fail_archive_move(source: Path, destination: Path) -> None:
        if source.name.startswith(".归档中-"):
            raise OSError("archive failed")
        original_move(source, destination)

    monkeypatch.setattr(service, "_move_path", fail_archive_move)
    with pytest.raises(WorkflowOptimizationError, match="生成当前优化任务失败"):
        service.generate_task(
            workflow_input(
                group.root,
                description="不应留在当前任务。",
                materials=(new_material,),
            )
        )

    assert old.task_path.read_text(encoding="utf-8") == old_content
    assert old.snapshot_path.read_text(encoding="utf-8") == old_snapshot
    material_root = group.root / "工作流优化" / "补充材料"
    assert [path.name for path in material_root.iterdir()] == [old_material.name]
    assert not list((group.root / "工作流优化").glob(".归档中-*"))


def test_incomplete_rollback_preserves_old_task_in_recovery_directory(
    workflow_group, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old = service.generate_task(
        workflow_input(group.root, description="必须保留的旧任务。")
    )
    old_content = old.task_path.read_text(encoding="utf-8")
    replacement = write_material(tmp_path / "replacement.txt", b"new")
    original_move = service._move_path

    def fail_new_task_promotion(source: Path, destination: Path) -> None:
        if (
            source.parent.name.startswith(".生成中-")
            and source.name == service.CURRENT_TASK_NAME
        ):
            raise OSError("promotion failed")
        original_move(source, destination)

    original_replace = Path.replace

    def fail_old_task_restore(path: Path, target: Path):
        if (
            path.parent.name.startswith(".归档中-")
            and path.name == service.CURRENT_TASK_NAME
        ):
            raise OSError("restore failed")
        return original_replace(path, target)

    monkeypatch.setattr(service, "_move_path", fail_new_task_promotion)
    monkeypatch.setattr(Path, "replace", fail_old_task_restore)

    with pytest.raises(WorkflowOptimizationError, match="回滚未完整完成"):
        service.generate_task(
            workflow_input(
                group.root,
                description="本轮应失败。",
                materials=(replacement,),
            )
        )

    recovery = list((group.root / "工作流优化").glob(".归档中-*"))
    assert len(recovery) == 1
    assert (recovery[0] / service.CURRENT_TASK_NAME).read_text(
        encoding="utf-8"
    ) == old_content


def test_load_current_input_rejects_material_path_escape(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    material = write_material(tmp_path / "safe.txt", b"safe")
    result = service.generate_task(
        workflow_input(
            group.root,
            description="验证快照路径边界。",
            materials=(material,),
        )
    )
    snapshot = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
    snapshot["input_context"]["materials"][0]["file_name"] = "..\\..\\outside.txt"
    result.snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert service.load_current_input(group.root) is None


def test_validation_rejects_non_file_entry_in_managed_material_directory(
    workflow_group,
) -> None:
    resource_root, _project_service, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    service.generate_task(
        workflow_input(group.root, description="验证材料目录边界。")
    )
    unexpected = group.root / "工作流优化" / "补充材料" / "unexpected"
    unexpected.mkdir()

    validation = service.validate_current_task(group.root)

    assert not validation.valid
    assert "非普通文件" in validation.reason


def test_execution_instruction_revalidates_real_absolute_chinese_space_path(
    workflow_group,
) -> None:
    resource_root, _project_service, group = workflow_group
    result = WorkflowOptimizationService(resource_root).generate_task(
        workflow_input(group.root, description="验证执行指令。")
    )
    prompt_service = PromptService(resource_root)

    instruction = prompt_service.workflow_task_execution_instruction(group.root)

    assert instruction == f"请读取并完整执行以下任务文件：\n{result.task_path}"
    assert result.task_path.is_absolute()
    result.task_path.unlink()
    with pytest.raises(ValueError, match="尚未生成"):
        prompt_service.workflow_task_execution_instruction(group.root)


def test_unified_workflow_page_supports_real_checkbox_combination(
    app: QApplication,
    workflow_group,
    tmp_path: Path,
) -> None:
    resource_root, project_service, group = workflow_group
    archived = archive_only_project(project_service, group)
    settings = SettingsService(
        QSettings(str(tmp_path / "unified-ui.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        project_service, TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.resize(1100, 720)
    window.show()
    window.show_workflow_optimization()
    app.processEvents()
    page = window.workflow_page

    assert page.layout().contentsMargins().left() == 28
    assert page.layout().contentsMargins().top() == 24
    assert not hasattr(page, "mode_stack")
    assert not hasattr(page, "review_mode_button")
    assert not hasattr(page, "manual_mode_button")
    assert page.unified_scroll.widget() is not None
    assert page.apply_button.text() == "查看当前任务"
    assert page.preview_button.text() == "复制执行指令"
    assert page.project_list.count() == 1
    assert page.project_list.height() <= 80
    assert page.project_list.y() < page.manual_description_input.y()
    assert page.manual_description_input.y() < page.material_empty_label.y()
    assert not page.copy_button.isEnabled()
    item = page.project_list.item(0)
    assert item.text() == ""
    row = page.project_list.itemWidget(item)
    assert row is not None
    assert isinstance(row.checkbox, GlassCheckBox)
    unchecked = row.checkbox.grab().toImage()
    QTest.mouseClick(
        row.checkbox,
        Qt.MouseButton.LeftButton,
        pos=row.checkbox.rect().center(),
    )
    app.processEvents()
    assert row.is_checked()
    assert page.copy_button.isEnabled()
    checked = row.checkbox.grab().toImage()
    assert checked != unchecked

    QTest.mouseClick(
        row.checkbox,
        Qt.MouseButton.LeftButton,
        pos=row.checkbox.rect().center(),
    )
    app.processEvents()
    assert not row.is_checked()
    assert not page.copy_button.isEnabled()
    page.manual_description_input.setPlainText("仅使用人工优化说明。")
    assert page.copy_button.isEnabled()
    page.manual_description_input.clear()
    assert not page.copy_button.isEnabled()

    material = write_material(tmp_path / "组合材料.md", b"# combined")
    assert page.add_material_files([material, material]) == (1, 1)
    assert page.copy_button.isEnabled()
    QTest.mouseClick(
        row.checkbox,
        Qt.MouseButton.LeftButton,
        pos=row.checkbox.rect().center(),
    )
    page.manual_description_input.setPlainText("项目、说明、材料同时使用。")
    assert page.selected_project_paths() == (archived.resolve(),)
    assert page.copy_button.isEnabled()

    page._generate_task()
    wait_until(app, lambda: not page._generation_in_progress)

    content = (
        group.root / "工作流优化" / "当前优化任务.md"
    ).read_text(encoding="utf-8")
    assert "项目、说明、材料同时使用。" in content
    assert str(archived.resolve()) in content
    assert material.name in content
    assert page.preview_button.isEnabled()
    window.close()
    app.processEvents()


def test_project_list_uses_bounded_height_and_internal_scroll(
    app: QApplication, tmp_path: Path
) -> None:
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    page = WorkflowOptimizationPage(
        ArchiveService(),
        PromptService(resource_root),
        WorkflowOptimizationService(resource_root),
    )
    page.resize(900, 650)
    page.show()
    app.processEvents()

    def info(index: int) -> WorkflowProjectInfo:
        root = tmp_path / f"项目-{index:02d}"
        return WorkflowProjectInfo(
            project_path=root,
            display_name=f"参考项目 {index:02d}",
            record_path=root / "项目记录.md",
            original_requirements_path=root / "原始需求",
            latest_product_path=None,
            feedback_rounds=(),
        )

    page._rebuild_project_list((info(1),), set())
    assert page.project_list.height() <= 80
    page._rebuild_project_list(tuple(info(index) for index in range(1, 14)), set())
    app.processEvents()
    assert page.project_list.height() <= 240
    assert page.project_list.verticalScrollBar().maximum() > 0
    page.close()
    app.processEvents()


def test_cancelled_material_dialog_and_duplicates_preserve_state(
    app: QApplication,
    workflow_group,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_root, project_service, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "material-ui.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        project_service, TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.show_workflow_optimization()
    page = window.workflow_page
    material = write_material(tmp_path / "material.png", b"image")
    assert page.add_material_files([material, material]) == (1, 1)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args, **kwargs: ([], ""))

    for _ in range(5):
        page._choose_material_files()

    assert page.selected_materials == [material]
    assert page.material_count_label.text() == "1 个文件"
    window.close()
    app.processEvents()


def test_large_material_generation_runs_in_background_and_enables_result(
    app: QApplication, workflow_group, tmp_path: Path
) -> None:
    resource_root, project_service, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "background.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        project_service, TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.show_workflow_optimization()
    page = window.workflow_page
    large = write_material(tmp_path / "large.bin", b"L" * (8 * 1024 * 1024))
    page.add_material_files([large])
    page.manual_description_input.setPlainText("后台复制大文件。")
    event_processed: list[bool] = []
    QTimer.singleShot(0, lambda: event_processed.append(True))

    page._generate_task()
    assert page._generation_in_progress
    assert not page.copy_button.isEnabled()
    wait_until(app, lambda: not page._generation_in_progress)

    assert event_processed == [True]
    assert page.preview_button.isEnabled()
    assert (group.root / "工作流优化" / "补充材料" / large.name).stat().st_size == (
        large.stat().st_size
    )
    window.close()
    app.processEvents()


def test_home_first_build_and_feedback_modes_copy_execution_instruction(
    app: QApplication, workflow_group, tmp_path: Path
) -> None:
    resource_root, project_service, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "home-copy.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        project_service, TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    home = window.home_page
    project = group.projects[0]

    assert home.first_execute_button.text() == "复制执行指令"
    assert not home.first_execute_button.isEnabled()
    home._generate_task()
    assert home.first_execute_button.isEnabled()
    home.first_execute_button.click()
    expected = f"请读取并完整执行以下任务文件：\n{project.path / '当前任务.md'}"
    assert QGuiApplication.clipboard().text() == expected

    create_valid_product(project_service, project)
    feedback_round = project.path / "客户反馈" / "第1轮"
    feedback_round.mkdir()
    (feedback_round / "反馈.txt").write_text("调整字号。", encoding="utf-8")
    home.refresh_current_project()
    home.feedback_task_button.click()
    assert home.feedback_execute_button.text() == "复制执行指令"
    assert not home.feedback_execute_button.isEnabled()
    home._generate_task()
    assert home.feedback_execute_button.isEnabled()
    home.feedback_execute_button.click()
    assert QGuiApplication.clipboard().text() == expected
    assert "任务类型：反馈修改" in (project.path / "当前任务.md").read_text(
        encoding="utf-8"
    )
    window.close()
    app.processEvents()


def test_home_execution_buttons_remain_connected_after_workflow_navigation(
    app: QApplication, workflow_group, tmp_path: Path
) -> None:
    resource_root, project_service, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "navigation-copy.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(project_service, TaskService(resource_root), settings)
    window.load_project_group(group.root)
    window.show()
    window.show_workflow_optimization()
    page = window.workflow_page
    page.manual_description_input.setPlainText("先生成工作流任务。")
    page._generate_task()
    wait_until(app, lambda: not page._generation_in_progress)
    page.preview_button.click()

    window.show_home_page()
    home = window.home_page
    project = group.projects[0]
    home.first_build_button.click()
    home._generate_task()
    home.first_execute_button.click()
    create_valid_product(project_service, project)
    feedback_root = project.path / "客户反馈" / "第1轮"
    feedback_root.mkdir()
    (feedback_root / "反馈.txt").write_text("调整字号。", encoding="utf-8")
    home.refresh_current_project()
    home.feedback_task_button.click()
    home._generate_task()

    QGuiApplication.clipboard().clear()
    QTest.mouseClick(
        home.feedback_execute_button,
        Qt.MouseButton.LeftButton,
        pos=home.feedback_execute_button.rect().center(),
    )
    app.processEvents()

    assert QGuiApplication.clipboard().text() == (
        f"请读取并完整执行以下任务文件：\n{project.path / '当前任务.md'}"
    )
    window.close()
    app.processEvents()
