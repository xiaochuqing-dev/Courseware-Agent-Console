from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True, slots=True)
class ToolBinding:
    workflow: Path
    template: Path
    validate: Path

    def paths(self) -> dict[str, Path]:
        return {
            "workflow": Path(self.workflow),
            "template": Path(self.template),
            "validate": Path(self.validate),
        }


class ProjectService:
    REQUIRED_PUBLIC_TOOLS = ("WORKFLOW.md", "template.html", "validate-tool.js")
    TOOL_ROLES = {
        "workflow": "WORKFLOW.md",
        "template": "template.html",
        "validate": "validate-tool.js",
    }
    MANIFEST_NAME = "项目组配置.json"
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

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest().upper()

    def validate_tool_binding(self, binding: ToolBinding | None) -> dict[str, dict[str, str | int]]:
        if binding is None:
            missing = "、".join(self.TOOL_ROLES.values())
            raise ValidationError(
                f"尚未选择真实公共工具：{missing}。请分别选择 workflow、template、validate 文件后再创建。"
            )

        metadata: dict[str, dict[str, str | int]] = {}
        for role, expected_name in self.TOOL_ROLES.items():
            source = binding.paths()[role].expanduser()
            display_role = {"workflow": "workflow", "template": "template", "validate": "validate"}[role]
            if not source.exists():
                raise ValidationError(
                    f"{display_role} 文件不存在：{source}。请重新选择真实的 {expected_name}。"
                )
            if not source.is_file():
                raise ValidationError(f"{display_role} 路径不是文件：{source}")
            try:
                size = source.stat().st_size
                raw = source.read_bytes()
            except OSError as exc:
                raise ValidationError(f"{display_role} 文件无法读取：{source}（{exc}）") from exc
            if size == 0 or not raw.strip():
                raise ValidationError(f"{display_role} 文件为空：{source}")
            try:
                raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError(f"{display_role} 文件不是可解析的 UTF-8 文本：{source}") from exc
            metadata[role] = {
                "source_path": str(source.resolve()),
                "source_name": source.name,
                "size": size,
                "sha256": self.file_sha256(source),
                "copied_name": expected_name,
            }

        workflow_text = binding.workflow.read_text(encoding="utf-8-sig")
        for referenced in (binding.template.name, binding.validate.name):
            if referenced not in workflow_text:
                raise ValidationError(
                    f"workflow 未明确引用 {referenced}：{binding.workflow}。请确认三份文件属于同一套工具。"
                )

        validator = binding.validate.resolve()
        syntax = subprocess.run(
            ["node", "--check", str(validator)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if syntax.returncode != 0:
            detail = (syntax.stderr or syntax.stdout).strip()
            raise ValidationError(f"validate 文件语法检查失败：{validator}\n{detail}")

        template_check = subprocess.run(
            ["node", str(validator), str(binding.template.resolve())],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if template_check.returncode != 0:
            detail = (template_check.stdout or template_check.stderr).strip()
            raise ValidationError(
                f"template 未通过所选 validate：{binding.template.resolve()}\n{detail}"
            )
        return metadata

    def validate_creation(
        self,
        group_name: str,
        project_count: int,
        location: Path,
        json_files: list[Path],
        tool_binding: ToolBinding | None,
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
        longest_target = target / f"项目{project_count}" / "工作文件" / "第999轮修改.html"
        if len(str(longest_target.resolve())) >= 240:
            raise ValidationError(
                "目标路径过长，后续保存版本可能失败。请缩短项目组名称或选择更靠近磁盘根目录的位置："
                f"{target}"
            )

        self.validate_tool_binding(tool_binding)

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
        tool_binding: ToolBinding | None = None,
    ) -> ProjectGroup:
        target = self.validate_creation(
            group_name, project_count, location, json_files, tool_binding
        )
        assert tool_binding is not None
        tool_metadata = self.validate_tool_binding(tool_binding)
        staging = target.parent / f".{target.name}.creating-{uuid4().hex}"
        try:
            staging.mkdir()
            shutil.copy2(
                self.prompt_templates_root / "AGENT任务规则.md",
                staging / "AGENT任务规则.md",
            )

            tools_target = staging / "公共工具"
            tools_target.mkdir()
            for role, name in self.TOOL_ROLES.items():
                shutil.copy2(tool_binding.paths()[role], tools_target / name)

            manifest = {
                "schema_version": 1,
                "group_name": target.name,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "tools": tool_metadata,
                "product_directory": "工作文件",
                "delivery_directory": "最终交付",
            }
            (staging / self.MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            for index, json_path in enumerate(json_files, start=1):
                project_root = staging / f"项目{index}"
                source_root = project_root / "原始需求"
                source_root.mkdir(parents=True)
                (project_root / "客户反馈").mkdir()
                (project_root / "工作文件").mkdir()
                (project_root / "最终交付").mkdir()
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

        manifest_path = root / self.MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"项目组缺少真实工具绑定记录：{manifest_path}。旧项目组只可导入查看，不能生成新任务或通过验收。"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(f"项目组工具绑定记录无法解析：{manifest_path}（{exc}）") from exc
        tools = manifest.get("tools") if isinstance(manifest, dict) else None
        if not isinstance(tools, dict):
            raise FileNotFoundError(f"项目组工具绑定记录缺少 tools：{manifest_path}")
        for role, name in self.TOOL_ROLES.items():
            entry = tools.get(role)
            copied = root / "公共工具" / name
            if not isinstance(entry, dict) or not entry.get("sha256"):
                raise FileNotFoundError(f"项目组工具绑定记录缺少 {role} 哈希：{manifest_path}")
            if self.file_sha256(copied) != str(entry["sha256"]).upper():
                raise FileNotFoundError(f"项目组绑定的 {role} 文件已变化：{copied}")

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
        for directory_name in ("客户反馈", "工作文件", "最终交付"):
            path = project / directory_name
            if not path.is_dir():
                raise FileNotFoundError(
                    f"项目目录缺失：{path}。请确认是否需要重新创建空目录。"
                )

    def read_manifest(self, group_root: Path) -> dict:
        path = Path(group_root).resolve() / self.MANIFEST_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidProjectGroupError(f"项目组配置无法读取：{path}（{exc}）") from exc
        if not isinstance(data, dict):
            raise InvalidProjectGroupError(f"项目组配置格式无效：{path}")
        return data

    def delete_project_group(self, group_root: Path, delete_local_files: bool = False) -> None:
        root = Path(group_root).expanduser().resolve()
        if not delete_local_files:
            return
        self.load_project_group(root)
        if root.is_symlink():
            raise InvalidProjectGroupError("不允许递归删除符号链接项目组。")
        protected = {
            Path(root.anchor).resolve(),
            Path.home().resolve(),
            Path.home().joinpath("Desktop").resolve(),
        }
        if root in protected or len(root.parts) < 4:
            raise InvalidProjectGroupError(f"拒绝删除范围过大的目录：{root}")
        shutil.rmtree(root)

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
