from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings


class SettingsService:
    RECENT_GROUP_KEY = "projects/recent_group_path"
    REGISTERED_GROUPS_KEY = "projects/registered_group_paths"
    STALE_GROUPS_KEY = "projects/stale_group_registrations"
    LAST_SELECTED_PROJECTS_KEY = "projects/last_selected_by_group"

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("CoursewareTools", "CoursewareAgentConsole")

    def recent_group_path(self) -> Path | None:
        value = self.settings.value(self.RECENT_GROUP_KEY, "", type=str).strip()
        return Path(value).expanduser().resolve() if value else None

    def save_recent_group_path(self, path: Path) -> None:
        resolved = Path(path).resolve()
        self.settings.setValue(self.RECENT_GROUP_KEY, str(resolved))
        self.register_project_group(resolved, sync=False)
        self.settings.sync()

    def clear_recent_group_path(self) -> None:
        self.settings.remove(self.RECENT_GROUP_KEY)
        self.settings.sync()

    def registered_group_paths(self) -> tuple[Path, ...]:
        registrations = self._registrations()
        recent = self.recent_group_path()
        if recent and all(self._group_key(Path(item["path"])) != self._group_key(recent) for item in registrations):
            registrations.append({"path": str(recent), "group_id": ""})
        result: list[Path] = []
        seen: set[str] = set()
        for item in registrations:
            path = Path(item["path"]).expanduser().resolve()
            key = self._group_key(path)
            if key not in seen:
                seen.add(key)
                result.append(path)
        return tuple(result)

    def register_project_group(self, path: Path, sync: bool = True) -> None:
        resolved = Path(path).expanduser().resolve()
        group_id = self._read_group_id(resolved)
        registrations = self._registrations()
        key = self._group_key(resolved)
        matching = next(
            (item for item in registrations if self._group_key(Path(item["path"])) == key),
            None,
        )
        if matching is None:
            registrations.append({"path": str(resolved), "group_id": group_id})
        elif group_id and not matching.get("group_id"):
            matching["group_id"] = group_id

        if group_id:
            stale = self._stale_registrations()
            relocated = next((item for item in stale if item.get("group_id") == group_id), None)
            if relocated:
                last_project = str(relocated.get("last_project", "")).strip()
                if last_project:
                    selections = self._last_selected_projects()
                    selections[self._group_key(resolved)] = last_project
                    self._save_selections(selections)
                stale.remove(relocated)
                self._save_stale(stale)

        self._save_registrations(registrations)
        if sync:
            self.settings.sync()

    def remove_project_group(self, path: Path) -> None:
        key = self._group_key(path)
        registrations = [
            item
            for item in self._registrations()
            if self._group_key(Path(item["path"])) != key
        ]
        self._save_registrations(registrations)
        selections = self._last_selected_projects()
        selections.pop(key, None)
        self._save_selections(selections)
        recent = self.recent_group_path()
        if recent and self._group_key(recent) == key:
            self.settings.remove(self.RECENT_GROUP_KEY)
        self.settings.sync()

    def prune_missing_groups(self) -> tuple[Path, ...]:
        missing: list[Path] = []
        active: list[dict[str, str]] = []
        stale = self._stale_registrations()
        selections = self._last_selected_projects()
        recent = self.recent_group_path()
        registrations = self._registrations()
        if recent and all(
            self._group_key(Path(item["path"])) != self._group_key(recent)
            for item in registrations
        ):
            registrations.append({"path": str(recent), "group_id": ""})
        for item in registrations:
            path = Path(item["path"]).expanduser().resolve()
            if path.exists():
                active.append(item)
                continue
            missing.append(path)
            key = self._group_key(path)
            stale.append(
                {
                    "path": str(path),
                    "group_id": str(item.get("group_id", "")),
                    "last_project": selections.pop(key, ""),
                }
            )
            if recent and self._group_key(recent) == key:
                self.settings.remove(self.RECENT_GROUP_KEY)
        if missing:
            unique_stale: dict[str, dict[str, str]] = {}
            for item in stale:
                unique_stale[self._group_key(Path(item["path"]))] = item
            self._save_registrations(active)
            self._save_stale(list(unique_stale.values()))
            self._save_selections(selections)
            self.settings.sync()
        return tuple(missing)

    def relocate_project_group(self, old_path: Path, new_path: Path) -> None:
        old = Path(old_path).expanduser().resolve()
        new = Path(new_path).expanduser().resolve()
        if not new.is_dir():
            raise FileNotFoundError(f"移动后的项目组目录不存在：{new}")
        new_id = self._read_group_id(new)
        if not new_id:
            raise ValueError("移动后的目录缺少有效 group_id，无法确认是同一项目组。")
        stale = self._stale_registrations()
        old_key = self._group_key(old)
        record = next(
            (item for item in stale if self._group_key(Path(item["path"])) == old_key),
            None,
        )
        if record is None:
            raise ValueError("未找到对应的失效项目组记录。")
        expected_id = str(record.get("group_id", ""))
        if expected_id and expected_id != new_id:
            raise ValueError("所选目录的 group_id 与原项目组不一致。")
        stale.remove(record)
        self._save_stale(stale)
        self.register_project_group(new, sync=False)
        last_project = str(record.get("last_project", "")).strip()
        if last_project:
            self.save_last_selected_project(new, last_project)
        self.settings.setValue(self.RECENT_GROUP_KEY, str(new))
        self.settings.sync()

    def stale_group_paths(self) -> tuple[Path, ...]:
        return tuple(Path(item["path"]) for item in self._stale_registrations())

    def last_selected_project(self, group_path: Path) -> str | None:
        value = self._last_selected_projects().get(self._group_key(group_path), "").strip()
        return value or None

    def save_last_selected_project(self, group_path: Path, project_name: str) -> None:
        name = project_name.strip()
        if not name:
            return
        selections = self._last_selected_projects()
        selections[self._group_key(group_path)] = name
        self._save_selections(selections)
        self.settings.sync()

    def _registrations(self) -> list[dict[str, str]]:
        raw = self.settings.value(self.REGISTERED_GROUPS_KEY, "", type=str).strip()
        try:
            decoded = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            decoded = []
        result: list[dict[str, str]] = []
        if isinstance(decoded, list):
            for value in decoded:
                if isinstance(value, str):
                    result.append({"path": value, "group_id": ""})
                elif isinstance(value, dict) and isinstance(value.get("path"), str):
                    result.append(
                        {
                            "path": value["path"],
                            "group_id": str(value.get("group_id", "")),
                        }
                    )
        return result

    def _stale_registrations(self) -> list[dict[str, str]]:
        raw = self.settings.value(self.STALE_GROUPS_KEY, "", type=str).strip()
        try:
            decoded = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            return []
        return [
            {
                "path": str(item.get("path", "")),
                "group_id": str(item.get("group_id", "")),
                "last_project": str(item.get("last_project", "")),
            }
            for item in decoded
            if isinstance(item, dict) and item.get("path")
        ] if isinstance(decoded, list) else []

    def _save_registrations(self, registrations: list[dict[str, str]]) -> None:
        self.settings.setValue(
            self.REGISTERED_GROUPS_KEY,
            json.dumps(registrations, ensure_ascii=False),
        )

    def _save_stale(self, stale: list[dict[str, str]]) -> None:
        self.settings.setValue(self.STALE_GROUPS_KEY, json.dumps(stale, ensure_ascii=False))

    def _last_selected_projects(self) -> dict[str, str]:
        raw = self.settings.value(self.LAST_SELECTED_PROJECTS_KEY, "", type=str).strip()
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        } if isinstance(data, dict) else {}

    def _save_selections(self, selections: dict[str, str]) -> None:
        self.settings.setValue(
            self.LAST_SELECTED_PROJECTS_KEY,
            json.dumps(selections, ensure_ascii=False, sort_keys=True),
        )

    @staticmethod
    def _read_group_id(path: Path) -> str:
        manifest = Path(path) / "项目组配置.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ""
        return str(data.get("group_id", "")) if isinstance(data, dict) else ""

    @staticmethod
    def _group_key(group_path: Path) -> str:
        return os.path.normcase(str(Path(group_path).expanduser().resolve()))
