import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings


class SettingsService:
    RECENT_GROUP_KEY = "projects/recent_group_path"
    LAST_SELECTED_PROJECTS_KEY = "projects/last_selected_by_group"

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

    def last_selected_project(self, group_path: Path) -> str | None:
        selections = self._last_selected_projects()
        value = selections.get(self._group_key(group_path), "").strip()
        return value or None

    def save_last_selected_project(self, group_path: Path, project_name: str) -> None:
        name = project_name.strip()
        if not name:
            return
        selections = self._last_selected_projects()
        selections[self._group_key(group_path)] = name
        self.settings.setValue(
            self.LAST_SELECTED_PROJECTS_KEY,
            json.dumps(selections, ensure_ascii=False, sort_keys=True),
        )
        self.settings.sync()

    def _last_selected_projects(self) -> dict[str, str]:
        raw = self.settings.value(self.LAST_SELECTED_PROJECTS_KEY, "", type=str).strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    @staticmethod
    def _group_key(group_path: Path) -> str:
        return os.path.normcase(str(Path(group_path).expanduser().resolve()))
