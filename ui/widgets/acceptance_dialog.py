from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services import AcceptanceReport


class AcceptanceDialog(QDialog):
    rerun_requested = Signal()

    def __init__(
        self,
        report: AcceptanceReport,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("完整产品验收")
        self.resize(820, 640)
        self.setMinimumSize(680, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("完整产品验收结果")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.summary = QLabel()
        self.summary.setObjectName("acceptanceSummary")
        self.summary.setProperty("status", "passed" if report.passed else "failed")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.result_list = QListWidget()
        self.result_list.setObjectName("acceptanceResultList")
        self.result_list.currentItemChanged.connect(self._show_current_detail)
        layout.addWidget(self.result_list, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(130)
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        open_report = QPushButton("打开项目记录")
        open_report.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(report.markdown_path)))
        )
        actions.addWidget(open_report)
        self.open_path_button = QPushButton("定位当前项")
        self.open_path_button.clicked.connect(self._open_current_path)
        actions.addWidget(self.open_path_button)
        actions.addStretch()
        rerun = QPushButton("重新执行验收")
        rerun.setProperty("role", "primary")
        rerun.clicked.connect(self.rerun_requested)
        actions.addWidget(rerun)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

        self._populate()

    def _populate(self) -> None:
        state = "通过" if self.report.passed else "未通过"
        self.summary.setText(
            f"总体状态：{state}    "
            f"通过 {self.report.passed_count} 项    "
            f"警告 {self.report.warning_count} 项    "
            f"失败 {self.report.failed_count} 项\n"
            f"验收时间：{self.report.checked_at}"
        )
        labels = {"passed": "通过", "warning": "警告", "failed": "失败"}
        colors = {
            "passed": QColor("#247359"),
            "warning": QColor("#9a5b32"),
            "failed": QColor("#a33f3f"),
        }
        for index, result in enumerate(self.report.items):
            item = QListWidgetItem(f"{labels[result.status]}  {result.title}")
            item.setForeground(colors[result.status])
            item.setData(256, index)
            self.result_list.addItem(item)
        if self.result_list.count():
            self.result_list.setCurrentRow(0)

    def _show_current_detail(self, current: QListWidgetItem | None) -> None:
        if current is None:
            self.detail.clear()
            self.open_path_button.setEnabled(False)
            return
        result = self.report.items[int(current.data(256))]
        lines = [result.detail]
        if result.path:
            lines.extend(["", f"路径：{result.path}"])
        if result.suggestion:
            lines.extend(["", f"建议：{result.suggestion}"])
        self.detail.setPlainText("\n".join(lines))
        self.open_path_button.setEnabled(bool(result.path))

    def _open_current_path(self) -> None:
        current = self.result_list.currentItem()
        if current is None:
            return
        result = self.report.items[int(current.data(256))]
        if not result.path:
            return
        path = Path(result.path)
        target = path if path.exists() else path.parent
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
