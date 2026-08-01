import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from services import (
    ProjectService,
    PromptService,
    SettingsService,
    TaskService,
    WorkflowOptimizationError,
    WorkflowOptimizationService,
)
from tests.helpers import create_valid_product, tool_binding
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def workflow_group(tmp_path: Path):
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    source = tmp_path / "项目源.json"
    source.write_text(
        json.dumps({"title": "人工优化测试"}, ensure_ascii=False),
        encoding="utf-8",
    )
    group = ProjectService(resource_root).create_project_group(
        "中文 空格项目组",
        1,
        tmp_path,
        [source],
        tool_binding(resource_root),
    )
    return resource_root, group


def write_material(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def wait_until(
    app: QApplication, predicate, timeout_ms: int = 10000
) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        app.processEvents()
        QTest.qWait(20)
        elapsed += 20
    assert predicate()


def test_text_only_generates_fixed_task_and_empty_material_directory(
    workflow_group,
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)

    result = service.generate_task(group.root, "减少模板中不必要的动效。")

    expected = group.root / "工作流优化" / "当前优化任务.md"
    assert result.task_path == expected.resolve()
    assert result.material_paths == ()
    assert (group.root / "工作流优化" / "补充材料").is_dir()
    assert not list((group.root / "工作流优化" / "补充材料").iterdir())
    content = expected.read_text(encoding="utf-8")
    assert "减少模板中不必要的动效。" in content
    assert "本次未提供补充材料，请直接根据用户说明执行。" in content
    assert str(group.root / "公共工具" / "WORKFLOW.md") in content
    assert str(group.root / "公共工具" / "template.html") in content
    assert str(group.root / "公共工具" / "validate-tool.js") in content
    assert "AGENT任务规则.md" not in content


def test_multiple_materials_copy_in_order_and_task_lists_them(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    materials = [
        write_material(tmp_path / "参考图.png", b"png"),
        write_material(tmp_path / "说明 文档.pdf", b"%PDF"),
        write_material(tmp_path / "sample.html", b"<html></html>"),
        write_material(tmp_path / "notes.md", b"# notes"),
        write_material(tmp_path / "logic.py", b"print('ok')"),
    ]

    result = service.generate_task(group.root, "按材料调整工作流。", materials)

    assert [path.name for path in result.material_paths] == [
        path.name for path in materials
    ]
    for source, copied in zip(materials, result.material_paths):
        assert copied.read_bytes() == source.read_bytes()
    content = result.task_path.read_text(encoding="utf-8")
    positions = [content.index(f"- {path.name}") for path in materials]
    assert positions == sorted(positions)


def test_duplicate_physical_material_is_deduplicated_preserving_order(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    first = write_material(tmp_path / "first.txt", b"first")
    second = write_material(tmp_path / "second.txt", b"second")

    result = service.generate_task(
        group.root, "验证材料去重。", [first, first, second, first]
    )

    assert [path.name for path in result.material_paths] == ["first.txt", "second.txt"]


def test_same_name_different_sources_blocks_without_replacing_current_task(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    original = service.generate_task(group.root, "保留的旧任务。")
    original_content = original.task_path.read_text(encoding="utf-8")
    first = write_material(tmp_path / "a" / "same.txt", b"first")
    second = write_material(tmp_path / "b" / "same.txt", b"second")

    with pytest.raises(WorkflowOptimizationError, match="同名冲突"):
        service.generate_task(group.root, "不应成功。", [first, second])

    assert original.task_path.read_text(encoding="utf-8") == original_content


def test_material_matching_managed_file_name_is_rejected(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    material = write_material(tmp_path / "当前优化任务.md", b"reserved")

    with pytest.raises(WorkflowOptimizationError, match="控制台管理文件同名"):
        service.generate_task(group.root, "不应覆盖管理文件。", [material])


@pytest.mark.parametrize("kind", ["missing", "directory", "unreadable"])
def test_invalid_material_blocks_generation_without_partial_task(
    workflow_group,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old = service.generate_task(group.root, "旧任务仍应有效。")
    old_content = old.task_path.read_text(encoding="utf-8")
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
        service.generate_task(group.root, "新任务不应生成。", [material])

    assert old.task_path.read_text(encoding="utf-8") == old_content


def test_previous_task_and_materials_are_archived_together(
    workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old_material = write_material(tmp_path / "old.pdf", b"old")
    first = service.generate_task(group.root, "第一轮任务。", [old_material])
    new_material = write_material(tmp_path / "new.png", b"new")

    second = service.generate_task(group.root, "第二轮任务。", [new_material])

    assert second.archived_path is not None
    assert second.archived_path.parent == (
        group.root / "工作流优化" / "历史优化任务"
    )
    assert "第一轮任务。" in (
        second.archived_path / "当前优化任务.md"
    ).read_text(encoding="utf-8")
    assert (second.archived_path / "补充材料" / old_material.name).read_bytes() == b"old"
    assert "第二轮任务。" in first.task_path.read_text(encoding="utf-8")
    assert [path.name for path in (group.root / "工作流优化" / "补充材料").iterdir()] == [
        new_material.name
    ]


def test_copy_failure_keeps_previous_task_and_materials(
    workflow_group, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old_material = write_material(tmp_path / "old.txt", b"old")
    old = service.generate_task(group.root, "复制失败前的任务。", [old_material])
    old_content = old.task_path.read_text(encoding="utf-8")
    new_material = write_material(tmp_path / "new.txt", b"new")

    def fail_copy(_source, _destination):
        raise OSError("copy failed")

    monkeypatch.setattr(
        "services.workflow_optimization_service.shutil.copy2", fail_copy
    )
    with pytest.raises(WorkflowOptimizationError, match="生成当前优化任务失败"):
        service.generate_task(group.root, "不应替换旧任务。", [new_material])

    assert old.task_path.read_text(encoding="utf-8") == old_content
    assert (group.root / "工作流优化" / "补充材料" / old_material.name).read_bytes() == b"old"


def test_archive_failure_rolls_back_current_task_and_materials(
    workflow_group, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_root, group = workflow_group
    service = WorkflowOptimizationService(resource_root)
    old_material = write_material(tmp_path / "before.txt", b"before")
    old = service.generate_task(group.root, "归档失败前的任务。", [old_material])
    old_content = old.task_path.read_text(encoding="utf-8")
    new_material = write_material(tmp_path / "after.txt", b"after")
    original_move = service._move_path

    def fail_archive_move(source: Path, destination: Path) -> None:
        if source.name.startswith(".归档中-"):
            raise OSError("archive failed")
        original_move(source, destination)

    monkeypatch.setattr(service, "_move_path", fail_archive_move)
    with pytest.raises(WorkflowOptimizationError, match="生成当前优化任务失败"):
        service.generate_task(group.root, "不应留在当前任务。", [new_material])

    assert old.task_path.read_text(encoding="utf-8") == old_content
    material_root = group.root / "工作流优化" / "补充材料"
    assert [path.name for path in material_root.iterdir()] == [old_material.name]
    assert not list((group.root / "工作流优化").glob(".归档中-*"))


def test_execution_instruction_uses_real_absolute_chinese_space_path(
    workflow_group,
) -> None:
    resource_root, group = workflow_group
    result = WorkflowOptimizationService(resource_root).generate_task(
        group.root, "验证执行指令。"
    )
    prompt_service = PromptService(resource_root)

    instruction = prompt_service.workflow_task_execution_instruction(group.root)

    assert instruction == f"请读取并完整执行以下任务文件：\n{result.task_path}"
    assert result.task_path.is_absolute()
    result.task_path.unlink()
    with pytest.raises(FileNotFoundError, match="任务文件不存在"):
        prompt_service.workflow_task_execution_instruction(group.root)


def test_manual_page_cancel_and_duplicate_selection_preserve_state(
    app: QApplication,
    workflow_group,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_root, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "manual-ui.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root), TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.show_workflow_optimization()
    page = window.workflow_page
    page.manual_mode_button.click()
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
    resource_root, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "background.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root), TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.show_workflow_optimization()
    page = window.workflow_page
    page.manual_mode_button.click()
    large = write_material(tmp_path / "large.bin", b"L" * (8 * 1024 * 1024))
    page.add_material_files([large])
    page.manual_description_input.setPlainText("后台复制大文件。")
    event_processed: list[bool] = []
    QTimer.singleShot(0, lambda: event_processed.append(True))

    page._generate_manual_task()
    assert page._generation_in_progress
    assert not page.manual_generate_button.isEnabled()
    wait_until(app, lambda: not page._generation_in_progress)

    assert event_processed == [True]
    assert page.manual_copy_execution_button.isEnabled()
    assert (group.root / "工作流优化" / "补充材料" / large.name).stat().st_size == (
        large.stat().st_size
    )
    window.close()
    app.processEvents()


def test_home_first_build_and_feedback_modes_copy_execution_instruction(
    app: QApplication, workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    settings = SettingsService(
        QSettings(str(tmp_path / "home-copy.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root), TaskService(resource_root), settings
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

    create_valid_product(ProjectService(resource_root), project)

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


def test_original_review_mode_still_selects_completed_projects(
    app: QApplication, workflow_group, tmp_path: Path
) -> None:
    resource_root, group = workflow_group
    project = group.projects[0]
    (project.path / "产品迭代" / "初始版本.html").write_text(
        "product", encoding="utf-8"
    )
    from services import ArchiveService

    ArchiveService().archive_project(group.root, project.name)
    settings = SettingsService(
        QSettings(str(tmp_path / "review.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root), TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.show_workflow_optimization()
    page = window.workflow_page

    assert page.mode_stack.currentIndex() == 0
    assert page.project_list.count() == 1
    page._set_all_checked(True)
    assert page.copy_button.isEnabled()
    assert str(page.selected_project_paths()[0]) in page._analysis_prompt()
    window.close()
    app.processEvents()
