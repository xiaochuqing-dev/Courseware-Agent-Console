import json
from pathlib import Path

import pytest

from services import (
    FeedbackService,
    ProjectService,
    PromptService,
    TaskService,
    TaskType,
    TaskValidationResult,
)
from services.identity_service import write_courseware_meta
from tests.helpers import create_valid_product, tool_binding


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def create_project(tmp_path: Path):
    source = tmp_path / "需求.json"
    source.write_text('{"title":"反馈上下文"}', encoding="utf-8")
    project_service = ProjectService(RESOURCE_ROOT)
    group = project_service.create_project_group(
        "反馈上下文测试",
        1,
        tmp_path,
        [source],
        tool_binding(RESOURCE_ROOT),
    )
    return project_service, group.projects[0]


def save_text_feedback(project_root: Path, round_number: int, text: str) -> None:
    feedback = FeedbackService()
    result = feedback.save_pending(
        project_root,
        round_number,
        [feedback.pending_from_text(text)],
    )
    assert result.saved_paths and not result.errors


def test_first_build_task_has_structured_snapshot_and_round_zero(tmp_path: Path) -> None:
    _project_service, project = create_project(tmp_path)
    tasks = TaskService(RESOURCE_ROOT)
    task_path = tasks.generate_first_build_task(project.path, "使用绿色主题")

    validation = tasks.validate_current_task(
        project.path,
        expected_task_type=TaskType.FIRST_BUILD,
        expected_round=0,
        expected_special_requirements="使用绿色主题",
    )
    assert validation.valid
    assert "任务类型：首次制作" in task_path.read_text(encoding="utf-8")
    snapshot = json.loads(
        (project.path / TaskService.SNAPSHOT_NAME).read_text(encoding="utf-8")
    )
    assert snapshot["task_type"] == TaskType.FIRST_BUILD.value
    assert snapshot["feedback_round"] == 0
    assert snapshot["input_context"]["feedback_materials"] == []


def test_feedback_task_has_complete_material_snapshot_and_expires_on_append(
    tmp_path: Path,
) -> None:
    project_service, project = create_project(tmp_path)
    product = create_valid_product(project_service, project)
    save_text_feedback(project.path, 1, "调整字号")
    tasks = TaskService(RESOURCE_ROOT)
    task_path = tasks.generate_feedback_task(project.path, 1, "保留绿色配色")
    content = task_path.read_text(encoding="utf-8")

    assert "任务类型：反馈修改" in content
    assert "反馈轮次：第1轮" in content
    assert str((project.path / "客户反馈" / "第1轮").resolve()) in content
    assert "补充说明.txt" in content
    assert "类型：文本" in content
    assert "大小：" in content
    assert "SHA-256：" in content
    assert "实际路径：" in content
    assert product.name in content and str(product.resolve()) in content
    assert "保留绿色配色" in content
    assert "## 输入优先级" in content
    assert "## 执行顺序" in content
    assert "枚举“客户反馈/第1轮/”全部文件" in content
    assert "{{" not in content and "}}" not in content

    prompt = PromptService(
        RESOURCE_ROOT,
        task_service=tasks,
    )
    instruction = prompt.project_task_execution_instruction(
        project.path,
        expected_task_type=TaskType.FEEDBACK_MODIFICATION,
        expected_round=1,
        expected_special_requirements="保留绿色配色",
    )
    assert instruction.endswith(str(task_path))

    save_text_feedback(project.path, 1, "补充：标题加粗")
    expired = tasks.validate_current_task(project.path)
    assert not expired.valid
    assert "反馈轮次材料已变化" in expired.reason
    with pytest.raises(ValueError, match="已过期"):
        prompt.project_task_execution_instruction(project.path)

    tasks.generate_feedback_task(project.path, 1, "保留绿色配色")
    regenerated = task_path.read_text(encoding="utf-8")
    assert "补充说明.txt" in regenerated
    assert regenerated.count("### 材料 ") == 2


def test_round_special_requirement_and_product_changes_expire_task(
    tmp_path: Path,
) -> None:
    project_service, project = create_project(tmp_path)
    product = create_valid_product(project_service, project)
    save_text_feedback(project.path, 1, "第一轮")
    save_text_feedback(project.path, 2, "第二轮")
    tasks = TaskService(RESOURCE_ROOT)
    tasks.generate_feedback_task(project.path, 1, "要求 A")

    mismatch = tasks.validate_current_task(
        project.path,
        expected_task_type=TaskType.FEEDBACK_MODIFICATION,
        expected_round=2,
        expected_special_requirements="要求 A",
    )
    assert not mismatch.valid and "当前选择第2轮" in mismatch.reason
    changed_requirement = tasks.validate_current_task(
        project.path,
        expected_task_type=TaskType.FEEDBACK_MODIFICATION,
        expected_round=1,
        expected_special_requirements="要求 B",
    )
    assert not changed_requirement.valid and "特殊要求已变化" in changed_requirement.reason

    product.write_text(
        product.read_text(encoding="utf-8").replace("测试产品", "产品已变化", 1),
        encoding="utf-8",
    )
    changed_product = tasks.validate_current_task(project.path)
    assert not changed_product.valid and "最新有效产品已变化" in changed_product.reason


def test_old_task_without_snapshot_cannot_be_copied(tmp_path: Path) -> None:
    _project_service, project = create_project(tmp_path)
    (project.path / "当前任务.md").write_text(
        "# 当前任务\n\n任务类型：反馈修改\n反馈轮次：第1轮\n",
        encoding="utf-8",
    )
    tasks = TaskService(RESOURCE_ROOT)
    validation = tasks.validate_current_task(project.path)
    assert not validation.valid and "缺少结构化快照" in validation.reason
    with pytest.raises(ValueError, match="缺少结构化快照"):
        PromptService(RESOURCE_ROOT, task_service=tasks).project_task_execution_instruction(
            project.path
        )


def test_reworking_existing_round_allocates_new_product_version(tmp_path: Path) -> None:
    project_service, project = create_project(tmp_path)
    create_valid_product(project_service, project)
    save_text_feedback(project.path, 1, "第一版反馈")
    tasks = TaskService(RESOURCE_ROOT)
    first_task = tasks.generate_feedback_task(project.path, 1, "")
    first_snapshot = json.loads(
        (project.path / TaskService.SNAPSHOT_NAME).read_text(encoding="utf-8")
    )
    output = first_snapshot["input_context"]["output"]
    first_product = Path(output["path"])
    first_product.write_text(
        "<!doctype html><html><head><title>第一轮产品</title></head>"
        "<body><section class=\"slide\">v1</section></body></html>",
        encoding="utf-8",
    )
    write_courseware_meta(
        first_product,
        project.project_id,
        output["artifact_id"],
        output["version_number"],
        1,
    )
    original_bytes = first_product.read_bytes()

    save_text_feedback(project.path, 1, "追加反馈")
    tasks.generate_feedback_task(project.path, 1, "")
    second_content = first_task.read_text(encoding="utf-8")
    second_snapshot = json.loads(
        (project.path / TaskService.SNAPSHOT_NAME).read_text(encoding="utf-8")
    )
    second_output = second_snapshot["input_context"]["output"]
    assert second_output["version_number"] > output["version_number"]
    assert Path(second_output["path"]) != first_product
    assert "反馈轮次：第1轮" in second_content
    assert first_product.read_bytes() == original_bytes


def test_task_commit_failure_restores_task_snapshot_and_config(
    tmp_path: Path, monkeypatch
) -> None:
    project_service, project = create_project(tmp_path)
    create_valid_product(project_service, project)
    save_text_feedback(project.path, 1, "回滚测试")
    tasks = TaskService(RESOURCE_ROOT)
    task_path = project.path / "当前任务.md"
    snapshot_path = project.path / TaskService.SNAPSHOT_NAME
    config_path = project.path / "项目配置.json"
    task_path.write_text("old task\n", encoding="utf-8")
    snapshot_path.write_text('{"old":true}\n', encoding="utf-8")
    before = {
        path: path.read_bytes() for path in (task_path, snapshot_path, config_path)
    }
    prepared = tasks.prepare_feedback_task(project.path, 1, "")
    monkeypatch.setattr(
        tasks,
        "validate_current_task",
        lambda *args, **kwargs: TaskValidationResult(
            False,
            True,
            "模拟写入后校验失败",
        ),
    )

    with pytest.raises(ValueError, match="写入后自校验失败"):
        tasks.commit_prepared_task(prepared)
    assert all(path.read_bytes() == content for path, content in before.items())
