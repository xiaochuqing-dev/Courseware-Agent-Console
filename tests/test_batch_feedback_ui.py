import json
import time
from pathlib import Path
from zipfile import ZipFile

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from services import FeedbackService, ProjectService, SettingsService, TaskService
from tests.helpers import tool_binding
from ui.main_window import MainWindow


RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources"


def wait_until(app: QApplication, predicate, timeout_ms: int = 10000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def write_docx(path: Path) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document><body>反馈</body></document>")
    return path


def create_window(tmp_path: Path, count: int = 3):
    app = QApplication.instance() or QApplication([])
    sources = []
    for index in range(count):
        source = tmp_path / f"ui-source-{index}.json"
        source.write_text(json.dumps({"title": f"课件 {index}"}), encoding="utf-8")
        sources.append(source)
    project_service = ProjectService(RESOURCE_ROOT)
    group = project_service.create_project_group(
        "批量反馈界面",
        count,
        tmp_path,
        sources,
        tool_binding(RESOURCE_ROOT),
        project_names=[f"课件 {index + 1}" for index in range(count)],
    )
    settings = SettingsService(
        QSettings(str(tmp_path / "batch-ui.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(project_service, TaskService(RESOURCE_ROOT), settings)
    window.load_project_group(group.root)
    return app, window, group


def select_rows(panel, rows: tuple[int, ...]) -> None:
    for row in rows:
        panel.project_table.item(row, 0).setCheckState(Qt.CheckState.Checked)


def test_batch_mode_defaults_to_single_and_previews_independent_rounds(
    tmp_path: Path,
) -> None:
    app, window, group = create_window(tmp_path)
    (group.projects[0].path / "客户反馈" / "第1轮").mkdir()
    (group.projects[0].path / "客户反馈" / "第1轮" / "旧反馈.txt").write_text(
        "old", encoding="utf-8"
    )
    window.home_page.refresh_group()
    home = window.home_page
    panel = home.batch_feedback_panel

    assert home.feedback_mode_stack.currentIndex() == 0
    assert home.single_feedback_mode_button.isChecked()
    home.batch_feedback_mode_button.click()
    select_rows(panel, (0, 1))

    assert panel.selected_count_label.text() == "已选择 2 个课件"
    assert panel.project_table.item(0, 2).text() == "新建第2轮"
    assert panel.project_table.item(1, 2).text() == "新建第1轮"
    assert not panel.strategy_combo.model().item(1).isEnabled()
    assert "尚无反馈轮次" in panel.append_unavailable_label.text()
    window.close()
    app.processEvents()


def test_batch_and_single_pending_state_do_not_pollute_each_other(tmp_path: Path) -> None:
    app, window, _group = create_window(tmp_path)
    home = window.home_page
    panel = home.batch_feedback_panel
    home.batch_feedback_mode_button.click()
    select_rows(panel, (0, 1))
    panel._append_pending(FeedbackService().pending_from_text("批量文字反馈"))
    hint = panel.project_table.cellWidget(0, 3)
    hint.setText("对应第一部分")

    home.single_feedback_mode_button.click()
    home._append_pending(FeedbackService().pending_from_text("单项目文字反馈"))
    assert len(home.pending_feedback) == 1
    assert len(panel.pending_feedback) == 1
    home.project_list.setCurrentRow(1)
    assert not home.pending_feedback
    assert len(panel.pending_feedback) == 1
    assert any(value == "对应第一部分" for value in panel.project_hints.values())
    home.batch_feedback_mode_button.click()
    assert panel.selected_count_label.text() == "已选择 2 个课件"
    assert panel.pending_feedback[0].preview == "批量文字反馈"
    window.close()
    app.processEvents()


def test_batch_word_save_task_generation_and_copy_instruction(tmp_path: Path) -> None:
    app, window, group = create_window(tmp_path)
    home = window.home_page
    panel = home.batch_feedback_panel
    errors: list[str] = []
    home.error_requested.disconnect()
    home.error_requested.connect(errors.append)
    home.batch_feedback_mode_button.click()
    select_rows(panel, (0, 1, 2))
    panel.project_table.cellWidget(0, 3).setText("Word 第一部分")
    panel.project_table.cellWidget(1, 3).setText("Word 第二部分")
    panel.batch_note_input.setPlainText("Word 按课件分为三个部分")
    word = write_docx(tmp_path / "客户统一反馈.docx")
    panel.add_files([word])

    assert panel.save_button.isEnabled()
    panel.save_button.click()
    wait_until(app, lambda: not panel._operation_in_progress)
    assert not errors
    assert panel.saved_result is not None
    assert "批量反馈已保存" in panel.result_label.text()
    assert "第1轮" in panel.result_label.text()
    for project in group.projects:
        assert (project.path / "客户反馈" / "第1轮" / word.name).is_file()

    panel.generate_tasks_button.click()
    wait_until(app, lambda: not panel._operation_in_progress)
    assert not errors
    assert panel.task_result is not None
    assert "3 个项目反馈任务已生成" in panel.result_label.text()
    assert panel.copy_instruction_button.isEnabled()
    panel.copy_instruction_button.click()
    clipboard = QGuiApplication.clipboard().text()
    assert clipboard.startswith("请读取并完整执行以下批量任务文件：")
    assert str(panel.task_result.batch_task_path) in clipboard
    window.close()
    app.processEvents()


def test_batch_same_name_error_and_common_width_has_no_horizontal_scroll(
    tmp_path: Path,
) -> None:
    app, window, _group = create_window(tmp_path)
    home = window.home_page
    panel = home.batch_feedback_panel
    errors: list[str] = []
    home.error_requested.disconnect()
    panel.error_requested.connect(errors.append)
    home.batch_feedback_mode_button.click()
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "反馈.txt").write_text("left", encoding="utf-8")
    (right / "反馈.txt").write_text("right", encoding="utf-8")
    panel.add_files([left / "反馈.txt", right / "反馈.txt"])
    assert len(panel.pending_feedback) == 1
    assert errors and "不同来源的同名材料" in errors[-1]

    window.resize(1120, 760)
    window.show()
    app.processEvents()
    assert panel.project_table.horizontalScrollBar().maximum() == 0
    assert panel.copy_instruction_button.text() == "复制批量执行指令"
    window.close()
    app.processEvents()
