from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from services.resource_paths import bundled_resource_root


def create_application(argv: list[str] | None = None) -> QApplication:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("课件 Agent 控制台")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("CoursewareTools")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    stylesheet = bundle_root / "ui" / "styles" / "app.qss"
    app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))
    icon = bundled_resource_root() / "app.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="课件 Agent 控制台")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="启动后自动退出，用于无交互启动检查",
    )
    args, qt_args = parser.parse_known_args()
    app = create_application([sys.argv[0], *qt_args])
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(250, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
