from __future__ import annotations

import re
import shutil
from pathlib import Path


class ArchiveError(RuntimeError):
    pass


class NoProductVersionError(ArchiveError):
    pass


class ArchiveConflictError(ArchiveError):
    pass


class ArchiveService:
    VERSION_PATTERN = re.compile(r"^第([1-9]\d*)轮修改\.html$", re.IGNORECASE)
    PROJECT_PATTERN = re.compile(r"^项目([1-9]\d*)$")

    def latest_product(self, project_root: Path) -> Path | None:
        products_root = Path(project_root) / "产品迭代"
        if not products_root.is_dir():
            return None
        versions: list[tuple[int, Path]] = []
        for path in products_root.iterdir():
            if not path.is_file():
                continue
            match = self.VERSION_PATTERN.fullmatch(path.name)
            if match:
                versions.append((int(match.group(1)), path))
        if versions:
            return max(versions, key=lambda item: item[0])[1]
        initial = products_root / "初始版本.html"
        return initial if initial.is_file() else None

    def archive_root(self, group_root: Path) -> Path:
        group_path = Path(group_root).resolve()
        return group_path.parent / "已完成项目"

    def archive_destination(self, group_root: Path, project_name: str) -> Path:
        group_path = Path(group_root).resolve()
        return self.archive_root(group_path) / group_path.name / project_name

    def archive_project(self, group_root: Path, project_name: str) -> Path:
        group_path = Path(group_root).resolve()
        source = group_path / project_name
        if not source.is_dir():
            raise ArchiveError(f"项目目录不存在或已被改名：{source}")
        if self.latest_product(source) is None:
            raise NoProductVersionError(
                f"当前项目没有可用产品版本：{source / '产品迭代'}。"
                "请确认初始版本或第 N 轮修改 HTML 已保存。"
            )
        destination = self.archive_destination(group_path, project_name)
        if destination.exists():
            raise ArchiveConflictError(
                f"归档目标已存在同名项目：{destination}。不会覆盖或合并，"
                "请先手动核对并处理冲突。"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
        except Exception as exc:
            raise ArchiveError(
                f"项目归档失败。源目录：{source}；目标目录：{destination}。\n{exc}"
            ) from exc
        return destination

    def archived_group_names(self, group_root: Path) -> tuple[str, ...]:
        root = self.archive_root(group_root)
        if not root.is_dir():
            return ()
        return tuple(sorted(path.name for path in root.iterdir() if path.is_dir()))

    def archived_projects(self, group_root: Path, group_name: str) -> tuple[Path, ...]:
        root = self.archive_root(group_root) / group_name
        if not root.is_dir():
            return ()
        projects: list[tuple[int, Path]] = []
        for path in root.iterdir():
            if not path.is_dir():
                continue
            match = self.PROJECT_PATTERN.fullmatch(path.name)
            if match:
                projects.append((int(match.group(1)), path))
        return tuple(path for _, path in sorted(projects, key=lambda item: item[0]))
