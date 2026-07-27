from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from models import ProjectEntry, ProjectGroup

from .identity_service import (
    PROJECT_CONFIG_NAME,
    file_sha256,
    read_json_object,
    sanitize_project_name,
    unique_project_names,
    valid_uuid,
    write_courseware_meta,
    write_json_object,
)
from .process_utils import run_hidden_process
from .resource_paths import bundled_resource_root
from .app_logging import LOGGER_NAME


logger = logging.getLogger(LOGGER_NAME)


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


class MigrationRequiredError(InvalidProjectGroupError):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__(
            "检测到旧项目结构，需要迁移为“产品迭代”结构。"
        )


class RecycleBinError(RuntimeError):
    def __init__(self, root: Path, cause: BaseException) -> None:
        self.root = root
        self.cause = cause
        self.winerror = getattr(cause, "winerror", None)
        self.blocked_path = Path(getattr(cause, "filename", root) or root)
        detail = f"Windows 错误码：{self.winerror}" if self.winerror else str(cause)
        super().__init__(f"项目组暂时无法移到回收站：{self.blocked_path}\n{detail}")


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


@dataclass(frozen=True, slots=True)
class ToolFileInfo:
    source_path: str
    source_name: str
    copied_name: str
    size: int
    modified_ns: int
    sha256: str
    utf8_readable: bool = True

    def as_manifest(self) -> dict[str, str | int | bool]:
        return {
            "source_path": self.source_path,
            "source_name": self.source_name,
            "copied_name": self.copied_name,
            "size": self.size,
            "modified_ns": self.modified_ns,
            "sha256": self.sha256,
            "utf8_readable": self.utf8_readable,
        }


@dataclass(frozen=True, slots=True)
class ToolValidationResult:
    workflow: ToolFileInfo
    template: ToolFileInfo
    validate: ToolFileInfo
    syntax_passed: bool
    template_passed: bool
    stdout_summary: str
    stderr_summary: str
    warnings: tuple[str, ...] = ()

    def metadata(self) -> dict[str, dict[str, str | int | bool]]:
        return {
            "workflow": self.workflow.as_manifest(),
            "template": self.template.as_manifest(),
            "validate": self.validate.as_manifest(),
        }


@dataclass(frozen=True, slots=True)
class MaterialFileInfo:
    source_path: Path
    file_name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProjectStructureIssue:
    project_path: Path
    missing_directories: tuple[str, ...]
    unexpected_directories: tuple[Path, ...]

    def summary(self) -> str:
        missing = "、".join(self.missing_directories) or "无"
        unexpected = "、".join(path.name for path in self.unexpected_directories) or "无"
        return (
            f"{self.project_path.name}：缺少标准目录 {missing}；"
            f"发现未识别目录 {unexpected}。这些目录可能被手动改名。"
        )


@dataclass(frozen=True, slots=True)
class MigrationResult:
    group_root: Path
    conflicts: tuple[str, ...]
    report_path: Path


ProgressCallback = Callable[[str], None]


class ProjectService:
    REQUIRED_PUBLIC_TOOLS = ("WORKFLOW.md", "template.html", "validate-tool.js")
    TOOL_ROLES = {
        "workflow": "WORKFLOW.md",
        "template": "template.html",
        "validate": "validate-tool.js",
    }
    MANIFEST_NAME = "项目组配置.json"
    PROJECT_CONFIG_NAME = PROJECT_CONFIG_NAME
    MANIFEST_SCHEMA_VERSION = 3
    PRODUCT_DIRECTORY = "产品迭代"
    REQUIRED_PROJECT_DIRECTORIES = ("原始需求", "客户反馈", PRODUCT_DIRECTORY)
    LEGACY_DIRECTORIES = ("工作文件", "最终交付", "验收记录")
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
        self.resource_root = Path(resource_root) if resource_root else bundled_resource_root()
        self.public_tools_root = self.resource_root / "default_public_tools"
        self.prompt_templates_root = self.resource_root / "prompt_templates"
        self._validation_cache: dict[tuple[tuple[str, int, int, str], ...], ToolValidationResult] = {}
        self._cache_lock = threading.Lock()
        self._creation_lock = threading.Lock()
        self._creating_targets: set[str] = set()

    @staticmethod
    def file_sha256(path: Path) -> str:
        return file_sha256(path)

    sanitize_project_name = staticmethod(sanitize_project_name)

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str) -> None:
        if progress:
            progress(message)

    def validate_tool_binding(
        self,
        binding: ToolBinding | None,
        progress: ProgressCallback | None = None,
    ) -> ToolValidationResult:
        if binding is None:
            missing = "、".join(self.TOOL_ROLES.values())
            raise ValidationError(
                f"尚未选择真实公共工具：{missing}。请分别选择 workflow、template、validate 文件后再创建。"
            )

        self._emit(progress, "正在读取公共工具…")
        infos: dict[str, ToolFileInfo] = {}
        texts: dict[str, str] = {}
        cache_parts: list[tuple[str, int, int, str]] = []
        for role, expected_name in self.TOOL_ROLES.items():
            source = binding.paths()[role].expanduser()
            if not source.exists():
                raise ValidationError(
                    f"{role} 文件不存在：{source}。请重新选择真实的 {expected_name}。"
                )
            if not source.is_file():
                raise ValidationError(f"{role} 路径不是文件：{source}")
            try:
                stat = source.stat()
                raw = source.read_bytes()
            except OSError as exc:
                raise ValidationError(f"{role} 文件无法读取：{source}（{exc}）") from exc
            if stat.st_size == 0 or not raw.strip():
                raise ValidationError(f"{role} 文件为空：{source}")
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError(f"{role} 文件不是可解析的 UTF-8 文本：{source}") from exc
            digest = hashlib.sha256(raw).hexdigest().upper()
            resolved = str(source.resolve())
            infos[role] = ToolFileInfo(
                source_path=resolved,
                source_name=source.name,
                copied_name=expected_name,
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                sha256=digest,
            )
            texts[role] = text
            cache_parts.append((os.path.normcase(resolved), stat.st_size, stat.st_mtime_ns, digest))

        cache_key = tuple(cache_parts)
        with self._cache_lock:
            cached = self._validation_cache.get(cache_key)
        if cached is not None:
            self._emit(progress, "公共工具验证结果未变化，已复用。")
            return cached

        warnings: list[str] = []
        workflow_lower = texts["workflow"].lower()
        for role, normalized_name in (("template", "template.html"), ("validate", "validate-tool.js")):
            source_name = infos[role].source_name
            if source_name.casefold() != normalized_name.casefold():
                warnings.append(
                    f"所选 {role} 源文件名为“{source_name}”，创建时会规范化为“{normalized_name}”。"
                )
            role_words = ("template", "模板") if role == "template" else ("validate", "验证")
            if normalized_name.lower() not in workflow_lower and not any(
                word in workflow_lower for word in role_words
            ):
                warnings.append(
                    f"WORKFLOW 未明确写出 {normalized_name}，但将以真实执行结果判断兼容性。"
                )

        validator = Path(infos["validate"].source_path)
        self._emit(progress, "正在检查 validate 语法…")
        try:
            syntax = run_hidden_process(["node", "--check", str(validator)], timeout=30)
        except FileNotFoundError as exc:
            raise ValidationError("Node.js 不可用，请安装 Node.js 或检查 PATH 后重试。") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("validate 语法检查超时，请检查文件内容后重试。") from exc
        if syntax.returncode != 0:
            detail = (syntax.stderr or syntax.stdout).strip()
            raise ValidationError(f"validate 文件语法检查失败：{validator}\n{detail}")

        self._emit(progress, "正在验证 template…")
        try:
            template_check = run_hidden_process(
                ["node", str(validator), infos["template"].source_path], timeout=120
            )
        except subprocess.TimeoutExpired as exc:
            raise ValidationError("template 验证超时，请检查 validate 后重试。") from exc
        if template_check.returncode != 0:
            detail = (template_check.stdout or template_check.stderr).strip()
            raise ValidationError(
                f"template 未通过所选 validate：{infos['template'].source_path}\n{detail}"
            )

        result = ToolValidationResult(
            workflow=infos["workflow"],
            template=infos["template"],
            validate=infos["validate"],
            syntax_passed=True,
            template_passed=True,
            stdout_summary=(template_check.stdout or "").strip()[:4000],
            stderr_summary="\n".join(
                value.strip() for value in (syntax.stderr, template_check.stderr) if value.strip()
            )[:4000],
            warnings=tuple(warnings),
        )
        with self._cache_lock:
            self._validation_cache = {cache_key: result}
        return result

    def validate_creation(
        self,
        group_name: str,
        project_count: int,
        location: Path,
        json_files: list[Path],
        tool_binding: ToolBinding | None,
        validation_result: ToolValidationResult | None = None,
        progress: ProgressCallback | None = None,
        project_names: list[str] | tuple[str, ...] | None = None,
        json_validation_complete: bool = False,
        project_materials: dict[str, list[Path]] | None = None,
        material_validation_complete: bool = False,
    ) -> Path:
        self._emit(progress, "正在检查项目名称和 JSON 映射…")
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
        prepared_names = self.prepare_project_names(json_files, project_names, target)
        longest_directory = max((item[1] for item in prepared_names), key=len, default="项目")
        longest_target = (
            target / longest_directory / self.PRODUCT_DIRECTORY / f"{longest_directory}（999）.html"
        )
        if len(str(longest_target.resolve())) >= 240:
            raise ValidationError(
                "目标路径过长，后续保存版本可能失败。请缩短项目组名称或选择更靠近磁盘根目录的位置："
                f"{target}"
            )

        if validation_result is None:
            self.validate_tool_binding(tool_binding, progress)
        elif tool_binding is None:
            raise ValidationError("公共工具选择已失效，请重新选择。")

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
        if not json_validation_complete:
            for path in resolved_files:
                try:
                    with path.open("r", encoding="utf-8-sig") as handle:
                        json.load(handle)
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise ValidationError(f"JSON 文件无法解析：{path.name}（{exc}）") from exc
        if not material_validation_complete:
            self.validate_project_materials(json_files, project_materials, progress)
        return target

    def prepare_project_names(
        self,
        json_files: list[Path] | tuple[Path, ...],
        project_names: list[str] | tuple[str, ...] | None = None,
        target_root: Path | None = None,
    ) -> tuple[tuple[str, str], ...]:
        raw_names = (
            [Path(path).stem for path in json_files]
            if project_names is None
            else [str(value).strip() for value in project_names]
        )
        if len(raw_names) != len(json_files):
            raise ValidationError("项目名称数量与 JSON 数量不一致。")
        if any(not value for value in raw_names):
            raise ValidationError("项目名称不能为空。")
        max_length = 80
        if target_root is not None:
            remaining = 220 - len(str(Path(target_root).resolve())) - len(self.PRODUCT_DIRECTORY) - 24
            max_length = max(24, min(80, remaining))
        prepared = unique_project_names(raw_names, max_length)
        if project_names is not None and any(
            display != raw for (display, _directory), raw in zip(prepared, raw_names)
        ):
            raise ValidationError("项目名称必须唯一，且清洗后的目录名称也不能重复。")
        return prepared

    @staticmethod
    def path_key(path: Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def validate_project_materials(
        self,
        json_files: list[Path] | tuple[Path, ...],
        project_materials: dict[str, list[Path]] | None = None,
        progress: ProgressCallback | None = None,
    ) -> tuple[dict[str, tuple[MaterialFileInfo, ...]], int]:
        self._emit(progress, "正在验证首次制作材料…")
        selected_keys = {self.path_key(path): Path(path) for path in json_files}
        normalized_materials: dict[str, list[Path]] = {}
        for raw_key, raw_paths in (project_materials or {}).items():
            key = self.path_key(Path(raw_key))
            if key not in selected_keys:
                logger.warning(
                    "Initial material validation failed; type=unknown_project_mapping"
                )
                raise ValidationError("首次制作材料绑定了不在当前映射中的 JSON。")
            if not isinstance(raw_paths, (list, tuple)):
                logger.warning(
                    "Initial material validation failed; type=invalid_material_list"
                )
                raise ValidationError("首次制作材料列表格式无效。")
            normalized_materials.setdefault(key, []).extend(Path(path) for path in raw_paths)

        result: dict[str, tuple[MaterialFileInfo, ...]] = {}
        ignored_total = 0
        for key, json_path in selected_keys.items():
            seen_sources: set[str] = set()
            seen_names: dict[str, str] = {json_path.name.casefold(): "JSON"}
            infos: list[MaterialFileInfo] = []
            ignored_for_project = 0
            for source in normalized_materials.get(key, []):
                source_key = self.path_key(source)
                if source_key in seen_sources:
                    ignored_total += 1
                    ignored_for_project += 1
                    continue
                seen_sources.add(source_key)
                resolved = Path(source).expanduser().resolve()
                if not resolved.exists():
                    logger.warning(
                        "Initial material validation failed; type=missing_file; file=%s",
                        resolved.name,
                    )
                    raise ValidationError(f"首次制作材料不存在：{resolved.name}")
                if not resolved.is_file():
                    logger.warning(
                        "Initial material validation failed; type=not_regular_file; file=%s",
                        resolved.name,
                    )
                    raise ValidationError(f"首次制作材料不是普通文件：{resolved.name}")
                try:
                    with resolved.open("rb") as handle:
                        handle.read(1)
                    size = resolved.stat().st_size
                    digest = self.file_sha256(resolved)
                except OSError as exc:
                    logger.warning(
                        "Initial material validation failed; type=unreadable_file; file=%s",
                        resolved.name,
                    )
                    raise ValidationError(
                        f"首次制作材料无法读取：{resolved.name}（{exc}）"
                    ) from exc

                name_key = resolved.name.casefold()
                conflict = seen_names.get(name_key)
                if conflict == "JSON":
                    logger.warning(
                        "Initial material validation failed; type=json_name_conflict; file=%s",
                        resolved.name,
                    )
                    raise ValidationError(
                        f"首次制作材料与 JSON 文件同名：{resolved.name}"
                    )
                if conflict is not None:
                    logger.warning(
                        "Initial material validation failed; type=material_name_conflict; file=%s",
                        resolved.name,
                    )
                    raise ValidationError(
                        f"同一项目存在同名首次制作材料：{resolved.name}"
                    )
                seen_names[name_key] = resolved.name
                infos.append(
                    MaterialFileInfo(
                        source_path=resolved,
                        file_name=resolved.name,
                        size=size,
                        sha256=digest,
                    )
                )
            if ignored_for_project:
                logger.info(
                    "Ignored duplicate initial materials; project_json=%s; count=%d",
                    json_path.name,
                    ignored_for_project,
                )
            result[key] = tuple(infos)
        return result, ignored_total

    def create_project_group(
        self,
        group_name: str,
        project_count: int,
        location: Path,
        json_files: list[Path],
        tool_binding: ToolBinding | None = None,
        validation_result: ToolValidationResult | None = None,
        progress: ProgressCallback | None = None,
        project_names: list[str] | tuple[str, ...] | None = None,
        source_hashes: dict[str, str] | None = None,
        json_validation_complete: bool = False,
        project_materials: dict[str, list[Path]] | None = None,
    ) -> ProjectGroup:
        if validation_result is None:
            validation_result = self.validate_tool_binding(tool_binding, progress)
        material_infos, ignored_materials = self.validate_project_materials(
            json_files, project_materials, progress
        )
        if ignored_materials:
            self._emit(progress, f"已忽略 {ignored_materials} 个重复材料。")
        target = self.validate_creation(
            group_name,
            project_count,
            location,
            json_files,
            tool_binding,
            validation_result,
            progress,
            project_names,
            json_validation_complete,
            project_materials,
            True,
        )
        assert tool_binding is not None
        target_key = os.path.normcase(str(target.resolve()))
        with self._creation_lock:
            if target_key in self._creating_targets:
                raise ProjectCreationError(f"项目组正在创建：{target}")
            if target.exists():
                raise TargetExistsError(target)
            self._creating_targets.add(target_key)

        staging = target.parent / f".{target.name}.creating-{uuid4().hex}"
        try:
            self._emit(progress, "正在创建项目目录…")
            staging.mkdir()
            shutil.copy2(
                self.prompt_templates_root / "AGENT任务规则.md",
                staging / "AGENT任务规则.md",
            )
            tools_target = staging / "公共工具"
            tools_target.mkdir()
            for role, name in self.TOOL_ROLES.items():
                shutil.copy2(tool_binding.paths()[role], tools_target / name)

            group_id = str(uuid4())
            prepared_names = self.prepare_project_names(json_files, project_names, target)
            manifest = {
                "schema_version": self.MANIFEST_SCHEMA_VERSION,
                "group_id": str(uuid4()),
                "group_name": target.name,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "tools": validation_result.metadata(),
                "product_directory": self.PRODUCT_DIRECTORY,
                "projects": [],
            }
            self._emit(progress, "正在复制原始需求…")
            def create_project(arguments) -> dict[str, object]:
                index, json_path, names, materials = arguments
                display_name, directory_name = names
                project_id = str(uuid4())
                project_root = staging / directory_name
                source_root = project_root / "原始需求"
                source_root.mkdir(parents=True)
                (project_root / "客户反馈").mkdir()
                (project_root / self.PRODUCT_DIRECTORY).mkdir()
                json_target = source_root / Path(json_path).name
                shutil.copy2(json_path, json_target)
                expected_json_hash = (
                    source_hashes.get(self.path_key(json_path), "")
                    if source_hashes
                    else ""
                ) or self.file_sha256(json_path)
                if self.file_sha256(json_target) != expected_json_hash:
                    raise ProjectCreationError(
                        f"原始 JSON 复制校验失败：{Path(json_path).name}"
                    )

                source_materials: list[dict[str, str | int]] = []
                for material in materials:
                    destination = source_root / material.file_name
                    if destination.exists():
                        raise ProjectCreationError(
                            f"首次制作材料目标文件已存在：{material.file_name}"
                        )
                    shutil.copy2(material.source_path, destination)
                    if (
                        destination.stat().st_size != material.size
                        or self.file_sha256(destination) != material.sha256
                    ):
                        raise ProjectCreationError(
                            f"首次制作材料复制校验失败：{material.file_name}"
                        )
                    source_materials.append(
                        {
                            "source_id": str(uuid4()),
                            "file_name": material.file_name,
                            "sha256": material.sha256,
                            "size": material.size,
                        }
                    )
                logger.info(
                    "Copied initial materials; project=%s; count=%d",
                    display_name,
                    len(source_materials),
                )
                (project_root / "当前任务.md").write_text("", encoding="utf-8")
                (project_root / "项目记录.md").write_text(
                    "# 项目记录\n\n暂无执行记录。\n", encoding="utf-8"
                )
                self._emit(progress, "正在写入项目配置…")
                project_config = {
                    "schema_version": 1,
                    "project_id": project_id,
                    "order": index,
                    "display_name": display_name,
                    "directory_name": directory_name,
                    "source_json": {
                        "source_id": str(uuid4()),
                        "file_name": Path(json_path).name,
                        "sha256": expected_json_hash,
                    },
                    "source_materials": source_materials,
                    "product_base_name": display_name,
                    "known_directory_names": [directory_name],
                    "artifacts": [],
                }
                (project_root / self.PROJECT_CONFIG_NAME).write_text(
                    json.dumps(project_config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return {
                    "project_id": project_id,
                    "order": index,
                    "display_name": display_name,
                    "directory_name": directory_name,
                    "source_material_count": len(source_materials),
                }

            project_arguments = list(
                (
                    index,
                    json_path,
                    names,
                    material_infos.get(self.path_key(json_path), ()),
                )
                for index, (json_path, names) in enumerate(
                    zip(json_files, prepared_names), start=1
                )
            )
            with ThreadPoolExecutor(max_workers=min(4, len(project_arguments))) as executor:
                manifest["projects"] = list(executor.map(create_project, project_arguments))
            manifest["group_id"] = group_id
            (staging / self.MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if target.exists():
                raise TargetExistsError(target)
            staging.rename(target)
            self._emit(progress, "项目组创建完成")
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if isinstance(exc, ProjectCreationError):
                raise
            raise ProjectCreationError(f"创建项目组失败：{exc}") from exc
        finally:
            with self._creation_lock:
                self._creating_targets.discard(target_key)

        projects = tuple(
            ProjectEntry(
                index=int(record["order"]),
                name=str(record["display_name"]),
                path=(target / str(record["directory_name"])).resolve(),
                project_id=str(record["project_id"]),
                directory_name=str(record["directory_name"]),
            )
            for record in manifest["projects"]
        )
        return ProjectGroup(root=target.resolve(), projects=projects, group_id=group_id)

    def read_manifest(self, group_root: Path) -> dict:
        path = Path(group_root).resolve() / self.MANIFEST_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidProjectGroupError(f"项目组配置无法读取：{path}（{exc}）") from exc
        if not isinstance(data, dict):
            raise InvalidProjectGroupError(f"项目组配置格式无效：{path}")
        return data

    def requires_migration(self, root: Path) -> bool:
        group_root = Path(root).expanduser().resolve()
        try:
            manifest = self.read_manifest(group_root)
        except InvalidProjectGroupError:
            return False
        if int(manifest.get("schema_version", 1) or 1) < self.MANIFEST_SCHEMA_VERSION:
            return True
        return any(
            (project / legacy).exists()
            for project in self._project_paths(group_root)
            for legacy in self.LEGACY_DIRECTORIES
        )

    def load_project_group(self, root: Path, allow_legacy: bool = False) -> ProjectGroup:
        resolved_root = Path(root).expanduser().resolve()
        if not resolved_root.is_dir():
            raise InvalidProjectGroupError("项目组目录不存在。")
        if not (resolved_root / "AGENT任务规则.md").is_file():
            raise InvalidProjectGroupError("所选目录缺少 AGENT任务规则.md。")
        if not (resolved_root / self.MANIFEST_NAME).is_file():
            raise InvalidProjectGroupError(f"所选目录缺少 {self.MANIFEST_NAME}。")
        if not allow_legacy and self.requires_migration(resolved_root):
            raise MigrationRequiredError(resolved_root)
        manifest = self.read_manifest(resolved_root)
        schema_version = int(manifest.get("schema_version", 1) or 1)
        if schema_version < self.MANIFEST_SCHEMA_VERSION:
            projects = [
                ProjectEntry(
                    index=int(self.PROJECT_PATTERN.fullmatch(path.name).group(1)),
                    name=path.name,
                    path=path,
                    directory_name=path.name,
                )
                for path in self._legacy_project_paths(resolved_root)
            ]
            projects.sort(key=lambda item: item.index)
            return ProjectGroup(
                root=resolved_root,
                projects=tuple(projects),
                group_id=str(manifest.get("group_id", "")),
                migration_required=True,
            )

        configured = manifest.get("projects")
        if not isinstance(configured, list):
            raise InvalidProjectGroupError("项目组配置缺少 projects 索引。")
        scanned: dict[str, tuple[Path, dict]] = {}
        for path in resolved_root.iterdir():
            config_path = path / self.PROJECT_CONFIG_NAME
            if not path.is_dir() or not config_path.is_file():
                continue
            try:
                config = read_json_object(config_path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise InvalidProjectGroupError(f"项目配置无法读取：{config_path}（{exc}）") from exc
            project_id = str(config.get("project_id", ""))
            if not valid_uuid(project_id):
                raise InvalidProjectGroupError(f"项目配置缺少有效 project_id：{config_path}")
            if project_id in scanned:
                raise InvalidProjectGroupError(f"发现重复 project_id：{project_id}")
            scanned[project_id] = (path.resolve(), config)

        projects: list[ProjectEntry] = []
        normalized_records: list[dict[str, object]] = []
        known_ids: set[str] = set()
        manifest_changed = False
        for raw_record in configured:
            if not isinstance(raw_record, dict):
                manifest_changed = True
                continue
            project_id = str(raw_record.get("project_id", ""))
            match = scanned.get(project_id)
            if match is None:
                continue
            path, config = match
            known_ids.add(project_id)
            order = int(config.get("order", raw_record.get("order", len(projects) + 1)) or 0)
            display_name = str(
                config.get("display_name") or raw_record.get("display_name") or path.name
            ).strip()
            recorded_directory = str(
                config.get("directory_name") or raw_record.get("directory_name") or path.name
            )
            renamed_from = ""
            if recorded_directory != path.name:
                renamed_from = recorded_directory
                known = [str(value) for value in config.get("known_directory_names", [])]
                if recorded_directory and recorded_directory not in known:
                    known.append(recorded_directory)
                if path.name not in known:
                    known.append(path.name)
                config["known_directory_names"] = known
                config["directory_name"] = path.name
                config["directory_rename_notice"] = {
                    "old_name": recorded_directory,
                    "new_name": path.name,
                }
                write_json_object(path / self.PROJECT_CONFIG_NAME, config)
                manifest_changed = True
            normalized = {
                "project_id": project_id,
                "order": order,
                "display_name": display_name,
                "directory_name": path.name,
            }
            normalized_records.append(normalized)
            if any(raw_record.get(key) != value for key, value in normalized.items()):
                manifest_changed = True
            projects.append(
                ProjectEntry(
                    index=order,
                    name=display_name,
                    path=path,
                    project_id=project_id,
                    directory_name=path.name,
                    renamed_from=renamed_from,
                )
            )

        for project_id, (path, config) in scanned.items():
            if project_id in known_ids:
                continue
            order = int(config.get("order", len(projects) + 1) or len(projects) + 1)
            display_name = str(config.get("display_name") or path.name).strip()
            projects.append(
                ProjectEntry(
                    index=order,
                    name=display_name,
                    path=path,
                    project_id=project_id,
                    directory_name=path.name,
                )
            )
            normalized_records.append(
                {
                    "project_id": project_id,
                    "order": order,
                    "display_name": display_name,
                    "directory_name": path.name,
                }
            )
            manifest_changed = True

        projects.sort(key=lambda item: (item.index, item.display_name.casefold()))
        normalized_records.sort(key=lambda item: (int(item["order"]), str(item["display_name"]).casefold()))
        if manifest_changed:
            manifest["projects"] = normalized_records
            write_json_object(resolved_root / self.MANIFEST_NAME, manifest)
        return ProjectGroup(
            root=resolved_root,
            projects=tuple(projects),
            group_id=str(manifest.get("group_id", "")),
        )

    def _project_paths(self, root: Path) -> list[Path]:
        if not Path(root).is_dir():
            return []
        configured = [
            path
            for path in Path(root).iterdir()
            if path.is_dir() and (path / self.PROJECT_CONFIG_NAME).is_file()
        ]
        legacy = [
            path
            for path in Path(root).iterdir()
            if path.is_dir() and self.PROJECT_PATTERN.fullmatch(path.name)
        ]
        return list(dict.fromkeys([*configured, *legacy]))

    def _legacy_project_paths(self, root: Path) -> list[Path]:
        return [
            path
            for path in Path(root).iterdir()
            if path.is_dir() and self.PROJECT_PATTERN.fullmatch(path.name)
        ] if Path(root).is_dir() else []

    def read_project_config(self, project_root: Path) -> dict:
        path = Path(project_root).resolve() / self.PROJECT_CONFIG_NAME
        try:
            config = read_json_object(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidProjectGroupError(f"项目配置无法读取：{path}（{exc}）") from exc
        if not valid_uuid(config.get("project_id")):
            raise InvalidProjectGroupError(f"项目配置缺少有效 project_id：{path}")
        source_materials = config.get("source_materials", [])
        if not isinstance(source_materials, list):
            raise InvalidProjectGroupError(f"项目配置中的 source_materials 格式无效：{path}")
        config["source_materials"] = source_materials
        return config

    def write_project_config(self, project_root: Path, config: dict) -> None:
        write_json_object(Path(project_root).resolve() / self.PROJECT_CONFIG_NAME, config)

    def resolve_directory_rename(
        self, group_root: Path, project_id: str, adopt_display_name: bool
    ) -> ProjectEntry:
        group = self.load_project_group(group_root, allow_legacy=True)
        project = next((item for item in group.projects if item.project_id == project_id), None)
        if project is None:
            raise InvalidProjectGroupError("无法按 project_id 找到项目。")
        config = self.read_project_config(project.path)
        notice = config.pop("directory_rename_notice", None)
        if adopt_display_name:
            previous_base = str(config.get("product_base_name", ""))
            config["display_name"] = project.path.name
            if previous_base and previous_base != project.path.name:
                config["product_base_name_notice"] = {
                    "old_name": previous_base,
                    "new_name": project.path.name,
                }
        self.write_project_config(project.path, config)
        manifest = self.read_manifest(group.root)
        for record in manifest.get("projects", []):
            if isinstance(record, dict) and str(record.get("project_id")) == project_id:
                record["directory_name"] = project.path.name
                if adopt_display_name:
                    record["display_name"] = project.path.name
        write_json_object(group.root / self.MANIFEST_NAME, manifest)
        refreshed = self.load_project_group(group.root)
        result = next(item for item in refreshed.projects if item.project_id == project_id)
        if notice:
            record_path = result.path / "项目记录.md"
            with record_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"\n- 项目文件夹重命名：{notice.get('old_name', '')} → "
                    f"{notice.get('new_name', result.path.name)}\n"
                    f"- project_id 未变化：{project_id}\n"
                )
        return result

    def resolve_product_base_name(
        self, project_root: Path, use_new_name: bool
    ) -> None:
        project = Path(project_root).resolve()
        config = self.read_project_config(project)
        notice = config.pop("product_base_name_notice", None)
        if use_new_name and isinstance(notice, dict):
            config["product_base_name"] = str(
                notice.get("new_name") or config.get("display_name")
            )
        self.write_project_config(project, config)

    def validate_group_resources(self, group_root: Path) -> None:
        root = Path(group_root).resolve()
        rules = root / "AGENT任务规则.md"
        if not rules.is_file():
            raise FileNotFoundError(f"任务规则文件不存在：{rules}。")
        for name in self.REQUIRED_PUBLIC_TOOLS:
            path = root / "公共工具" / name
            if not path.is_file():
                raise FileNotFoundError(f"公共工具缺失：{path}。请从项目组备份恢复。")
        manifest = self.read_manifest(root)
        if int(manifest.get("schema_version", 1) or 1) < self.MANIFEST_SCHEMA_VERSION:
            raise MigrationRequiredError(root)
        if manifest.get("product_directory") != self.PRODUCT_DIRECTORY:
            raise InvalidProjectGroupError("项目组配置中的产品目录不是“产品迭代”。")
        tools = manifest.get("tools") if isinstance(manifest, dict) else None
        if not isinstance(tools, dict):
            raise FileNotFoundError(f"项目组工具绑定记录缺少 tools：{root / self.MANIFEST_NAME}")
        for role, name in self.TOOL_ROLES.items():
            entry = tools.get(role)
            copied = root / "公共工具" / name
            if not isinstance(entry, dict) or not entry.get("sha256"):
                raise FileNotFoundError(f"项目组工具绑定记录缺少 {role} 哈希。")
            if self.file_sha256(copied) != str(entry["sha256"]).upper():
                raise FileNotFoundError(f"项目组绑定的 {role} 文件已变化：{copied}")

    def inspect_project_structure(self, project_root: Path) -> ProjectStructureIssue | None:
        project = Path(project_root).resolve()
        if not project.is_dir():
            return ProjectStructureIssue(project, self.REQUIRED_PROJECT_DIRECTORIES, ())
        missing = tuple(name for name in self.REQUIRED_PROJECT_DIRECTORIES if not (project / name).is_dir())
        known = set(self.REQUIRED_PROJECT_DIRECTORIES) | set(self.LEGACY_DIRECTORIES)
        unexpected = tuple(
            sorted(
                (path for path in project.iterdir() if path.is_dir() and path.name not in known),
                key=lambda path: path.name,
            )
        )
        if not missing:
            return None
        return ProjectStructureIssue(project, missing, unexpected)

    def inspect_group_structure(self, group_root: Path) -> tuple[ProjectStructureIssue, ...]:
        issues = []
        for project in self._project_paths(Path(group_root).resolve()):
            issue = self.inspect_project_structure(project)
            if issue:
                issues.append(issue)
        return tuple(issues)

    def repair_project_directories(
        self, project_root: Path, mapping: dict[str, Path]
    ) -> None:
        project = Path(project_root).resolve()
        issue = self.inspect_project_structure(project)
        if issue is None:
            return
        if set(mapping) != set(issue.missing_directories):
            raise ValidationError("必须为每个缺失的标准目录选择一个对应文件夹。")
        sources: dict[str, Path] = {}
        for expected, raw_source in mapping.items():
            source = Path(raw_source).resolve()
            if expected not in self.REQUIRED_PROJECT_DIRECTORIES:
                raise ValidationError(f"未知标准目录：{expected}")
            if source.parent != project or not source.is_dir():
                raise ValidationError(f"只能选择当前项目下的直接子文件夹：{source}")
            if (project / expected).exists():
                raise ValidationError(f"目标目录已存在：{project / expected}")
            sources[expected] = source
        if len(set(sources.values())) != len(sources):
            raise ValidationError("同一个文件夹不能对应多个标准目录。")

        staged: dict[str, tuple[Path, Path]] = {}
        completed: list[tuple[Path, Path]] = []
        try:
            for expected, source in sources.items():
                temporary = project / f".{source.name}.renaming-{uuid4().hex}"
                source.rename(temporary)
                staged[expected] = (temporary, source)
            for expected, (temporary, original) in staged.items():
                destination = project / expected
                temporary.rename(destination)
                completed.append((destination, original))
        except Exception as exc:
            for destination, original in reversed(completed):
                if destination.exists() and not original.exists():
                    destination.rename(original)
            for temporary, original in staged.values():
                if temporary.exists() and not original.exists():
                    temporary.rename(original)
            raise ProjectCreationError(f"修复目录名称失败，已回滚：{exc}") from exc

    def validate_project_structure(self, group_root: Path, project_root: Path) -> None:
        self.validate_group_resources(group_root)
        project = Path(project_root).resolve()
        if not project.is_dir():
            raise FileNotFoundError(f"项目目录不存在或已被改名：{project}")
        issue = self.inspect_project_structure(project)
        if issue:
            raise FileNotFoundError(issue.summary() + "请使用“修复项目组”明确指定对应关系。")
        source = project / "原始需求"
        if not any(path.is_file() for path in source.iterdir()):
            raise FileNotFoundError(f"原始需求目录为空：{source}。请恢复原始需求文件后重试。")

    def migrate_legacy_group(
        self, group_root: Path, progress: ProgressCallback | None = None
    ) -> MigrationResult:
        root = Path(group_root).expanduser().resolve()
        if not root.is_dir():
            raise InvalidProjectGroupError(f"项目组目录不存在：{root}")
        if not self.requires_migration(root):
            raise InvalidProjectGroupError("当前项目组已经是 schema v3，无需重复迁移。")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        staging = root.parent / f".{root.name}.migrating-{uuid4().hex}"
        rollback = root.parent / f".{root.name}.migration-original-{uuid4().hex}"
        conflicts: list[str] = []
        replacing_group_directory = False
        self._emit(progress, "正在准备迁移项目组")
        try:
            shutil.copytree(root, staging)
            legacy_projects = self._project_paths(staging)
            legacy_projects.sort(
                key=lambda path: (
                    int(self.PROJECT_PATTERN.fullmatch(path.name).group(1))
                    if self.PROJECT_PATTERN.fullmatch(path.name)
                    else 999999,
                    path.name.casefold(),
                )
            )
            for project in legacy_projects:
                product = project / self.PRODUCT_DIRECTORY
                product.mkdir(exist_ok=True)
                for legacy_name in ("工作文件", "最终交付"):
                    legacy = project / legacy_name
                    if legacy.is_dir():
                        self._merge_directory(legacy, product, legacy_name, conflicts)
                        if legacy.exists() and not any(legacy.iterdir()):
                            legacy.rmdir()
                old_reports = project / "验收记录"
                if old_reports.is_dir():
                    self._append_legacy_acceptance_summary(project, old_reports)
                    shutil.rmtree(old_reports)

            source_names: list[str] = []
            for project in legacy_projects:
                existing_config = None
                try:
                    existing_config = read_json_object(project / self.PROJECT_CONFIG_NAME)
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    pass
                json_files = sorted((project / "原始需求").glob("*.json"))
                source_names.append(
                    str(existing_config.get("display_name"))
                    if existing_config and valid_uuid(existing_config.get("project_id"))
                    else (json_files[0].stem if len(json_files) == 1 else project.name)
                )
            prepared_names = unique_project_names(source_names)

            staged_projects: list[tuple[Path, Path, str, str, int]] = []
            for order, (project, names) in enumerate(
                zip(legacy_projects, prepared_names), start=1
            ):
                display_name, directory_name = names
                temporary = staging / f".project-identity-{uuid4().hex}"
                project.rename(temporary)
                staged_projects.append(
                    (temporary, staging / directory_name, display_name, directory_name, order)
                )
            for temporary, destination, _display, _directory, _order in staged_projects:
                if destination.exists():
                    raise ProjectCreationError(f"迁移目录名称冲突：{destination}")
                temporary.rename(destination)

            project_records: list[dict[str, object]] = []
            for _temporary, project, display_name, directory_name, order in staged_projects:
                json_files = sorted((project / "原始需求").glob("*.json"))
                source_json = json_files[0] if len(json_files) == 1 else None
                try:
                    existing_config = read_json_object(project / self.PROJECT_CONFIG_NAME)
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    existing_config = None
                if existing_config and valid_uuid(existing_config.get("project_id")):
                    config = existing_config
                    config.setdefault("source_materials", [])
                    project_id = str(config["project_id"])
                    known_names = [
                        str(value) for value in config.get("known_directory_names", [])
                    ]
                    if directory_name not in known_names:
                        known_names.append(directory_name)
                    config.update(
                        schema_version=1,
                        order=order,
                        display_name=display_name,
                        directory_name=directory_name,
                        known_directory_names=known_names,
                    )
                    config.setdefault("product_base_name", display_name)
                    config.setdefault("artifacts", [])
                else:
                    project_id = str(uuid4())
                    config = {
                        "schema_version": 1,
                        "project_id": project_id,
                        "order": order,
                        "display_name": display_name,
                        "directory_name": directory_name,
                        "source_json": {
                            "source_id": str(uuid4()),
                            "file_name": source_json.name if source_json else "",
                            "sha256": self.file_sha256(source_json) if source_json else "",
                        },
                        "source_materials": [],
                        "product_base_name": display_name,
                        "known_directory_names": [directory_name],
                        "artifacts": [],
                    }
                self._migrate_legacy_products(project, config, conflicts)
                write_json_object(project / self.PROJECT_CONFIG_NAME, config)
                task = project / "当前任务.md"
                if task.is_file():
                    try:
                        task_text = task.read_text(encoding="utf-8-sig")
                        task_text = re.sub(r"项目[：:]\s*项目\d+", f"项目显示名：{display_name}", task_text)
                        task.write_text(task_text, encoding="utf-8")
                    except (OSError, UnicodeError):
                        pass
                project_records.append(
                    {
                        "project_id": project_id,
                        "order": order,
                        "display_name": display_name,
                        "directory_name": directory_name,
                    }
                )

            manifest = self.read_manifest(staging)
            manifest["schema_version"] = self.MANIFEST_SCHEMA_VERSION
            manifest["group_id"] = str(manifest.get("group_id") or uuid4())
            manifest["product_directory"] = self.PRODUCT_DIRECTORY
            manifest["projects"] = project_records
            manifest.pop("delivery_directory", None)
            (staging / self.MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            shutil.copy2(
                self.prompt_templates_root / "AGENT任务规则.md",
                staging / "AGENT任务规则.md",
            )
            report = staging / f"迁移报告-{stamp}.md"
            report.write_text(
                "# 项目结构迁移报告\n\n"
                f"迁移时间：{datetime.now().astimezone().isoformat(timespec='seconds')}\n\n"
                "目标结构：原始需求 / 客户反馈 / 产品迭代 / 项目配置.json\n\n"
                "项目名称已从唯一原始 JSON 文件名生成，项目和产品已登记稳定 ID。\n\n"
                + ("同名冲突：\n" + "\n".join(f"- {item}" for item in conflicts) if conflicts else "同名冲突：无")
                + "\n",
                encoding="utf-8",
            )
            self.load_project_group(staging)
            self._emit(progress, "正在迁移项目组")
            replacing_group_directory = True
            root.rename(rollback)
            try:
                staging.rename(root)
            except Exception as replacement_error:
                try:
                    rollback.rename(root)
                except Exception as restore_error:
                    raise ProjectCreationError(
                        "项目结构迁移未完成，自动恢复原项目失败。"
                        f"原项目仍保留在：{rollback}。"
                        "请不要删除或移动该目录。"
                    ) from restore_error
                raise replacement_error
            shutil.rmtree(rollback)
            return MigrationResult(root, tuple(conflicts), root / report.name)
        except Exception as exc:
            if not root.exists() and rollback.exists():
                try:
                    rollback.rename(root)
                except Exception as restore_error:
                    raise ProjectCreationError(
                        "项目结构迁移未完成，自动恢复原项目失败。"
                        f"原项目仍保留在：{rollback}。"
                        "请不要删除或移动该目录。"
                    ) from restore_error
            if isinstance(exc, InvalidProjectGroupError):
                raise
            if isinstance(exc, ProjectCreationError) and "自动恢复原项目失败" in str(exc):
                raise
            raise ProjectCreationError(
                self._migration_failure_message(exc, replacing_group_directory)
            ) from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _migration_failure_message(
        error: BaseException, replacing_group_directory: bool
    ) -> str:
        permission_blocked = isinstance(error, PermissionError) or getattr(
            error, "winerror", None
        ) in {5, 32, 33}
        if replacing_group_directory and permission_blocked:
            return (
                "无法重命名项目文件夹，因为它正在资源管理器中打开，"
                "或被其他程序占用。\n\n"
                "请关闭已打开的项目文件夹，以及正在使用其中内容的程序，"
                "然后重新点击“预览迁移”。\n\n"
                "原项目保持不变，未创建备份。"
            )
        return f"项目结构迁移未完成，原项目保持不变，未创建备份：{error}"

    def preview_legacy_migration(self, group_root: Path) -> tuple[tuple[str, str], ...]:
        root = Path(group_root).expanduser().resolve()
        projects = self._project_paths(root)
        projects.sort(
            key=lambda path: (
                int(self.PROJECT_PATTERN.fullmatch(path.name).group(1))
                if self.PROJECT_PATTERN.fullmatch(path.name)
                else 999999,
                path.name.casefold(),
            )
        )
        source_names = []
        for project in projects:
            json_files = sorted((project / "原始需求").glob("*.json"))
            source_names.append(json_files[0].stem if len(json_files) == 1 else project.name)
        prepared = unique_project_names(source_names)
        return tuple(
            (project.name, display_name)
            for project, (display_name, _directory_name) in zip(projects, prepared)
        )

    def _migrate_legacy_products(
        self, project: Path, config: dict, conflicts: list[str]
    ) -> None:
        product_root = project / self.PRODUCT_DIRECTORY
        artifacts: list[dict[str, object]] = [
            item for item in config.get("artifacts", []) if isinstance(item, dict)
        ]
        config["artifacts"] = artifacts
        artifacts_by_version = {
            int(item.get("version_number", -1)): item for item in artifacts
        }
        candidates: list[tuple[int, Path]] = []
        for path in sorted(product_root.glob("*.html")):
            if path.name == "初始版本.html":
                candidates.append((0, path))
                continue
            legacy = re.fullmatch(r"第([1-9]\d*)轮修改\.html", path.name, re.IGNORECASE)
            if legacy:
                candidates.append((int(legacy.group(1)), path))
                continue
            base = re.escape(sanitize_project_name(str(config["product_base_name"]), 96))
            if re.fullmatch(rf"{base}\.html", path.name, re.IGNORECASE):
                candidates.append((0, path))
                continue
            current = re.fullmatch(rf"{base}（([1-9]\d*)）\.html", path.name, re.IGNORECASE)
            if current:
                candidates.append((int(current.group(1)), path))

        seen_versions: set[int] = set()
        for version, original in candidates:
            if version in seen_versions:
                conflicts.append(f"{project.name} 存在多个版本 {version} 的 HTML，已保留原名")
                continue
            seen_versions.add(version)
            expected = (
                f"{sanitize_project_name(str(config['product_base_name']), 96)}.html"
                if version == 0
                else f"{sanitize_project_name(str(config['product_base_name']), 96)}（{version}）.html"
            )
            destination = product_root / expected
            path = original
            aliases: list[str] = []
            if original.name != expected:
                if destination.exists():
                    conflicts.append(f"{original.name} 未改名：目标 {expected} 已存在")
                else:
                    aliases.append(original.name)
                    original.rename(destination)
                    path = destination
            artifact = artifacts_by_version.get(version)
            artifact_id = (
                str(artifact.get("artifact_id"))
                if artifact and valid_uuid(artifact.get("artifact_id"))
                else str(uuid4())
            )
            try:
                write_courseware_meta(
                    path,
                    str(config["project_id"]),
                    artifact_id,
                    version,
                    version,
                )
            except (OSError, UnicodeError):
                conflicts.append(f"{path.name} 无法写入稳定 ID meta")
            if artifact is None:
                artifact = {
                    "artifact_id": artifact_id,
                    "project_id": str(config["project_id"]),
                    "type": "courseware_html",
                    "version_number": version,
                    "feedback_round": version,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "aliases": [],
                    "ignored_names": [],
                }
                artifacts.append(artifact)
            existing_aliases = artifact.setdefault("aliases", [])
            for alias in aliases:
                if alias not in existing_aliases:
                    existing_aliases.append(alias)
            artifact.update(
                project_id=str(config["project_id"]),
                expected_name=expected,
                current_name=path.name,
                sha256=self.file_sha256(path),
            )

    def _merge_directory(
        self,
        source: Path,
        destination: Path,
        source_label: str,
        conflicts: list[str],
    ) -> None:
        for item in list(source.iterdir()):
            target = destination / item.name
            if not target.exists():
                shutil.move(str(item), str(target))
                continue
            if item.is_dir() and target.is_dir():
                self._merge_directory(item, target, source_label, conflicts)
                if not any(item.iterdir()):
                    item.rmdir()
                continue
            candidate = self._conflict_path(destination, item.name, source_label)
            shutil.move(str(item), str(candidate))
            conflicts.append(f"{item.name} → {candidate.name}（来自{source_label}）")

    @staticmethod
    def _conflict_path(destination: Path, name: str, source_label: str) -> Path:
        original = Path(name)
        stem, suffix = original.stem, original.suffix
        candidate = destination / f"{stem}-来自{source_label}{suffix}"
        index = 2
        while candidate.exists():
            candidate = destination / f"{stem}-来自{source_label}-{index}{suffix}"
            index += 1
        return candidate

    @staticmethod
    def _append_legacy_acceptance_summary(project: Path, report_root: Path) -> None:
        reports = sorted(path for path in report_root.rglob("*") if path.is_file())
        record = project / "项目记录.md"
        contents = [
            (report.relative_to(report_root), report.read_text(encoding="utf-8-sig"))
            for report in reports
        ]
        if not contents:
            return
        with record.open("a", encoding="utf-8") as handle:
            handle.write("\n\n## 历史验收记录（结构迁移）\n")
            for relative_path, text in contents:
                handle.write(f"\n### 文件：{relative_path.as_posix()}\n\n")
                handle.write(text)
                if not text.endswith("\n"):
                    handle.write("\n")

    def validate_removal_target(self, group_root: Path) -> Path:
        root = Path(group_root).expanduser().resolve()
        if not root.exists():
            raise InvalidProjectGroupError(f"项目组目录不存在：{root}")
        if not root.is_dir() or root.is_symlink():
            raise InvalidProjectGroupError("只允许处理真实的项目组根目录。")
        protected = {
            Path(root.anchor).resolve(),
            Path.home().resolve(),
            Path.home().joinpath("Desktop").resolve(),
        }
        if root in protected or len(root.parts) < 4:
            raise InvalidProjectGroupError(f"拒绝处理范围过大的目录：{root}")
        return root

    def move_project_group_to_recycle_bin(self, group_root: Path) -> None:
        root = self.validate_removal_target(group_root)
        try:
            from send2trash import send2trash

            send2trash(str(root))
        except Exception as exc:
            raise RecycleBinError(root, exc) from exc
        if root.exists():
            raise RecycleBinError(root, OSError("系统未确认项目组已进入回收站。"))

    def delete_project_group(self, group_root: Path, delete_local_files: bool = False) -> None:
        if delete_local_files:
            self.move_project_group_to_recycle_bin(group_root)

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
