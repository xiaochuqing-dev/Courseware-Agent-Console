import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings


class SettingsService:
    RECENT_GROUP_KEY = "projects/recent_group_path"
    REGISTERED_GROUPS_KEY = "projects/registered_group_paths"
    LAST_SELECTED_PROJECTS_KEY = "projects/last_selected_by_group"

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("CoursewareTools", "CoursewareAgentConsole")

    def recent_group_path(self) -> Path | None:
        value = self.settings.value(self.RECENT_GROUP_KEY, "", type=str).strip()
        return Path(value) if value else None

    def save_recent_group_path(self, path: Path) -> None:
        resolved = Path(path).resolve()
        self.settings.setValue(self.RECENT_GROUP_KEY, str(resolved))
        self.register_project_group(resolved, sync=False)
        self.settings.sync()

    def clear_recent_group_path(self) -> None:
        self.settings.remove(self.RECENT_GROUP_KEY)
        self.settings.sync()

    def registered_group_paths(self) -> tuple[Path, ...]:
        raw = self.settings.value(self.REGISTERED_GROUPS_KEY, "", type=str).strip()
        values: list[str] = []
        if raw:
            try:
                decoded = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                decoded = []
            if isinstance(decoded, list):
                values.extend(str(value) for value in decoded if isinstance(value, str))
        recent = self.recent_group_path()
        if recent:
            values.append(str(recent))
        result: list[Path] = []
        seen: set[str] = set()
        for value in values:
            path = Path(value).expanduser().resolve()
            key = self._group_key(path)
            if key not in seen:
                seen.add(key)
                result.append(path)
        return tuple(result)

    def register_project_group(self, path: Path, sync: bool = True) -> None:
        resolved = Path(path).expanduser().resolve()
        groups = list(self.registered_group_paths())
        key = self._group_key(resolved)
        if all(self._group_key(group) != key for group in groups):
            groups.append(resolved)
        self.settings.setValue(
            self.REGISTERED_GROUPS_KEY,
            json.dumps([str(group) for group in groups], ensure_ascii=False),
        )
        if sync:
            self.settings.sync()

    def remove_project_group(self, path: Path) -> None:
        key = self._group_key(path)
        groups = [
            group
            for group in self.registered_group_paths()
            if self._group_key(group) != key
        ]
        self.settings.setValue(
            self.REGISTERED_GROUPS_KEY,
            json.dumps([str(group) for group in groups], ensure_ascii=False),
        )
        selections = self._last_selected_projects()
        selections.pop(key, None)
        self.settings.setValue(
            self.LAST_SELECTED_PROJECTS_KEY,
            json.dumps(selections, ensure_ascii=False, sort_keys=True),
        )
        recent = self.recent_group_path()
        if recent and self._group_key(recent) == key:
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
