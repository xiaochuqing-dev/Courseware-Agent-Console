from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from models import ProjectEntry, ProjectGroup
from .resource_paths import bundled_resource_root


class ProjectCreationError(RuntimeError):
    pass


class ValidationError(ProjectCreationError):
    pass


class TargetExistsError(ProjectCreationError):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"目标目录已存在：{path}")


class InvalidProjectGroupError(RuntimeError):
    pass


class ProjectService:
    REQUIRED_PUBLIC_TOOLS = ("WORKFLOW.md", "template.html", "validate-tool.js")
    PROJECT_PATTERN = re.compile(r"^项目([1-9]\d*)$")
    INVALID_NAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    RESERVED_NAMES = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (
            Path(resource_root)
            if resource_root
            else bundled_resource_root()
        )
        self.public_tools_root = self.resource_root / "default_public_tools"
        self.prompt_templates_root = self.resource_root / "prompt_templates"

    def public_tools_status(self) -> dict[str, bool]:
        return {
            name: (self.public_tools_root / name).is_file()
            for name in self.REQUIRED_PUBLIC_TOOLS
        }

    def missing_public_tools(self) -> list[str]:
        return [name for name, exists in self.public_tools_status().items() if not exists]

    def validate_creation(
        self,
        group_name: str,
        project_count: int,
        location: Path,
        json_files: list[Path],
    ) -> Path:
        name = group_name.strip()
        if not name:
            raise ValidationError("项目目录名称不能为空。")
        if self.INVALID_NAME_PATTERN.search(name) or name.endswith((".", " ")):
            raise ValidationError("项目目录名称包含 Windows 不允许的字符。")
        if name.upper().split(".")[0] in self.RESERVED_NAMES:
            raise ValidationError("该项目目录名称是 Windows 保留名称，请更换。")
        if project_count <= 0:
            raise ValidationError("项目数量必须大于 0。")

        parent = Path(location).expanduser()
        if not parent.is_dir():
            raise ValidationError("创建位置不存在或不是文件夹。")
        target = parent / name
        if target.exists():
            raise TargetExistsError(target)
        longest_target = target / f"项目{project_count}" / "产品迭代" / "第999轮修改.html"
        if len(str(longest_target.resolve())) >= 240:
            raise ValidationError(
                "目标路径过长，后续保存版本可能失败。请缩短项目组名称或选择更靠近磁盘根目录的位置："
                f"{target}"
            )

        missing = self.missing_public_tools()
        if missing:
            raise ValidationError(f"缺少公共工具：{', '.join(missing)}")

        rules_template = self.prompt_templates_root / "AGENT任务规则.md"
        if not rules_template.is_file():
            raise ValidationError("缺少规则模板：AGENT任务规则.md")

        if len(json_files) != project_count:
            raise ValidationError(
                f"JSON 数量与项目数量不一致：需要 {project_count} 个，当前 {len(json_files)} 个。"
            )

        resolved_files: list[Path] = []
        for path in json_files:
            resolved = Path(path).resolve()
            if not resolved.is_file() or resolved.suffix.lower() != ".json":
                raise ValidationError(f"不是有效的 JSON 文件：{path}")
            resolved_files.append(resolved)

        if len(set(resolved_files)) != len(resolved_files):
            raise ValidationError("每个项目必须映射唯一的 JSON 文件。")

        for path in resolved_files:
            try:
                with path.open("r", encoding="utf-8-sig") as handle:
                    json.load(handle)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValidationError(f"JSON 文件无法解析：{path.name}（{exc}）") from exc
        return target

    def create_project_group(
        self,
        group_name: str,
        project_count: int,
        location: Path,
        json_files: list[Path],
    ) -> ProjectGroup:
        target = self.validate_creation(group_name, project_count, location, json_files)
        staging = target.parent / f".{target.name}.creating-{uuid4().hex}"
        try:
            staging.mkdir()
            shutil.copy2(
                self.prompt_templates_root / "AGENT任务规则.md",
                staging / "AGENT任务规则.md",
            )

            tools_target = staging / "公共工具"
            tools_target.mkdir()
            for name in self.REQUIRED_PUBLIC_TOOLS:
                shutil.copy2(self.public_tools_root / name, tools_target / name)

            for index, json_path in enumerate(json_files, start=1):
                project_root = staging / f"项目{index}"
                source_root = project_root / "原始需求"
                source_root.mkdir(parents=True)
                (project_root / "客户反馈").mkdir()
                (project_root / "产品迭代").mkdir()
                shutil.copy2(json_path, source_root / Path(json_path).name)
                (project_root / "当前任务.md").write_text("", encoding="utf-8")
                (project_root / "项目记录.md").write_text(
                    "# 项目记录\n\n暂无执行记录。\n", encoding="utf-8"
                )

            staging.rename(target)
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if isinstance(exc, ProjectCreationError):
                raise
            raise ProjectCreationError(f"创建项目组失败：{exc}") from exc

        return self.load_project_group(target)

    def load_project_group(self, root: Path) -> ProjectGroup:
        resolved_root = Path(root).expanduser().resolve()
        if not resolved_root.is_dir():
            raise InvalidProjectGroupError("项目组目录不存在。")
        if not (resolved_root / "AGENT任务规则.md").is_file():
            raise InvalidProjectGroupError("所选目录缺少 AGENT任务规则.md。")

        projects: list[ProjectEntry] = []
        for path in resolved_root.iterdir():
            if not path.is_dir():
                continue
            match = self.PROJECT_PATTERN.fullmatch(path.name)
            if match:
                projects.append(
                    ProjectEntry(index=int(match.group(1)), name=path.name, path=path)
                )
        projects.sort(key=lambda item: item.index)
        return ProjectGroup(root=resolved_root, projects=tuple(projects))

    def validate_group_resources(self, group_root: Path) -> None:
        root = Path(group_root).resolve()
        rules = root / "AGENT任务规则.md"
        if not rules.is_file():
            raise FileNotFoundError(
                f"任务规则文件不存在：{rules}。请先在“编辑任务规则”中恢复默认规则。"
            )
        for name in self.REQUIRED_PUBLIC_TOOLS:
            path = root / "公共工具" / name
            if not path.is_file():
                raise FileNotFoundError(
                    f"公共工具缺失：{path}。请从原项目组备份恢复该文件后重试。"
                )

    def validate_project_structure(self, group_root: Path, project_root: Path) -> None:
        self.validate_group_resources(group_root)
        project = Path(project_root).resolve()
        if not project.is_dir():
            raise FileNotFoundError(f"项目目录不存在或已被改名：{project}")
        source = project / "原始需求"
        if not source.is_dir() or not any(path.is_file() for path in source.iterdir()):
            raise FileNotFoundError(
                f"原始需求目录为空或不存在：{source}。请恢复原始需求文件后重试。"
            )
        for directory_name in ("客户反馈", "产品迭代"):
            path = project / directory_name
            if not path.is_dir():
                raise FileNotFoundError(
                    f"项目目录缺失：{path}。请确认是否需要重新创建空目录。"
                )

    @staticmethod
    def open_in_file_manager(path: Path) -> None:
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"路径不存在：{target}")
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
