import json
from pathlib import Path

import pytest

from services import (
    ArchiveConflictError,
    ArchiveService,
    FeedbackService,
    NoProductVersionError,
    ProjectService,
    PromptService,
    TaskService,
)


@pytest.fixture
def phase2_group(tmp_path: Path):
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    sources: list[Path] = []
    for index in range(1, 4):
        source = tmp_path / f"需求-{index}.json"
        source.write_text(
            json.dumps({"project": index}, ensure_ascii=False), encoding="utf-8"
        )
        sources.append(source)
    group = ProjectService(resource_root).create_project_group(
        "九年级", 3, tmp_path, sources
    )
    return resource_root, group


def test_feedback_rounds_append_new_round_and_name_conflict(
    phase2_group, tmp_path: Path
) -> None:
    _, group = phase2_group
    project = group.projects[2].path
    service = FeedbackService()
    source = tmp_path / "圈画反馈.png"
    source.write_bytes(b"image-one")
    first = service.pending_from_file(source)
    note = service.pending_from_text("动画速度需要放慢")

    first_result = service.save_pending(project, 1, [first, note])
    assert not first_result.errors
    assert (project / "客户反馈" / "第1轮" / "圈画反馈.png").read_bytes() == b"image-one"
    assert (project / "客户反馈" / "第1轮" / "补充说明.txt").is_file()

    duplicate = service.pending_from_file(source)
    service.save_pending(project, 1, [duplicate])
    assert (project / "客户反馈" / "第1轮" / "圈画反馈 (2).png").is_file()

    second_note = service.pending_from_text("第二轮文字")
    service.save_pending(project, 2, [second_note])
    (project / "客户反馈" / "temp").mkdir()
    (project / "客户反馈" / "截图").mkdir()
    assert service.scan_rounds(project) == (1, 2)


def test_multiple_pending_text_names_do_not_overlap() -> None:
    service = FeedbackService()
    first = service.pending_from_text("第一段")
    second = service.pending_from_text("第二段", {first.name})
    third = service.pending_from_text("第三段", {first.name, second.name})
    assert [first.name, second.name, third.name] == [
        "补充说明.txt",
        "补充说明-2.txt",
        "补充说明-3.txt",
    ]


def test_feedback_partial_failure_keeps_successful_files(
    phase2_group, tmp_path: Path
) -> None:
    _, group = phase2_group
    project = group.projects[0].path
    service = FeedbackService()
    successful = service.pending_from_text("可保存")
    source = tmp_path / "稍后删除.pdf"
    source.write_bytes(b"pdf")
    missing = service.pending_from_file(source)
    source.unlink()

    result = service.save_pending(project, 1, [successful, missing])
    assert result.saved_item_ids == (successful.item_id,)
    assert len(result.errors) == 1
    assert (project / "客户反馈" / "第1轮" / "补充说明.txt").is_file()

    only_missing = tmp_path / "另一个已删除.pdf"
    only_missing.write_bytes(b"pdf")
    failed_item = service.pending_from_file(only_missing)
    only_missing.unlink()
    failed = service.save_pending(project, 2, [failed_item])
    assert failed.errors
    assert not (project / "客户反馈" / "第2轮").exists()


def test_feedback_task_allows_empty_override(phase2_group) -> None:
    resource_root, group = phase2_group
    project = group.projects[0].path
    service = FeedbackService()
    service.save_pending(project, 1, [service.pending_from_text("修改字号")])

    task = TaskService(resource_root).generate_feedback_task(project, 1, "")
    content = task.read_text(encoding="utf-8")
    assert "任务类型：反馈修改" in content
    assert "反馈轮次：第1轮" in content
    assert "## 特殊要求\n\n无" in content
    assert "客户反馈/第1轮/" in content


def test_latest_product_uses_numeric_version_not_mtime_or_noise(phase2_group) -> None:
    _, group = phase2_group
    project = group.projects[0].path
    products = project / "产品迭代"
    service = ArchiveService()
    (products / "初始版本.html").write_text("initial", encoding="utf-8")
    assert service.latest_product(project).name == "初始版本.html"
    (products / "第2轮修改.html").write_text("two", encoding="utf-8")
    (products / "第1轮修改.html").write_text("one", encoding="utf-8")
    (products / "临时版.html").write_text("noise", encoding="utf-8")
    assert service.latest_product(project).name == "第2轮修改.html"


def test_archive_requires_product_and_never_overwrites(phase2_group) -> None:
    _, group = phase2_group
    service = ArchiveService()
    project1 = group.projects[0]
    with pytest.raises(NoProductVersionError):
        service.archive_project(group.root, project1.name)

    project2 = group.projects[1]
    (project2.path / "产品迭代" / "初始版本.html").write_text(
        "product", encoding="utf-8"
    )
    destination = service.archive_destination(group.root, project2.name)
    destination.mkdir(parents=True)
    sentinel = destination / "保留.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(ArchiveConflictError):
        service.archive_project(group.root, project2.name)
    assert project2.path.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_complete_two_round_feedback_acceptance_and_archive(phase2_group) -> None:
    resource_root, group = phase2_group
    project = group.projects[2].path
    feedback = FeedbackService()
    tasks = TaskService(resource_root)
    archive = ArchiveService()

    products = project / "产品迭代"
    (products / "初始版本.html").write_text("initial", encoding="utf-8")
    round1 = [
        feedback.pending_from_bytes("微信截图-1.png", b"png-one", "image"),
        feedback.pending_from_bytes("微信截图-2.png", b"png-two", "image"),
        feedback.pending_from_text("第一轮补充说明"),
    ]
    feedback.save_pending(project, 1, round1)
    tasks.generate_feedback_task(project, 1, "")
    (products / "第1轮修改.html").write_text("round-one", encoding="utf-8")

    feedback.save_pending(project, 2, [feedback.pending_from_text("第二轮反馈")])
    tasks.generate_feedback_task(project, 2, "保留上一轮配色")
    (products / "第2轮修改.html").write_text("round-two", encoding="utf-8")
    assert archive.latest_product(project).name == "第2轮修改.html"

    prompt = PromptService(resource_root, archive).product_acceptance_prompt(project)
    assert "项目“项目3”" in prompt
    assert "产品迭代/第2轮修改.html" in prompt
    assert "不得自动把项目标记为已完成" in prompt

    destination = archive.archive_project(group.root, project.name)
    assert destination == group.root.parent / "已完成项目" / "九年级" / "项目3"
    assert not project.exists()
    assert archive.archived_projects(group.root, "九年级")[-1] == destination
    active = ProjectService(resource_root).load_project_group(group.root)
    assert [entry.name for entry in active.projects] == ["项目1", "项目2"]
