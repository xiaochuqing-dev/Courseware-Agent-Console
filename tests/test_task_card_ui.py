import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from services import ProjectService, SettingsService, TaskService
from ui.main_window import MainWindow
from ui.widgets import PromptDialog
from tests.helpers import tool_binding


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
    assert home.generate_button.text() == "生成当前任务"
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
    assert home.generate_button.text() == "重新生成任务"
    assert home.task_status_text.text() == "已有任务：首次制作"
    assert home.task_preview_button.isEnabled()

    monkeypatch.setattr(PromptDialog, "exec", lambda self: 0)
    home._show_task_preview()
    dialog = home._prompt_dialog
    assert dialog is not None
    assert dialog.editor.toPlainText() == first_content
    assert dialog.copy_button.text() == "复制提示词（可选）"
    dialog.copy_button.click()
    assert QGuiApplication.clipboard().text() == "执行source当前任务。"

    home.requirements_input.setPlainText("改用第二版补充要求")
    home._generate_task()
    regenerated = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "改用第二版补充要求" in regenerated
    assert "先使用第一版补充要求" not in regenerated
    assert home.generate_button.text() == "重新生成任务"

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

    assert home.generate_button.text() == "生成反馈修改任务"
    assert home.generate_button.isEnabled()
    home.requirements_input.setPlainText("保留当前配色")
    home._generate_task()
    first_feedback_task = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "任务类型：反馈修改" in first_feedback_task
    assert "反馈轮次：第1轮" in first_feedback_task
    assert "保留当前配色" in first_feedback_task
    assert home.generate_button.text() == "重新生成任务"
    assert home.task_status_text.text() == "已有任务：反馈修改 · 第1轮"

    home.requirements_input.setPlainText("保留配色并放慢动画")
    home._generate_task()
    regenerated = (project_root / "当前任务.md").read_text(encoding="utf-8")
    assert "保留配色并放慢动画" in regenerated
    assert "保留当前配色" not in regenerated
    assert home.generate_button.text() == "重新生成任务"

    window.close()
    app.processEvents()
