from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOGGER_NAME = "courseware_agent"


def configure_logging(log_dir: Path | None = None) -> Path:
    candidates = (
        [Path(log_dir)]
        if log_dir
        else [Path.cwd() / "logs", Path.home() / "CoursewareAgentConsole" / "logs"]
    )
    directory: Path | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        directory = candidate
        break
    if directory is None:
        raise OSError("无法创建应用日志目录。")
    log_path = directory / "app.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(handler, "courseware_handler", False) for handler in logger.handlers):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.courseware_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)

    def log_uncaught(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback,
    ) -> None:
        logger.critical(
            "Unhandled application exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = log_uncaught
    return log_path
