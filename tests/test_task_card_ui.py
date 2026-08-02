import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from services import FeedbackService, ProjectService, SettingsService, TaskService
from ui.main_window import MainWindow
from ui.widgets import PromptDialog
from tests.helpers import create_valid_product, tool_binding


def create_window(tmp_path: Path) -> tuple[QApplication, MainWindow, Path]:
    app = QApplication.instance() or QApplication([])
    resource_root = Path(__file__).resolve().parents[1] / "resources"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"title": "任务卡测试"}), encoding="utf-8")
    project_service = ProjectService(resource_root)
    group = project_service.create_project_group(
        "任务卡项目组",
        1,
        tmp_path,
        [source],
        tool_binding(resource_root),
    )
    create_valid_product(project_service, group.projects[0])
    settings = SettingsService(
        QSettings(str(tmp_path / "task-card.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(project_service, TaskService(resource_root), settings)
    window.load_project_group(group.root)
    return app, window, group.projects[0].path


def test_task_card_uses_compact_actions_and_separate_preview(
    tmp_path: Path, monkeypatch
) -> None:
    app, window, project_root = create_window(tmp_path)
    home = window.home_page

    assert not hasattr(home, "task_preview")
    assert not hasattr(home, "copy_prompt_button")
    assert home.requirements_input.maximumHeight() == 76
    assert home.requirements_input.maximumWidth() == 760
    assert home.generate_button.text() == "生成首次制作任务"
    assert not home.task_preview_button.isEnabled()
    assert home.acceptance_button.isEnabled()
    assert home.record_button.isEnabled()

    window.resize(1240, 790)
    window.show()
    app.processEvents()
    text_right = home.latest_product_label.mapTo(
        home.task_card, home.latest_product_label.contentsRect().topRight()
    ).x()
    assert text_right < home.task_card.width()

    home.requirements_input.setPlainText("先使用第一版补充要求")
    home._generate_task()
    first_content = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "先使用第一版补充要求" in first_content
    assert home.generate_button.text() == "重新生成首次制作任务"
    assert home.task_status_text.text() == "已有首次制作任务"
    assert home.task_preview_button.isEnabled()

    monkeypatch.setattr(PromptDialog, "exec", lambda self: 0)
    home._show_task_preview()
    dialog = home._prompt_dialog
    assert dialog is not None
    assert dialog.editor.toPlainText() == first_content
    assert dialog.copy_button.text() == "复制执行指令"
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text() == (
        f"请读取并完整执行以下任务文件：\n{project_root / '当前任务.md'}"
    )

    home.requirements_input.setPlainText("改用第二版补充要求")
    assert "特殊要求已变化" in home.task_status_text.text()
    home._show_task_preview()
    stale_dialog = home._prompt_dialog
    assert stale_dialog is not None
    assert not stale_dialog.copy_button.isEnabled()
    home._generate_task()
    regenerated = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "改用第二版补充要求" in regenerated
    assert "先使用第一版补充要求" not in regenerated
    assert home.generate_button.text() == "重新生成首次制作任务"

    window.close()
    app.processEvents()


def test_feedback_task_switches_from_generate_to_regenerate(tmp_path: Path) -> None:
    app, window, project_root = create_window(tmp_path)
    home = window.home_page

    feedback_round = project_root / "客户反馈" / "第1轮"
    feedback_round.mkdir()
    (feedback_round / "反馈.txt").write_text("调整字号", encoding="utf-8")
    home.refresh_current_project()
    home.feedback_task_button.click()

    assert home.generate_button.text() == "生成第1轮反馈修改任务"
    assert home.generate_button.isEnabled()
    home.requirements_input.setPlainText("保留当前配色")
    home._generate_task()
    first_feedback_task = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "任务类型：反馈修改" in first_feedback_task
    assert "反馈轮次：第1轮" in first_feedback_task
    assert "保留当前配色" in first_feedback_task
    assert home.generate_button.text() == "重新生成第1轮反馈修改任务"
    assert home.task_status_text.text() == "已有第1轮反馈修改任务"

    home.requirements_input.setPlainText("保留配色并放慢动画")
    home._generate_task()
    regenerated = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "保留配色并放慢动画" in regenerated
    assert "保留当前配色" not in regenerated
    assert home.generate_button.text() == "重新生成第1轮反馈修改任务"

    window.close()
    app.processEvents()


def test_round_dropdown_really_switches_materials_and_task_binding(tmp_path: Path) -> None:
    app, window, project_root = create_window(tmp_path)
    home = window.home_page
    for number, name in ((1, "第一轮.txt"), (2, "第二轮.txt")):
        round_root = project_root / "客户反馈" / f"第{number}轮"
        round_root.mkdir()
        (round_root / name).write_text(name, encoding="utf-8")
    home._refresh_project_state(auto_task_type=True)
    window.resize(1100, 720)
    window.show()
    app.processEvents()

    assert home.feedback_round_combo.count() == 2
    assert home.feedback_round_combo.currentData() == 2
    assert {item.name for item in home.saved_feedback} == {"第二轮.txt"}

    home.feedback_round_combo.showPopup()
    view = home.feedback_round_combo.view()
    for _ in range(20):
        app.processEvents()
        if view.isVisible():
            break
        QTest.qWait(20)
    assert view.isVisible()
    first_index = home.feedback_round_combo.model().index(0, 0)
    QTest.mouseClick(
        view.viewport(),
        Qt.MouseButton.LeftButton,
        pos=view.visualRect(first_index).center(),
    )
    app.processEvents()
    assert home.feedback_round_combo.currentData() == 1
    assert {item.name for item in home.saved_feedback} == {"第一轮.txt"}
    assert home.append_round_button.text() == "追加到第1轮"

    home._generate_task()
    content = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "反馈轮次：第1轮" in content
    assert "第一轮.txt" in content
    assert "第二轮.txt" not in content
    window.close()
    app.processEvents()


def test_first_build_mode_warns_before_overwriting_feedback_task(
    tmp_path: Path, monkeypatch
) -> None:
    app, window, project_root = create_window(tmp_path)
    home = window.home_page
    round_root = project_root / "客户反馈" / "第1轮"
    round_root.mkdir()
    (round_root / "反馈.txt").write_text("调整字号", encoding="utf-8")
    home._refresh_project_state(auto_task_type=True)
    home._generate_task()
    original = (project_root / "当前任务.md").read_text(encoding="utf-8")

    home.first_build_button.click()
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    home._generate_task()
    assert (project_root / "当前任务.md").read_text(encoding="utf-8") == original
    assert "当前选择为首次制作" in home.task_status_text.text()
    window.close()
    app.processEvents()


def test_many_feedback_rounds_do_not_expand_page_height(tmp_path: Path) -> None:
    app, window, project_root = create_window(tmp_path)
    home = window.home_page
    for number in range(1, 3):
        round_root = project_root / "客户反馈" / f"第{number}轮"
        round_root.mkdir()
        (round_root / f"反馈-{number}.txt").write_text("反馈", encoding="utf-8")
    home._refresh_project_state(auto_task_type=True)
    app.processEvents()
    two_round_height = home.feedback_card.sizeHint().height()

    for number in range(3, 13):
        round_root = project_root / "客户反馈" / f"第{number}轮"
        round_root.mkdir()
        (round_root / f"反馈-{number}.txt").write_text("反馈", encoding="utf-8")
    home._refresh_project_state(auto_task_type=True)
    app.processEvents()

    assert home.feedback_round_combo.count() == 12
    assert home.saved_scroll.maximumHeight() == 172
    assert home.feedback_card.sizeHint().height() <= two_round_height + 20
    window.close()
    app.processEvents()


def test_save_new_round_and_append_keep_one_round_state_and_expire_task(
    tmp_path: Path,
) -> None:
    app, window, project_root = create_window(tmp_path)
    home = window.home_page
    feedback = FeedbackService()
    home._append_pending(feedback.pending_from_text("第一轮反馈"))
    QTest.mouseClick(home.new_round_button, Qt.MouseButton.LeftButton)
    app.processEvents()

    assert home.feedback_task_button.isChecked()
    assert home.feedback_round_combo.currentData() == 1
    assert home.feedback_round_combo.count() == 1
    assert home.generate_button.text() == "生成第1轮反馈修改任务"
    home._generate_task()
    assert home.current_task_validation.valid

    home._append_pending(feedback.pending_from_text("第一轮追加"))
    QTest.mouseClick(home.append_round_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert home.feedback_round_combo.currentData() == 1
    assert len(home.saved_feedback) == 2
    assert not home.current_task_validation.valid
    assert "反馈轮次材料已变化" in home.task_status_text.text()

    home._append_pending(feedback.pending_from_text("第二轮反馈"))
    QTest.mouseClick(home.new_round_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert home.feedback_round_combo.count() == 2
    assert home.feedback_round_combo.currentData() == 2
    home.feedback_round_combo.setCurrentIndex(0)
    app.processEvents()
    assert home.feedback_round_combo.currentData() == 1
    assert {item.preview for item in home.saved_feedback} == {
        "第一轮反馈",
        "第一轮追加",
    }
    window.close()
    app.processEvents()
