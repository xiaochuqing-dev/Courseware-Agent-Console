from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from models import ProjectEntry, ProjectGroup

from .process_utils import run_hidden_process
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


class MigrationRequiredError(InvalidProjectGroupError):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        super().__init__(
            "检测到旧项目结构，需要先备份并迁移为“产品迭代”结构。"
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
    backup_root: Path
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
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest().upper()

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
        longest_target = target / f"项目{project_count}" / self.PRODUCT_DIRECTORY / "第999轮修改.html"
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
        validation_result: ToolValidationResult | None = None,
        progress: ProgressCallback | None = None,
    ) -> ProjectGroup:
        if validation_result is None:
            validation_result = self.validate_tool_binding(tool_binding, progress)
        target = self.validate_creation(
            group_name,
            project_count,
            location,
            json_files,
            tool_binding,
            validation_result,
            progress,
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

            manifest = {
                "schema_version": 2,
                "group_id": str(uuid4()),
                "group_name": target.name,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "tools": validation_result.metadata(),
                "product_directory": self.PRODUCT_DIRECTORY,
            }
            (staging / self.MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            self._emit(progress, "正在复制公共工具和原始需求…")
            for index, json_path in enumerate(json_files, start=1):
                project_root = staging / f"项目{index}"
                source_root = project_root / "原始需求"
                source_root.mkdir(parents=True)
                (project_root / "客户反馈").mkdir()
                (project_root / self.PRODUCT_DIRECTORY).mkdir()
                shutil.copy2(json_path, source_root / Path(json_path).name)
                (project_root / "当前任务.md").write_text("", encoding="utf-8")
                (project_root / "项目记录.md").write_text(
                    "# 项目记录\n\n暂无执行记录。\n", encoding="utf-8"
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

        return self.load_project_group(target)

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
        if int(manifest.get("schema_version", 1) or 1) < 2:
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
        projects = [
            ProjectEntry(index=int(self.PROJECT_PATTERN.fullmatch(path.name).group(1)), name=path.name, path=path)
            for path in self._project_paths(resolved_root)
        ]
        projects.sort(key=lambda item: item.index)
        return ProjectGroup(root=resolved_root, projects=tuple(projects))

    def _project_paths(self, root: Path) -> list[Path]:
        if not Path(root).is_dir():
            return []
        return [
            path
            for path in Path(root).iterdir()
            if path.is_dir() and self.PROJECT_PATTERN.fullmatch(path.name)
        ]

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
        if int(manifest.get("schema_version", 1) or 1) < 2:
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
            raise InvalidProjectGroupError("当前项目组已经是 schema v2，无需重复迁移。")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = root.parent / f"{root.name}-迁移前备份-{stamp}"
        suffix = 2
        while backup.exists():
            backup = root.parent / f"{root.name}-迁移前备份-{stamp}-{suffix}"
            suffix += 1
        staging = root.parent / f".{root.name}.migrating-{uuid4().hex}"
        rollback = root.parent / f".{root.name}.migration-original-{uuid4().hex}"
        conflicts: list[str] = []
        self._emit(progress, "正在创建迁移工作副本…")
        try:
            shutil.copytree(root, backup)
            shutil.copytree(root, staging)
            for project in self._project_paths(staging):
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

            manifest = self.read_manifest(staging)
            manifest["schema_version"] = 2
            manifest["group_id"] = str(manifest.get("group_id") or uuid4())
            manifest["product_directory"] = self.PRODUCT_DIRECTORY
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
                "目标结构：原始需求 / 客户反馈 / 产品迭代\n\n"
                + ("同名冲突：\n" + "\n".join(f"- {item}" for item in conflicts) if conflicts else "同名冲突：无")
                + "\n",
                encoding="utf-8",
            )
            self._emit(progress, "正在保留完整迁移前备份…")
            root.rename(rollback)
            try:
                staging.rename(root)
            except Exception:
                rollback.rename(root)
                raise
            self.load_project_group(root)
            shutil.rmtree(rollback, ignore_errors=True)
            return MigrationResult(root, backup, tuple(conflicts), root / report.name)
        except Exception as exc:
            if not root.exists() and rollback.exists():
                rollback.rename(root)
            if isinstance(exc, InvalidProjectGroupError):
                raise
            raise ProjectCreationError(f"项目结构迁移失败，原目录和备份均已保留：{exc}") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

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
        reports = sorted(report_root.glob("验收-*.md"))
        record = project / "项目记录.md"
        summary = "旧验收文件已完整保留在项目组迁移前备份中。"
        if reports:
            try:
                text = reports[-1].read_text(encoding="utf-8-sig").strip()
                summary += "\n\n" + text[:2000]
            except OSError:
                pass
        with record.open("a", encoding="utf-8") as handle:
            handle.write("\n\n## 历史完整验收摘要（结构迁移）\n\n" + summary + "\n")

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
