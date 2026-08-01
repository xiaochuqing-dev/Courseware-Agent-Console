import json
import time
from pathlib import Path
from zipfile import ZipFile

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import FeedbackService, ProjectService, SettingsService, TaskService
from tests.helpers import create_valid_product, tool_binding
from ui.main_window import MainWindow
from ui.widgets.batch_feedback_panel import HighContrastCheckBox


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
    for project in group.projects:
        create_valid_product(project_service, project)
    settings = SettingsService(
        QSettings(str(tmp_path / "batch-ui.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(project_service, TaskService(RESOURCE_ROOT), settings)
    window.load_project_group(group.root)
    return app, window, group


def open_batch_page(app: QApplication, window: MainWindow):
    QTest.mouseClick(window.home_page.batch_feedback_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.page_stack.currentWidget() is window.batch_feedback_page
    return window.batch_feedback_page.panel


def select_rows(app: QApplication, panel, rows: tuple[int, ...]) -> None:
    for row in rows:
        checkbox = panel.project_table.cellWidget(row, 0)
        assert isinstance(checkbox, HighContrastCheckBox)
        QTest.mouseClick(
            checkbox,
            Qt.MouseButton.LeftButton,
            pos=checkbox.rect().center(),
        )
        app.processEvents()
        assert checkbox.isChecked()


def test_independent_batch_page_and_real_checkbox_clicks_preview_rounds(
    tmp_path: Path,
) -> None:
    app, window, group = create_window(tmp_path)
    (group.projects[0].path / "客户反馈" / "第1轮").mkdir()
    (group.projects[0].path / "客户反馈" / "第1轮" / "旧反馈.txt").write_text(
        "old", encoding="utf-8"
    )
    window.home_page.refresh_group()
    window.home_page.project_list.setCurrentRow(2)
    panel = open_batch_page(app, window)
    select_rows(app, panel, (0, 1))
    QTest.mouseClick(panel.select_none_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not panel.selected_project_ids
    QTest.mouseClick(panel.select_all_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert len(panel.selected_project_ids) == 3
    third_checkbox = panel.project_table.cellWidget(2, 0)
    QTest.mouseClick(
        third_checkbox,
        Qt.MouseButton.LeftButton,
        pos=third_checkbox.rect().center(),
    )
    app.processEvents()

    assert panel.selected_count_label.text() == "已选择 2 个课件"
    assert panel.project_table.item(0, 3).text() == "第2轮"
    assert panel.project_table.item(1, 3).text() == "第1轮"
    assert panel.project_table.cellWidget(0, 4).isEnabled()
    assert not panel.project_table.cellWidget(2, 4).isEnabled()
    assert not hasattr(panel, "strategy_combo")
    assert "独立创建各自下一轮" in panel.round_rule_label.text()
    assert window.home_page.current_project.project_id == group.projects[2].project_id
    window.close()
    app.processEvents()


def test_batch_and_single_pending_state_do_not_pollute_each_other(tmp_path: Path) -> None:
    app, window, _group = create_window(tmp_path)
    panel = open_batch_page(app, window)
    select_rows(app, panel, (0, 1))
    panel._append_pending(FeedbackService().pending_from_text("批量文字反馈"))
    panel.project_table.cellWidget(0, 4).setText("对应第一部分")

    QTest.mouseClick(
        window.batch_feedback_page.back_button,
        Qt.MouseButton.LeftButton,
    )
    home = window.home_page
    home._append_pending(FeedbackService().pending_from_text("单项目文字反馈"))
    assert len(home.pending_feedback) == 1
    assert len(panel.pending_feedback) == 1
    home.project_list.setCurrentRow(1)
    assert not home.pending_feedback
    assert len(panel.pending_feedback) == 1

    restored = open_batch_page(app, window)
    assert restored is panel
    assert panel.selected_count_label.text() == "已选择 2 个课件"
    assert panel.pending_feedback[0].preview == "批量文字反馈"
    assert "对应第一部分" in panel.project_hints.values()
    window.close()
    app.processEvents()


def test_batch_word_save_task_generation_copy_and_project_jump(tmp_path: Path) -> None:
    app, window, group = create_window(tmp_path)
    panel = open_batch_page(app, window)
    errors: list[str] = []
    panel.error_requested.connect(errors.append)
    select_rows(app, panel, (0, 1, 2))
    panel.project_table.cellWidget(0, 4).setText("Word 第一部分")
    panel.project_table.cellWidget(1, 4).setText("Word 第二部分")
    panel.batch_note_input.setPlainText("Word 按课件分为三个部分")
    word = write_docx(tmp_path / "客户统一反馈.docx")
    panel.add_files([word])

    assert panel.save_button.isEnabled()
    QTest.mouseClick(panel.save_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: not panel._operation_in_progress)
    assert not errors
    assert panel.saved_result is not None
    assert len(panel.result_project_buttons) == 3
    for project in group.projects:
        assert (project.path / "客户反馈" / "第1轮" / word.name).is_file()

    QTest.mouseClick(panel.generate_tasks_button, Qt.MouseButton.LeftButton)
    wait_until(app, lambda: not panel._operation_in_progress)
    assert not errors
    assert panel.task_result is not None
    assert panel.copy_instruction_button.isEnabled()
    QTest.mouseClick(panel.copy_instruction_button, Qt.MouseButton.LeftButton)
    clipboard = QGuiApplication.clipboard().text()
    assert clipboard.startswith("请读取并完整执行以下批量任务文件：")
    assert str(panel.task_result.batch_task_path) in clipboard

    first_project_id = group.projects[0].project_id
    QTest.mouseClick(
        panel.result_project_buttons[first_project_id],
        Qt.MouseButton.LeftButton,
    )
    app.processEvents()
    home = window.home_page
    assert window.page_stack.currentWidget() is home
    assert home.current_project.project_id == first_project_id
    assert home.feedback_task_button.isChecked()
    assert home.feedback_round_combo.currentData() == 1
    assert any(item.name == word.name for item in home.saved_feedback)
    window.close()
    app.processEvents()


def test_batch_same_name_error_and_common_width_has_no_horizontal_scroll(
    tmp_path: Path,
) -> None:
    app, window, _group = create_window(tmp_path)
    panel = open_batch_page(app, window)
    errors: list[str] = []
    window.batch_feedback_page.error_requested.disconnect()
    panel.error_requested.connect(errors.append)
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
