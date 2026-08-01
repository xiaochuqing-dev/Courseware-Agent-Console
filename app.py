from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QTemporaryDir, QTimer, Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from services import APP_VERSION, SettingsService, SingleInstanceController
from services.app_logging import LOGGER_NAME, configure_logging
from services.resource_paths import bundled_resource_root


logger = logging.getLogger(LOGGER_NAME)


def create_application(argv: list[str] | None = None) -> QApplication:
    log_path = configure_logging()
    logger.info("Application startup; log=%s", log_path)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("课件 Agent 控制台")
    app.setApplicationVersion(APP_VERSION)
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
    parser.add_argument(
        "--allow-multiple-instances",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args, qt_args = parser.parse_known_args()
    app = create_application([sys.argv[0], *qt_args])
    instance: SingleInstanceController | None = None
    if not args.smoke_test and not args.allow_multiple_instances:
        channel = "prod" if getattr(sys, "frozen", False) else "dev"
        instance = SingleInstanceController(
            f"CoursewareAgentConsole.SingleInstance.v1.{channel}", app
        )
        if not instance.acquire():
            logger.info("Existing application instance activated")
            return 0
    smoke_temp: QTemporaryDir | None = None
    smoke_settings: SettingsService | None = None
    if args.smoke_test:
        smoke_temp = QTemporaryDir()
        smoke_settings = SettingsService(
            QSettings(
                str(Path(smoke_temp.path()) / "smoke.ini"),
                QSettings.Format.IniFormat,
            )
        )
    window = MainWindow(settings_service=smoke_settings)
    if instance is not None:
        instance.activation_requested.connect(lambda: activate_window(window))
        app.aboutToQuit.connect(instance.release)
    window.show()
    if args.smoke_test:
        QTimer.singleShot(250, app.quit)
    return app.exec()


def activate_window(window: MainWindow) -> None:
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()


if __name__ == "__main__":
    raise SystemExit(main())
