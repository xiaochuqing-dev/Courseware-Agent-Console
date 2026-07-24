from pathlib import Path

from PySide6.QtCore import QSettings


class SettingsService:
    RECENT_GROUP_KEY = "projects/recent_group_path"

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("CoursewareTools", "CoursewareAgentConsole")

    def recent_group_path(self) -> Path | None:
        value = self.settings.value(self.RECENT_GROUP_KEY, "", type=str).strip()
        return Path(value) if value else None

    def save_recent_group_path(self, path: Path) -> None:
        self.settings.setValue(self.RECENT_GROUP_KEY, str(Path(path).resolve()))
        self.settings.sync()

    def clear_recent_group_path(self) -> None:
        self.settings.remove(self.RECENT_GROUP_KEY)
        self.settings.sync()

