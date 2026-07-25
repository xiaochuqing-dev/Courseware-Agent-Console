import json
import os
from pathlib import Path

import pytest

from services import ProjectService, TargetExistsError, ValidationError
from tests.helpers import tool_binding


@pytest.fixture
def project_service() -> ProjectService:
    return ProjectService(Path(__file__).resolve().parents[1] / "resources")


def write_json(path: Path, marker: str) -> Path:
    path.write_text(json.dumps({"marker": marker}, ensure_ascii=False), encoding="utf-8")
    return path


def test_create_three_projects_with_explicit_mapping(
    tmp_path: Path, project_service: ProjectService
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    mappings = [
        write_json(sources / "Z.json", "project-1"),
        write_json(sources / "A.json", "project-2"),
        write_json(sources / "M.json", "project-3"),
    ]

    group = project_service.create_project_group(
        "九年级", 3, tmp_path, mappings, tool_binding(project_service.resource_root)
    )

    assert [project.name for project in group.projects] == ["Z", "A", "M"]
    for index, source in enumerate(mappings, start=1):
        project = group.projects[index - 1]
        copied = project.path / "原始需求" / source.name
        assert copied.read_bytes() == source.read_bytes()
        assert (project.path / "客户反馈").is_dir()
        assert (project.path / "产品迭代").is_dir()
        assert not (project.path / "工作文件").exists()
        assert not (project.path / "最终交付").exists()
        assert not (project.path / "验收记录").exists()
        assert (project.path / "当前任务.md").is_file()
        assert (project.path / "项目记录.md").is_file()
        assert (project.path / "项目配置.json").is_file()

    for tool_name in project_service.REQUIRED_PUBLIC_TOOLS:
        assert (group.root / "公共工具" / tool_name).read_bytes() == (
            project_service.public_tools_root / tool_name
        ).read_bytes()
    assert (group.root / project_service.MANIFEST_NAME).is_file()
    assert (group.root / "AGENT任务规则.md").read_bytes() == (
        project_service.prompt_templates_root / "AGENT任务规则.md"
    ).read_bytes()


def test_count_mismatch_is_rejected_without_partial_directory(
    tmp_path: Path, project_service: ProjectService
) -> None:
    source = write_json(tmp_path / "one.json", "one")
    with pytest.raises(ValidationError, match="JSON 数量与项目数量不一致"):
        project_service.create_project_group(
            "数量不匹配",
            2,
            tmp_path,
            [source],
            tool_binding(project_service.resource_root),
        )
    assert not (tmp_path / "数量不匹配").exists()
    assert not list(tmp_path.glob(".数量不匹配.creating-*"))


def test_existing_target_is_never_overwritten(
    tmp_path: Path, project_service: ProjectService
) -> None:
    target = tmp_path / "已有项目组"
    target.mkdir()
    sentinel = target / "保留.txt"
    sentinel.write_text("keep", encoding="utf-8")
    source = write_json(tmp_path / "one.json", "one")

    with pytest.raises(TargetExistsError):
        project_service.create_project_group(
            "已有项目组",
            1,
            tmp_path,
            [source],
            tool_binding(project_service.resource_root),
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_open_in_file_manager_uses_exact_existing_path(
    tmp_path: Path, project_service: ProjectService, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(os, "startfile", opened.append)
    project_service.open_in_file_manager(tmp_path)
    assert opened == [str(tmp_path.resolve())]
