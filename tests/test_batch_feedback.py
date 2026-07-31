import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from models import ProjectGroup
from services import (
    BatchFeedbackError,
    BatchFeedbackService,
    BatchPlanChangedError,
    FeedbackService,
    PendingFeedback,
    ProjectService,
    TaskService,
)
from tests.helpers import tool_binding


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def write_docx(path: Path, text: str = "feedback") -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
        )
        archive.writestr(
            "word/document.xml",
            f"<?xml version='1.0'?><document><body>{text}</body></document>",
        )
    return path


@pytest.fixture()
def batch_group(tmp_path: Path) -> tuple[ProjectService, ProjectGroup]:
    sources = []
    for index in range(3):
        source = tmp_path / f"source-{index + 1}.json"
        source.write_text(json.dumps({"title": f"课件 {index + 1}"}), encoding="utf-8")
        sources.append(source)
    project_service = ProjectService(RESOURCE_ROOT)
    group = project_service.create_project_group(
        "中文 空格项目组",
        3,
        tmp_path,
        sources,
        tool_binding(RESOURCE_ROOT),
        project_names=["一元二次方程", "函数 图像", "很长的勾股定理课件名称"],
    )
    return project_service, group


def make_service(project_service: ProjectService) -> BatchFeedbackService:
    feedback = FeedbackService()
    return BatchFeedbackService(
        project_service, feedback, TaskService(RESOURCE_ROOT)
    )


def add_round(project: Path, number: int, name: str = "历史.txt") -> None:
    round_root = project / "客户反馈" / f"第{number}轮"
    round_root.mkdir()
    (round_root / name).write_text(f"round {number}", encoding="utf-8")


def project_ids(group: ProjectGroup, count: int = 3) -> tuple[str, ...]:
    return tuple(project.project_id for project in group.projects[:count])


def test_word_feedback_supports_docx_doc_and_rejects_docm(tmp_path: Path) -> None:
    service = FeedbackService()
    docx = service.pending_from_file(write_docx(tmp_path / "统一意见.docx"))
    legacy = tmp_path / "旧版意见.doc"
    legacy.write_bytes(b"legacy word material")
    doc = service.pending_from_file(legacy)

    assert docx.kind == doc.kind == "word"
    assert "DOCX" in docx.detail and "DOC" in doc.detail
    assert docx.source_path == (tmp_path / "统一意见.docx").resolve()
    macro = tmp_path / "宏反馈.docm"
    macro.write_bytes(b"macro")
    with pytest.raises(ValueError, match="带宏"):
        service.pending_from_file(macro)
    broken = tmp_path / "损坏.docx"
    broken.write_bytes(b"not a package")
    with pytest.raises(ValueError, match="基础结构"):
        service.pending_from_file(broken)


def test_word_feedback_save_and_rescan_is_not_parse_failure(
    batch_group, tmp_path: Path
) -> None:
    _project_service, group = batch_group
    service = FeedbackService()
    docx = service.pending_from_file(write_docx(tmp_path / "反馈.docx"))
    legacy = tmp_path / "反馈.doc"
    legacy.write_bytes(b"legacy")
    doc = service.pending_from_file(legacy)
    result = service.save_pending(group.projects[0].path, 1, [docx, doc])

    assert not result.errors
    scanned = service.saved_items(group.projects[0].path, 1)
    assert {item.kind for item in scanned} == {"word"}
    assert {item.status for item in scanned} == {"已保存"}


def test_preview_calculates_each_project_round_independently(batch_group) -> None:
    project_service, group = batch_group
    add_round(group.projects[1].path, 1)
    add_round(group.projects[2].path, 1)
    add_round(group.projects[2].path, 3)
    targets = make_service(project_service).preview_rounds(
        group.root, project_ids(group)
    )

    assert [target.latest_round for target in targets] == [None, 1, 3]
    assert [target.target_round for target in targets] == [1, 2, 4]


def test_append_uses_each_latest_round_and_blocks_missing_round(batch_group) -> None:
    project_service, group = batch_group
    add_round(group.projects[0].path, 2)
    add_round(group.projects[1].path, 4)
    service = make_service(project_service)
    targets = service.preview_rounds(
        group.root,
        project_ids(group, 2),
        BatchFeedbackService.STRATEGY_APPEND,
    )
    assert [target.target_round for target in targets] == [2, 4]
    with pytest.raises(BatchFeedbackError, match="还没有反馈轮次"):
        service.preview_rounds(
            group.root,
            project_ids(group),
            BatchFeedbackService.STRATEGY_APPEND,
        )


def test_stale_round_preview_blocks_whole_batch(batch_group, tmp_path: Path) -> None:
    project_service, group = batch_group
    service = make_service(project_service)
    ids = project_ids(group, 2)
    preview = service.preview_rounds(group.root, ids)
    add_round(group.projects[0].path, 1)
    material = FeedbackService().pending_from_file(
        write_docx(tmp_path / "统一反馈.docx")
    )
    with pytest.raises(BatchPlanChangedError, match="预览后发生变化"):
        service.save_batch(
            group.root,
            ids,
            BatchFeedbackService.STRATEGY_NEXT,
            [material],
            expected_targets=preview,
        )
    assert not (group.projects[1].path / "客户反馈" / "第1轮").exists()


def test_batch_save_writes_independent_rounds_notes_and_record(
    batch_group, tmp_path: Path
) -> None:
    project_service, group = batch_group
    add_round(group.projects[1].path, 2)
    source = write_docx(tmp_path / "客户统一意见.docx")
    image = tmp_path / "参考截图.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    feedback = FeedbackService()
    items = [feedback.pending_from_file(source), feedback.pending_from_file(image)]
    hints = {
        group.projects[0].project_id: "对应 Word 第一部分",
        group.projects[1].project_id: "对应 Word 第二部分",
    }
    service = make_service(project_service)
    result = service.save_batch(
        group.root,
        project_ids(group, 2),
        BatchFeedbackService.STRATEGY_NEXT,
        items,
        "两部分分别对应两门课件",
        hints,
    )

    assert source.is_file()
    assert [target.target_round for target in result.targets] == [1, 3]
    for target in result.targets:
        round_root = target.project_path / "客户反馈" / f"第{target.target_round}轮"
        assert (round_root / source.name).read_bytes() == source.read_bytes()
        note = next(round_root.glob("批量反馈说明-*.txt")).read_text(encoding="utf-8")
        assert f"项目稳定 ID：{target.project_id}" in note
        assert f"本次目标轮次：第{target.target_round}轮" in note
        assert hints[target.project_id] in note
        assert "不得把其他课件" in note
    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    assert record["feedback_save_status"] == "success"
    assert [item["target_round"] for item in record["targets"]] == [1, 3]
    assert len(record["shared_materials"]) == 2


def test_duplicate_physical_file_is_preserved_once(batch_group, tmp_path: Path) -> None:
    project_service, group = batch_group
    source = tmp_path / "重复.txt"
    source.write_text("同一个文件", encoding="utf-8")
    feedback = FeedbackService()
    first = feedback.pending_from_file(source)
    second = feedback.pending_from_file(source)
    result = make_service(project_service).save_batch(
        group.root,
        project_ids(group, 2),
        BatchFeedbackService.STRATEGY_NEXT,
        [first, second],
    )
    assert result.material_names == ("重复.txt",)


def test_different_sources_with_same_name_block_whole_batch(
    batch_group, tmp_path: Path
) -> None:
    project_service, group = batch_group
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "反馈.txt").write_text("left", encoding="utf-8")
    (right / "反馈.txt").write_text("right", encoding="utf-8")
    feedback = FeedbackService()
    with pytest.raises(BatchFeedbackError, match="不同来源的同名材料"):
        make_service(project_service).save_batch(
            group.root,
            project_ids(group, 2),
            BatchFeedbackService.STRATEGY_NEXT,
            [
                feedback.pending_from_file(left / "反馈.txt"),
                feedback.pending_from_file(right / "反馈.txt"),
            ],
        )
    assert not any(
        (project.path / "客户反馈" / "第1轮").exists()
        for project in group.projects[:2]
    )


def test_target_name_conflict_blocks_all_projects(batch_group, tmp_path: Path) -> None:
    project_service, group = batch_group
    add_round(group.projects[0].path, 1, "统一.txt")
    add_round(group.projects[1].path, 1, "其他.txt")
    source = tmp_path / "统一.txt"
    source.write_text("new", encoding="utf-8")
    item = FeedbackService().pending_from_file(source)
    with pytest.raises(BatchFeedbackError, match="已存在同名文件"):
        make_service(project_service).save_batch(
            group.root,
            project_ids(group, 2),
            BatchFeedbackService.STRATEGY_APPEND,
            [item],
        )
    assert not (group.projects[1].path / "客户反馈" / "第1轮" / "统一.txt").exists()


@pytest.mark.parametrize("invalid_kind", ["missing", "directory", "symlink"])
def test_invalid_source_blocks_whole_batch(
    batch_group, tmp_path: Path, invalid_kind: str
) -> None:
    project_service, group = batch_group
    source = tmp_path / "失效.txt"
    source.write_text("feedback", encoding="utf-8")
    item = FeedbackService().pending_from_file(source)
    if invalid_kind == "missing":
        source.unlink()
    elif invalid_kind == "directory":
        source.unlink()
        source.mkdir()
    else:
        link = tmp_path / "链接.txt"
        try:
            link.symlink_to(source)
        except OSError:
            pytest.skip("当前 Windows 环境不允许创建符号链接")
        item = PendingFeedback(
            item_id="link",
            name=link.name,
            kind="text",
            source_path=link,
            size_bytes=source.stat().st_size,
        )
    with pytest.raises(BatchFeedbackError):
        make_service(project_service).save_batch(
            group.root,
            project_ids(group, 2),
            BatchFeedbackService.STRATEGY_NEXT,
            [item],
        )
    assert not any(
        (project.path / "客户反馈" / "第1轮").exists()
        for project in group.projects[:2]
    )


def test_project_move_after_preview_blocks_whole_batch(batch_group, tmp_path: Path) -> None:
    project_service, group = batch_group
    service = make_service(project_service)
    ids = project_ids(group, 2)
    preview = service.preview_rounds(group.root, ids)
    moved = group.root / "移动后的课件"
    group.projects[1].path.rename(moved)
    source = tmp_path / "反馈.txt"
    source.write_text("feedback", encoding="utf-8")
    with pytest.raises((BatchPlanChangedError, BatchFeedbackError)):
        service.save_batch(
            group.root,
            ids,
            BatchFeedbackService.STRATEGY_NEXT,
            [FeedbackService().pending_from_file(source)],
            expected_targets=preview,
        )
    assert not (group.projects[0].path / "客户反馈" / "第1轮").exists()


def test_commit_failure_rolls_back_new_rounds(batch_group, tmp_path: Path, monkeypatch) -> None:
    project_service, group = batch_group
    service = make_service(project_service)
    source = tmp_path / "反馈.txt"
    source.write_text("feedback", encoding="utf-8")
    original = service._promote_new_round
    calls = 0

    def fail_second(staged: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated commit failure")
        original(staged, destination)

    monkeypatch.setattr(service, "_promote_new_round", fail_second)
    with pytest.raises(BatchFeedbackError, match="已回滚"):
        service.save_batch(
            group.root,
            project_ids(group, 2),
            BatchFeedbackService.STRATEGY_NEXT,
            [FeedbackService().pending_from_file(source)],
        )
    assert not any(
        (project.path / "客户反馈" / "第1轮").exists()
        for project in group.projects[:2]
    )


def test_append_failure_keeps_all_historical_feedback(
    batch_group, tmp_path: Path, monkeypatch
) -> None:
    project_service, group = batch_group
    for project in group.projects[:2]:
        add_round(project.path, 1)
    service = make_service(project_service)
    source = tmp_path / "追加.txt"
    source.write_text("append", encoding="utf-8")
    original = service._promote_file
    calls = 0

    def fail_second(staged: Path, destination: Path, allow_replace: bool = False) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated append failure")
        original(staged, destination, allow_replace)

    monkeypatch.setattr(service, "_promote_file", fail_second)
    with pytest.raises(BatchFeedbackError):
        service.save_batch(
            group.root,
            project_ids(group, 2),
            BatchFeedbackService.STRATEGY_APPEND,
            [FeedbackService().pending_from_file(source)],
        )
    for project in group.projects[:2]:
        round_root = project.path / "客户反馈" / "第1轮"
        assert (round_root / "历史.txt").is_file()
        assert not (round_root / "追加.txt").exists()


def save_two_project_batch(
    project_service: ProjectService, group: ProjectGroup, tmp_path: Path
):
    source = write_docx(tmp_path / "任务材料.docx")
    service = make_service(project_service)
    saved = service.save_batch(
        group.root,
        project_ids(group, 2),
        BatchFeedbackService.STRATEGY_NEXT,
        [FeedbackService().pending_from_file(source)],
        project_hints={
            group.projects[0].project_id: "第一部分",
            group.projects[1].project_id: "第二部分",
        },
    )
    return service, saved


def test_batch_task_generation_uses_independent_rounds_and_paths(
    batch_group, tmp_path: Path
) -> None:
    project_service, group = batch_group
    add_round(group.projects[1].path, 2)
    service, saved = save_two_project_batch(project_service, group, tmp_path)
    generated = service.generate_tasks(saved.record_path)

    assert len(generated.project_task_paths) == 2
    assert "反馈轮次：第1轮" in generated.project_task_paths[0].read_text(encoding="utf-8")
    assert "反馈轮次：第3轮" in generated.project_task_paths[1].read_text(encoding="utf-8")
    for task_path in generated.project_task_paths:
        content = task_path.read_text(encoding="utf-8")
        assert f"批次 ID：{saved.batch_id}" in content
        assert "不得把其他课件" in content
    batch_task = generated.batch_task_path.read_text(encoding="utf-8")
    assert "本次反馈轮次：第1轮" in batch_task
    assert "本次反馈轮次：第3轮" in batch_task
    assert all(str(path) in batch_task for path in generated.project_task_paths)
    record = json.loads(saved.record_path.read_text(encoding="utf-8"))
    assert record["task_generation_status"] == "success"


def test_batch_task_generation_failure_restores_tasks_and_configs(
    batch_group, tmp_path: Path, monkeypatch
) -> None:
    project_service, group = batch_group
    for index, project in enumerate(group.projects[:2], start=1):
        (project.path / "当前任务.md").write_text(
            f"old task {index}\n", encoding="utf-8"
        )
    service, saved = save_two_project_batch(project_service, group, tmp_path)
    snapshots = {
        path: path.read_bytes()
        for project in group.projects[:2]
        for path in (
            project.path / "当前任务.md",
            project.path / "项目配置.json",
        )
    }
    original = service._promote_file
    calls = 0

    def fail_midway(staged: Path, destination: Path, allow_replace: bool = False) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated task commit failure")
        original(staged, destination, allow_replace)

    monkeypatch.setattr(service, "_promote_file", fail_midway)
    with pytest.raises(BatchFeedbackError, match="恢复原任务"):
        service.generate_tasks(saved.record_path)
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    record = json.loads(saved.record_path.read_text(encoding="utf-8"))
    assert record["task_generation_status"] == "not_generated"
    assert not (saved.batch_directory / "批量反馈任务.md").exists()


def test_batch_execution_instruction_validates_every_project_task(
    batch_group, tmp_path: Path
) -> None:
    project_service, group = batch_group
    service, saved = save_two_project_batch(project_service, group, tmp_path)
    generated = service.generate_tasks(saved.record_path)
    instruction = service.batch_execution_instruction(saved.record_path)
    assert instruction == (
        f"请读取并完整执行以下批量任务文件：\n\n{generated.batch_task_path}"
    )
    generated.project_task_paths[1].write_text("其他模式", encoding="utf-8")
    with pytest.raises(BatchFeedbackError, match="已被改写"):
        service.batch_execution_instruction(saved.record_path)


def test_project_hint_remains_bound_to_stable_id_after_display_rename(
    batch_group, tmp_path: Path
) -> None:
    project_service, group = batch_group
    service = make_service(project_service)
    ids = project_ids(group, 2)
    hints = {
        group.projects[0].project_id: "稳定提示 A",
        group.projects[1].project_id: "稳定提示 B",
    }
    manifest = project_service.read_manifest(group.root)
    manifest["projects"].reverse()
    (group.root / project_service.MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    config = project_service.read_project_config(group.projects[0].path)
    config["display_name"] = "重命名后的课件"
    project_service.write_project_config(group.projects[0].path, config)
    source = tmp_path / "反馈.txt"
    source.write_text("feedback", encoding="utf-8")
    result = service.save_batch(
        group.root,
        ids,
        BatchFeedbackService.STRATEGY_NEXT,
        [FeedbackService().pending_from_file(source)],
        project_hints=hints,
    )
    record = json.loads(result.record_path.read_text(encoding="utf-8"))
    by_id = {item["project_id"]: item for item in record["targets"]}
    assert by_id[group.projects[0].project_id]["project_hint"] == "稳定提示 A"
    assert by_id[group.projects[1].project_id]["project_hint"] == "稳定提示 B"


def test_path_limit_preflight_blocks_before_writing(
    batch_group, tmp_path: Path, monkeypatch
) -> None:
    project_service, group = batch_group
    service = make_service(project_service)
    source = tmp_path / "反馈.txt"
    source.write_text("feedback", encoding="utf-8")
    monkeypatch.setattr(service, "WINDOWS_SAFE_PATH_LIMIT", 30)
    with pytest.raises(BatchFeedbackError, match="路径过长"):
        service.save_batch(
            group.root,
            project_ids(group, 2),
            BatchFeedbackService.STRATEGY_NEXT,
            [FeedbackService().pending_from_file(source)],
        )
    assert not any(
        (project.path / "客户反馈" / "第1轮").exists()
        for project in group.projects[:2]
    )
