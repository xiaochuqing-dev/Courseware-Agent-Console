import json
from pathlib import Path

import pytest
import send2trash
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from services import (
    FeedbackRecycleError,
    FeedbackService,
    ProjectService,
    SettingsService,
    TaskService,
    TaskType,
)
from tests.helpers import create_valid_product, tool_binding
from ui.main_window import MainWindow
from ui.widgets import PendingFeedbackRow


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def create_project(tmp_path: Path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"title": "反馈回收测试"}), encoding="utf-8")
    project_service = ProjectService(RESOURCE_ROOT)
    group = project_service.create_project_group(
        "反馈回收项目组",
        1,
        tmp_path,
        [source],
        tool_binding(RESOURCE_ROOT),
    )
    create_valid_product(project_service, group.projects[0])
    return project_service, group, group.projects[0].path


def add_feedback(project_root: Path, round_number: int, name: str, content: str) -> Path:
    round_root = project_root / "客户反馈" / f"第{round_number}轮"
    round_root.mkdir(parents=True, exist_ok=True)
    path = round_root / name
    path.write_text(content, encoding="utf-8")
    return path


def fake_recycle(monkeypatch: pytest.MonkeyPatch, trash_root: Path) -> None:
    trash_root.mkdir(parents=True, exist_ok=True)

    def move(path: str) -> None:
        source = Path(path)
        destination = trash_root / source.name
        if destination.exists():
            destination = trash_root / f"{source.stem}-2{source.suffix}"
        source.replace(destination)

    monkeypatch.setattr(send2trash, "send2trash", move)


def saved_item(service: FeedbackService, project_root: Path, round_number: int, name: str):
    return next(
        item
        for item in service.saved_items(project_root, round_number)
        if item.name == name
    )


def test_recycle_moves_file_to_trash_keeps_round_and_returns_remaining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_service, _group, project_root = create_project(tmp_path)
    first = add_feedback(project_root, 1, "第一条.txt", "first")
    second = add_feedback(project_root, 1, "第二条.txt", "second")
    service = FeedbackService()
    item = saved_item(service, project_root, 1, first.name)
    fake_recycle(monkeypatch, tmp_path / "模拟回收站")

    result = service.recycle_saved_item(
        project_root,
        1,
        first,
        item.fingerprint,
    )

    assert not first.exists()
    assert (tmp_path / "模拟回收站" / first.name).read_text(encoding="utf-8") == "first"
    assert first.parent.is_dir()
    assert [remaining.name for remaining in result.remaining_items] == [second.name]
    assert not list(first.parent.glob(".*.recycle-backup-*"))


def test_recycle_rejects_escape_symlink_and_changed_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_service, _group, project_root = create_project(tmp_path)
    target = add_feedback(project_root, 1, "反馈.txt", "original")
    service = FeedbackService()
    original_item = saved_item(service, project_root, 1, target.name)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(FeedbackRecycleError, match="不在当前项目"):
        service.recycle_saved_item(
            project_root,
            1,
            outside,
            service._file_sha256(outside),
        )

    target.write_text("changed", encoding="utf-8")
    with pytest.raises(FeedbackRecycleError, match="替换或修改"):
        service.recycle_saved_item(
            project_root,
            1,
            target,
            original_item.fingerprint,
        )
    assert target.read_text(encoding="utf-8") == "changed"
    assert not list(target.parent.glob(".*.recycle-backup-*"))

    real = tmp_path / "real.txt"
    real.write_text("real", encoding="utf-8")
    link = target.parent / "link.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")
    with pytest.raises(FeedbackRecycleError, match="符号链接"):
        service.recycle_saved_item(
            project_root,
            1,
            link,
            service._file_sha256(real),
        )


def test_recycle_failure_preserves_original_and_wraps_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_service, _group, project_root = create_project(tmp_path)
    target = add_feedback(project_root, 1, "保留.txt", "keep")
    service = FeedbackService()
    item = saved_item(service, project_root, 1, target.name)

    def denied(_path: str) -> None:
        raise OSError("denied")

    monkeypatch.setattr(send2trash, "send2trash", denied)
    with pytest.raises(FeedbackRecycleError, match="原文件已保留"):
        service.recycle_saved_item(
            project_root,
            1,
            target,
            item.fingerprint,
        )

    assert target.read_text(encoding="utf-8") == "keep"
    assert not list(target.parent.glob(".*.recycle-backup-*"))


def test_system_batch_note_is_marked_not_deletable_and_cannot_support_task(
    app: QApplication, tmp_path: Path
) -> None:
    _project_service, _group, project_root = create_project(tmp_path)
    note = add_feedback(
        project_root,
        1,
        "批量反馈说明-20260802-120000-abcdef12.txt",
        "batch boundary",
    )
    service = FeedbackService()
    item = saved_item(service, project_root, 1, note.name)

    assert item.system_managed
    assert item.status == "系统批量说明"
    row = PendingFeedbackRow(item, read_only=True, allow_saved_delete=True)
    assert row.status_label.text() == "系统批量说明"
    assert "系统批量说明 · 系统批量说明" not in row.status_label.text()
    assert "不作为普通反馈材料删除" in row.status_label.toolTip()
    assert row.remove_button is None
    with pytest.raises(FeedbackRecycleError, match="不能作为普通材料删除"):
        service.recycle_saved_item(
            project_root,
            1,
            note,
            item.fingerprint,
        )
    with pytest.raises(ValueError, match="没有任何有效材料"):
        TaskService(RESOURCE_ROOT).generate_feedback_task(project_root, 1, "")


def test_similar_user_filename_is_not_misclassified_as_system_note(
    tmp_path: Path
) -> None:
    _project_service, _group, project_root = create_project(tmp_path)
    path = add_feedback(
        project_root,
        1,
        "批量反馈说明-普通反馈.txt",
        "ordinary feedback",
    )
    service = FeedbackService()
    item = saved_item(service, project_root, 1, path.name)

    assert not item.system_managed
    assert item.status == "已保存"
    TaskService(RESOURCE_ROOT).generate_feedback_task(project_root, 1, "")


def test_deleting_other_round_does_not_invalidate_bound_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_service, _group, project_root = create_project(tmp_path)
    first = add_feedback(project_root, 1, "第一轮.txt", "one")
    second = add_feedback(project_root, 2, "第二轮.txt", "two")
    task_service = TaskService(RESOURCE_ROOT)
    task_service.generate_feedback_task(project_root, 1, "")
    feedback_service = FeedbackService()
    fake_recycle(monkeypatch, tmp_path / "模拟回收站")

    second_item = saved_item(feedback_service, project_root, 2, second.name)
    feedback_service.recycle_saved_item(
        project_root,
        2,
        second,
        second_item.fingerprint,
    )
    assert task_service.validate_current_task(project_root).valid

    first_item = saved_item(feedback_service, project_root, 1, first.name)
    feedback_service.recycle_saved_item(
        project_root,
        1,
        first,
        first_item.fingerprint,
    )
    validation = task_service.validate_current_task(project_root)
    assert not validation.valid
    assert "反馈轮次已无有效材料" in validation.reason


def test_home_saved_row_cancel_then_recycle_expires_task_and_disables_generation(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_service, group, project_root = create_project(tmp_path)
    feedback_path = add_feedback(project_root, 1, "误存材料.txt", "remove me")
    settings = SettingsService(
        QSettings(str(tmp_path / "recycle-ui.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(
        project_service,
        TaskService(RESOURCE_ROOT),
        settings,
    )
    window.load_project_group(group.root)
    home = window.home_page
    home._refresh_project_state(auto_task_type=True)
    home._set_task_type(TaskType.FEEDBACK_MODIFICATION)
    home._generate_task()
    assert home.current_task_validation.valid
    assert len(home.saved_feedback) == 1
    saved_row = home.saved_layout.itemAt(0).widget()
    assert isinstance(saved_row, PendingFeedbackRow)
    assert saved_row.remove_button is not None

    item_id = home.saved_feedback[0].item_id
    monkeypatch.setattr(home, "_confirm_recycle_saved_item", lambda *_args: False)
    home._remove_saved_feedback(item_id)
    assert feedback_path.is_file()
    assert home.current_task_validation.valid

    fake_recycle(monkeypatch, tmp_path / "模拟回收站")
    monkeypatch.setattr(home, "_confirm_recycle_saved_item", lambda *_args: True)
    toasts: list[str] = []
    errors: list[str] = []
    home.toast_requested.connect(toasts.append)
    home.error_requested.connect(errors.append)
    home._remove_saved_feedback(item_id)
    app.processEvents()

    assert not errors
    assert toasts and "移入系统回收站" in toasts[-1]
    assert not feedback_path.exists()
    assert feedback_path.parent.is_dir()
    assert home.saved_feedback == []
    assert home.selected_round_hint.text() == "该轮当前没有有效反馈材料"
    assert not home.feedback_execute_button.isEnabled()
    assert not home.generate_button.isEnabled()
    assert home.generate_button.text() == "重新生成第1轮反馈修改任务"
    assert "任务已过期：反馈材料已删除或变化" in home.task_status_text.text()
    window.close()
    app.processEvents()
