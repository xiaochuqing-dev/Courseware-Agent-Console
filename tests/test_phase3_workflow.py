import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QBoxLayout

from services import ArchiveService, ProjectService, PromptService, SettingsService, TaskService
from ui.main_window import MainWindow
from ui.pages import CreateProjectPage
from tests.helpers import tool_binding


@pytest.fixture
def completed_group(tmp_path: Path):
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    sources: list[Path] = []
    for index in range(1, 4):
        source = tmp_path / f"需求-{index}.json"
        source.write_text(json.dumps({"index": index}), encoding="utf-8")
        sources.append(source)
    project_service = ProjectService(resource_root)
    group = project_service.create_project_group(
        "工作流复盘样本", 3, tmp_path, sources, tool_binding(resource_root)
    )
    archive = ArchiveService()
    destinations: list[Path] = []
    for project in group.projects:
        (project.path / "工作文件" / "初始版本.html").write_text(
            "product", encoding="utf-8"
        )
        destinations.append(archive.archive_project(group.root, project.name))
    return resource_root, group, tuple(destinations)


def test_workflow_prompt_uses_only_selected_completed_projects(completed_group) -> None:
    resource_root, group, projects = completed_group
    prompt = PromptService(resource_root).workflow_optimization_prompt(
        group.root, [projects[0], projects[2]]
    )

    assert str(projects[0]) in prompt
    assert str(projects[2]) in prompt
    assert str(projects[1]) not in prompt
    for common_file in (
        group.root / "AGENT任务规则.md",
        group.root / "公共工具" / "WORKFLOW.md",
        group.root / "公共工具" / "template.html",
        group.root / "公共工具" / "validate-tool.js",
    ):
        assert prompt.count(str(common_file)) == 1
    assert "第一轮只分析" in prompt
    assert "先读取《项目记录.md》作为历史索引" in prompt


def test_workflow_prompt_rejects_empty_or_active_selection(completed_group) -> None:
    resource_root, group, _ = completed_group
    service = PromptService(resource_root)
    with pytest.raises(ValueError, match="至少选择一个"):
        service.workflow_optimization_prompt(group.root, [])
    active = group.root / "项目99"
    active.mkdir()
    with pytest.raises(ValueError, match="只能选择"):
        service.workflow_optimization_prompt(group.root, [active])


def test_workflow_apply_prompt_is_confirmation_scoped(completed_group) -> None:
    resource_root, group, _ = completed_group
    prompt = PromptService(resource_root).workflow_apply_prompt(group.root)
    assert "仅实施用户在本次对话中明确确认采纳" in prompt
    assert "用户未明确确认的建议不得顺手实施" in prompt
    assert str(group.root / "公共工具" / "validate-tool.js") in prompt


def test_workflow_template_missing_reports_exact_name(completed_group, tmp_path: Path) -> None:
    _, group, projects = completed_group
    service = PromptService(tmp_path / "missing-resources")
    with pytest.raises(FileNotFoundError, match="workflow_optimization_prompt.md"):
        service.workflow_optimization_prompt(group.root, [projects[0]])


def test_workflow_page_scans_completed_projects(completed_group, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    resource_root, group, _ = completed_group
    settings = SettingsService(
        QSettings(str(tmp_path / "phase3.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        ProjectService(resource_root), TaskService(resource_root), settings
    )
    window.load_project_group(group.root)
    window.show_workflow_optimization()
    page = window.workflow_page
    assert page.project_list.count() == 3
    page._set_all_checked(True)
    assert len(page.selected_project_paths()) == 3
    assert page.copy_button.isEnabled()
    window.close()
    app.processEvents()


def test_create_page_switches_to_vertical_layout_at_compact_width() -> None:
    app = QApplication.instance() or QApplication([])
    page = CreateProjectPage(ProjectService())
    page.resize(980, 680)
    page.show()
    app.processEvents()
    assert page.content_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert page.content_scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    page.resize(1280, 800)
    app.processEvents()
    assert page.content_layout.direction() == QBoxLayout.Direction.LeftToRight
    page.close()
