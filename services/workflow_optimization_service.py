from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .archive_service import ArchiveService
from .feedback_service import FeedbackService
from .resource_paths import bundled_resource_root


ProgressCallback = Callable[[str], None]


class WorkflowOptimizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowOptimizationInput:
    group_root: Path
    selected_project_paths: tuple[Path, ...] = ()
    user_description: str = ""
    material_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowProjectInfo:
    project_path: Path
    display_name: str
    record_path: Path
    original_requirements_path: Path
    latest_product_path: Path | None
    feedback_rounds: tuple[int, ...]

    @property
    def feedback_summary(self) -> str:
        if not self.feedback_rounds:
            return "无反馈轮次"
        rounds = "、".join(f"第{number}轮" for number in self.feedback_rounds)
        return f"共 {len(self.feedback_rounds)} 轮（{rounds}）"


@dataclass(frozen=True, slots=True)
class WorkflowMaterialInfo:
    source_path: Path
    file_name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class WorkflowTaskResult:
    task_path: Path
    snapshot_path: Path
    material_paths: tuple[Path, ...]
    archived_path: Path | None


@dataclass(frozen=True, slots=True)
class WorkflowTaskValidationResult:
    valid: bool
    exists: bool
    reason: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def status_text(self) -> str:
        if not self.exists:
            return "尚未生成当前优化任务"
        if not self.valid:
            return f"任务已过期：{self.reason or '输入或任务文件已变化'}"
        return "当前优化任务可执行"


class WorkflowOptimizationService:
    DIRECTORY_NAME = "工作流优化"
    CURRENT_TASK_NAME = "当前优化任务.md"
    SNAPSHOT_NAME = "当前优化任务快照.json"
    MATERIALS_DIRECTORY_NAME = "补充材料"
    HISTORY_DIRECTORY_NAME = "历史优化任务"
    TEMPLATE_NAME = "manual_workflow_optimization_task.md"
    SNAPSHOT_SCHEMA_VERSION = 1
    UNRESOLVED_MARKERS = ("{{", "}}")

    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (
            Path(resource_root) if resource_root else bundled_resource_root()
        )
        self.templates_root = self.resource_root / "prompt_templates"
        self.archive_service = ArchiveService()
        self.feedback_service = FeedbackService()
        self._state_lock = threading.Lock()
        self._active_groups: set[str] = set()

    @staticmethod
    def path_key(path: Path) -> str:
        return os.path.normcase(
            str(Path(path).expanduser().resolve(strict=False))
        )

    def current_task_path(self, group_root: Path) -> Path:
        group = self._validate_group_root(group_root)
        return group / self.DIRECTORY_NAME / self.CURRENT_TASK_NAME

    def current_snapshot_path(self, group_root: Path) -> Path:
        group = self._validate_group_root(group_root)
        return group / self.DIRECTORY_NAME / self.SNAPSHOT_NAME

    def list_reference_projects(
        self, group_root: Path
    ) -> tuple[WorkflowProjectInfo, ...]:
        group = self._validate_group_root(group_root)
        archive_group = self._archive_group_root(group)
        if not archive_group.is_dir():
            return ()
        projects = self.archive_service.archived_projects(group, group.name)
        return tuple(self._project_info(path) for path in projects)

    def prepare_materials(
        self,
        material_paths: Sequence[Path],
        progress: ProgressCallback | None = None,
    ) -> tuple[WorkflowMaterialInfo, ...]:
        self._emit(progress, "正在检查补充材料…")
        seen_sources: set[str] = set()
        seen_names: dict[str, Path] = {}
        materials: list[WorkflowMaterialInfo] = []
        reserved_names = {
            self.CURRENT_TASK_NAME.casefold(),
            self.SNAPSHOT_NAME.casefold(),
        }

        for raw_path in material_paths:
            raw = Path(raw_path).expanduser()
            if raw.is_symlink():
                raise WorkflowOptimizationError(f"补充材料不是普通文件：{raw}")
            try:
                source = raw.resolve(strict=True)
            except FileNotFoundError as exc:
                raise WorkflowOptimizationError(f"补充材料不存在：{raw}") from exc
            source_key = self.path_key(source)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)

            if not source.is_file():
                raise WorkflowOptimizationError(f"补充材料不是普通文件：{source}")
            if source.name.casefold() in reserved_names:
                raise WorkflowOptimizationError(
                    f"补充材料与控制台管理文件同名：{source.name}"
                )

            previous = seen_names.get(source.name.casefold())
            if previous is not None:
                raise WorkflowOptimizationError(
                    "不同源文件存在同名冲突，未生成任务："
                    f"{previous}；{source}"
                )
            seen_names[source.name.casefold()] = source

            try:
                size = source.stat().st_size
                digest = self.file_sha256(source)
            except OSError as exc:
                raise WorkflowOptimizationError(
                    f"补充材料无法读取：{source}（{exc}）"
                ) from exc
            materials.append(
                WorkflowMaterialInfo(
                    source_path=source,
                    file_name=source.name,
                    size=size,
                    sha256=digest,
                )
            )
        return tuple(materials)

    def prepare_projects(
        self, group_root: Path, project_paths: Sequence[Path]
    ) -> tuple[WorkflowProjectInfo, ...]:
        group = self._validate_group_root(group_root)
        archive_group = self._archive_group_root(group)
        seen: set[str] = set()
        projects: list[WorkflowProjectInfo] = []
        for raw_path in project_paths:
            raw = Path(raw_path).expanduser()
            if raw.is_symlink():
                raise WorkflowOptimizationError(f"参考项目不能是符号链接：{raw}")
            try:
                project = raw.resolve(strict=True)
            except FileNotFoundError as exc:
                raise WorkflowOptimizationError(f"参考项目不存在：{raw}") from exc
            key = self.path_key(project)
            if key in seen:
                continue
            seen.add(key)
            if not project.is_dir() or project.parent != archive_group:
                raise WorkflowOptimizationError(
                    f"只能选择当前项目组“已完成项目”中的合法项目目录：{project}"
                )
            projects.append(self._project_info(project))
        return tuple(projects)

    def generate_task(
        self,
        workflow_input: WorkflowOptimizationInput,
        progress: ProgressCallback | None = None,
    ) -> WorkflowTaskResult:
        group = self._validate_group_root(workflow_input.group_root)
        projects = self.prepare_projects(
            group, workflow_input.selected_project_paths
        )
        description = str(workflow_input.user_description).strip()
        materials = self.prepare_materials(workflow_input.material_paths, progress)
        if not projects and not description and not materials:
            raise WorkflowOptimizationError(
                "请至少选择参考项目、填写优化说明或添加补充材料中的一项。"
            )

        group_key = self.path_key(group)
        with self._state_lock:
            if group_key in self._active_groups:
                raise WorkflowOptimizationError("当前项目组正在生成优化任务，请稍候。")
            self._active_groups.add(group_key)
        try:
            return self._generate_task(
                group, projects, description, materials, progress
            )
        finally:
            with self._state_lock:
                self._active_groups.discard(group_key)

    def validate_current_task(
        self, group_root: Path
    ) -> WorkflowTaskValidationResult:
        group = self._validate_group_root(group_root)
        optimization_root = group / self.DIRECTORY_NAME
        task_path = optimization_root / self.CURRENT_TASK_NAME
        snapshot_path = optimization_root / self.SNAPSHOT_NAME
        materials_root = optimization_root / self.MATERIALS_DIRECTORY_NAME
        if optimization_root.is_symlink() or (
            optimization_root.exists() and not optimization_root.is_dir()
        ):
            return WorkflowTaskValidationResult(
                False, True, "工作流优化目录不安全"
            )
        if task_path.is_symlink():
            return WorkflowTaskValidationResult(
                False, True, "当前优化任务不能是符号链接"
            )
        if not task_path.is_file():
            return WorkflowTaskValidationResult(False, False, "尚未生成任务")
        if snapshot_path.is_symlink():
            return WorkflowTaskValidationResult(
                False, True, "当前优化任务快照不能是符号链接"
            )
        if not snapshot_path.is_file():
            return WorkflowTaskValidationResult(
                False, True, "旧版任务缺少结构化快照"
            )
        if materials_root.is_symlink() or (
            materials_root.exists() and not materials_root.is_dir()
        ):
            return WorkflowTaskValidationResult(
                False, True, "补充材料目录不安全"
            )
        try:
            task_content = task_path.read_text(encoding="utf-8")
            metadata = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
            if not isinstance(metadata, dict):
                raise ValueError("优化任务快照格式无效")
            if int(metadata.get("schema_version", 0)) != self.SNAPSHOT_SCHEMA_VERSION:
                raise ValueError("优化任务快照版本过旧")
            if Path(str(metadata.get("group_path", ""))).resolve() != group:
                raise ValueError("项目组路径与优化任务快照不一致")
            if Path(str(metadata.get("task_path", ""))).resolve() != task_path.resolve():
                raise ValueError("优化任务路径与快照不一致")
            if Path(str(metadata.get("snapshot_path", ""))).resolve() != snapshot_path.resolve():
                raise ValueError("优化任务快照路径不一致")
            expected_task_hash = str(metadata.get("task_sha256", ""))
            if not expected_task_hash or self.text_sha256(task_content) != expected_task_hash:
                raise ValueError("当前优化任务文件已变化")
            if any(marker in task_content for marker in self.UNRESOLVED_MARKERS):
                raise ValueError("当前优化任务仍有未替换模板变量")
            self._validate_task_markers(task_content)
            stored_context = metadata.get("input_context")
            if not isinstance(stored_context, dict):
                raise ValueError("优化任务快照缺少输入上下文")
            current_context = self._current_input_context(
                group, stored_context, materials_root
            )
            current_hash = self.canonical_sha256(current_context)
            if current_hash != str(metadata.get("input_snapshot_sha256", "")):
                raise ValueError(
                    self._context_change_reason(stored_context, current_context)
                )
            return WorkflowTaskValidationResult(True, True, metadata=metadata)
        except Exception as exc:
            return WorkflowTaskValidationResult(
                False,
                True,
                str(exc),
                self._safe_read_json(snapshot_path),
            )

    def require_valid_current_task(
        self, group_root: Path
    ) -> WorkflowTaskValidationResult:
        result = self.validate_current_task(group_root)
        if not result.valid:
            raise ValueError(result.status_text)
        return result

    def load_current_input(
        self, group_root: Path
    ) -> WorkflowOptimizationInput | None:
        group = self._validate_group_root(group_root)
        optimization_root = group / self.DIRECTORY_NAME
        snapshot_path = optimization_root / self.SNAPSHOT_NAME
        if optimization_root.is_symlink() or snapshot_path.is_symlink():
            return None
        metadata = self._safe_read_json(snapshot_path)
        context = metadata.get("input_context")
        if not isinstance(context, dict):
            return None
        try:
            if Path(str(metadata.get("group_path", ""))).resolve() != group:
                return None
            if Path(str(metadata.get("task_path", ""))).resolve() != (
                optimization_root / self.CURRENT_TASK_NAME
            ).resolve():
                return None
            if Path(str(metadata.get("snapshot_path", ""))).resolve() != (
                optimization_root / self.SNAPSHOT_NAME
            ).resolve():
                return None
            project_paths = tuple(
                Path(str(item["project_path"]))
                for item in context.get("reference_projects", [])
                if isinstance(item, dict) and item.get("project_path")
            )
            materials_root = optimization_root / self.MATERIALS_DIRECTORY_NAME
            if materials_root.is_symlink():
                return None
            materials_root_resolved = materials_root.resolve(strict=False)
            material_paths: list[Path] = []
            for item in context.get("materials", []):
                if not isinstance(item, dict) or not item.get("file_name"):
                    continue
                file_name = str(item["file_name"])
                name_path = Path(file_name)
                if name_path.is_absolute() or name_path.name != file_name:
                    return None
                candidate = materials_root / file_name
                if candidate.is_symlink():
                    return None
                resolved = candidate.resolve(strict=False)
                if resolved.parent != materials_root_resolved:
                    return None
                material_paths.append(candidate)
            return WorkflowOptimizationInput(
                group_root=group,
                selected_project_paths=project_paths,
                user_description=str(context.get("user_description", "")),
                material_paths=tuple(material_paths),
            )
        except Exception:
            return None

    def _generate_task(
        self,
        group: Path,
        projects: tuple[WorkflowProjectInfo, ...],
        description: str,
        materials: tuple[WorkflowMaterialInfo, ...],
        progress: ProgressCallback | None,
    ) -> WorkflowTaskResult:
        common_paths = self._common_tool_paths(group)
        optimization_root = self._prepare_optimization_root(group)
        current_task = optimization_root / self.CURRENT_TASK_NAME
        current_snapshot = optimization_root / self.SNAPSHOT_NAME
        current_materials = optimization_root / self.MATERIALS_DIRECTORY_NAME
        history_root = optimization_root / self.HISTORY_DIRECTORY_NAME
        self._validate_managed_paths(
            current_task, current_snapshot, current_materials, history_root
        )

        transaction_id = uuid4().hex
        staging = optimization_root / f".生成中-{transaction_id}"
        archive_staging = optimization_root / f".归档中-{transaction_id}"
        archived_path: Path | None = None
        moved_old: list[tuple[Path, Path]] = []
        promoted_new: list[tuple[Path, Path]] = []
        preserve_archive_staging = False

        try:
            staging.mkdir()
            staged_materials = staging / self.MATERIALS_DIRECTORY_NAME
            staged_materials.mkdir()
            self._emit(progress, "正在复制补充材料…")
            copied_paths: list[Path] = []
            for material in materials:
                destination = staged_materials / material.file_name
                shutil.copy2(material.source_path, destination)
                if (
                    destination.stat().st_size != material.size
                    or self.file_sha256(destination) != material.sha256
                ):
                    raise WorkflowOptimizationError(
                        f"补充材料复制校验失败：{material.file_name}"
                    )
                copied_paths.append(current_materials / material.file_name)

            self._emit(progress, "正在生成当前优化任务…")
            project_snapshots = tuple(
                self._project_snapshot(project) for project in projects
            )
            material_snapshots = tuple(
                self._material_snapshot(material, current_materials / material.file_name)
                for material in materials
            )
            input_context = {
                "group": self._group_snapshot(group, common_paths),
                "user_description": description,
                "reference_projects": list(project_snapshots),
                "materials": list(material_snapshots),
            }
            input_hash = self.canonical_sha256(input_context)
            task_content = self._render_task(
                group,
                description,
                projects,
                materials,
                copied_paths,
                common_paths,
            ).rstrip() + "\n"
            generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
            snapshot_content = json.dumps(
                {
                    "schema_version": self.SNAPSHOT_SCHEMA_VERSION,
                    "group_path": str(group),
                    "generated_at": generated_at,
                    "task_path": str(current_task.resolve()),
                    "snapshot_path": str(current_snapshot.resolve()),
                    "task_sha256": self.text_sha256(task_content),
                    "input_snapshot_sha256": input_hash,
                    "input_context": input_context,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"
            staged_task = staging / self.CURRENT_TASK_NAME
            staged_snapshot = staging / self.SNAPSHOT_NAME
            staged_task.write_text(task_content, encoding="utf-8")
            staged_snapshot.write_text(snapshot_content, encoding="utf-8")
            if staged_task.read_text(encoding="utf-8") != task_content:
                raise WorkflowOptimizationError("当前优化任务暂存校验失败。")
            if staged_snapshot.read_text(encoding="utf-8") != snapshot_content:
                raise WorkflowOptimizationError("优化任务快照暂存校验失败。")

            has_previous = any(
                path.exists()
                for path in (current_task, current_snapshot, current_materials)
            )
            if has_previous:
                archive_staging.mkdir()
                self._emit(progress, "正在归档上一轮优化任务…")
                for source in (current_task, current_snapshot, current_materials):
                    if source.exists():
                        destination = archive_staging / source.name
                        self._move_path(source, destination)
                        moved_old.append((destination, source))

            for staged_path, current_path in (
                (staged_materials, current_materials),
                (staged_task, current_task),
                (staged_snapshot, current_snapshot),
            ):
                self._move_path(staged_path, current_path)
                promoted_new.append((current_path, staged_path))

            validation = self.validate_current_task(group)
            if not validation.valid:
                raise WorkflowOptimizationError(
                    f"当前优化任务写入后自校验失败：{validation.reason}"
                )

            if has_previous:
                history_root.mkdir(exist_ok=True)
                archived_path = self._allocate_history_path(history_root)
                self._move_path(archive_staging, archived_path)

            self._emit(progress, "当前优化任务已生成")
            return WorkflowTaskResult(
                task_path=current_task.resolve(),
                snapshot_path=current_snapshot.resolve(),
                material_paths=tuple(path.resolve() for path in copied_paths),
                archived_path=archived_path.resolve() if archived_path else None,
            )
        except Exception as exc:
            rollback_errors = self._rollback(
                promoted_new, moved_old, archived_path, archive_staging
            )
            if rollback_errors:
                preserve_archive_staging = archive_staging.exists()
                detail = "；".join(rollback_errors)
                recovery_paths = [
                    str(path)
                    for path in (archive_staging, archived_path)
                    if path is not None and path.exists()
                ]
                if recovery_paths:
                    detail += "；保留的恢复数据：" + "；".join(recovery_paths)
                raise WorkflowOptimizationError(
                    f"生成失败且回滚未完整完成：{detail}"
                ) from exc
            if isinstance(exc, WorkflowOptimizationError):
                raise
            raise WorkflowOptimizationError(f"生成当前优化任务失败：{exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if not preserve_archive_staging:
                shutil.rmtree(archive_staging, ignore_errors=True)

    def _rollback(
        self,
        promoted_new: list[tuple[Path, Path]],
        moved_old: list[tuple[Path, Path]],
        archived_path: Path | None,
        archive_staging: Path,
    ) -> list[str]:
        errors: list[str] = []
        if archived_path and archived_path.exists() and not archive_staging.exists():
            try:
                archived_path.replace(archive_staging)
            except OSError as exc:
                errors.append(f"无法撤回历史归档：{exc}")

        for current, staged in reversed(promoted_new):
            if current.exists():
                try:
                    current.replace(staged)
                except OSError as exc:
                    errors.append(f"无法撤回新文件 {current.name}：{exc}")

        for archived, original in reversed(moved_old):
            if archived.exists() and not original.exists():
                try:
                    archived.replace(original)
                except OSError as exc:
                    errors.append(f"无法恢复旧文件 {original.name}：{exc}")
        return errors

    def _render_task(
        self,
        group: Path,
        description: str,
        projects: Sequence[WorkflowProjectInfo],
        materials: Sequence[WorkflowMaterialInfo],
        copied_paths: Sequence[Path],
        common_paths: tuple[Path, Path, Path],
    ) -> str:
        template_path = self.templates_root / self.TEMPLATE_NAME
        if not template_path.is_file():
            raise WorkflowOptimizationError(f"缺少任务模板：{self.TEMPLATE_NAME}")
        template = template_path.read_text(encoding="utf-8")
        replacements = {
            "{{GROUP_ROOT}}": str(group),
            "{{WORKFLOW_PATH}}": str(common_paths[0]),
            "{{TEMPLATE_PATH}}": str(common_paths[1]),
            "{{VALIDATOR_PATH}}": str(common_paths[2]),
            "{{USER_DESCRIPTION}}": (
                description
                if description
                else "未单独填写，请根据参考项目或材料分析。"
            ),
            "{{REFERENCE_PROJECTS}}": self._format_projects(projects),
            "{{MATERIALS}}": self._format_materials(materials, copied_paths),
        }
        for marker, value in replacements.items():
            template = template.replace(marker, value)
        return template

    def _format_projects(self, projects: Sequence[WorkflowProjectInfo]) -> str:
        if not projects:
            return "本轮未选择参考项目。"
        lines: list[str] = []
        for index, project in enumerate(projects, start=1):
            latest = (
                str(project.latest_product_path)
                if project.latest_product_path is not None
                else "未找到可用产品"
            )
            lines.extend(
                [
                    f"### {index}. {project.display_name}",
                    f"- 项目路径：{project.project_path}",
                    f"- 项目记录路径：{project.record_path}",
                    f"- 原始需求路径：{project.original_requirements_path}",
                    f"- 最新有效产品：{latest}",
                    f"- 反馈轮次概况：{project.feedback_summary}",
                    "- 只读边界：仅用于分析，不得修改该历史项目中的任何文件。",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    def _format_materials(
        self,
        materials: Sequence[WorkflowMaterialInfo],
        copied_paths: Sequence[Path],
    ) -> str:
        if not materials:
            return "本轮未提供补充材料。"
        lines: list[str] = []
        for index, (material, copied) in enumerate(
            zip(materials, copied_paths), start=1
        ):
            lines.extend(
                [
                    f"### {index}. {material.file_name}",
                    f"- 文件名：{material.file_name}",
                    f"- 大小：{FeedbackService.format_size(material.size)}（{material.size} 字节）",
                    f"- SHA-256：{material.sha256}",
                    f"- 复制后的路径：{copied.resolve()}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    def _project_info(self, project_path: Path) -> WorkflowProjectInfo:
        project = Path(project_path).resolve()
        record = project / "项目记录.md"
        if record.is_symlink() or not record.is_file():
            raise WorkflowOptimizationError(f"已完成项目缺少安全的项目记录：{record}")
        original = project / "原始需求"
        if original.exists() and (original.is_symlink() or not original.is_dir()):
            raise WorkflowOptimizationError(f"已完成项目原始需求目录异常：{original}")
        latest = self.archive_service.latest_product(project)
        if latest is not None:
            if latest.is_symlink() or not latest.is_file():
                raise WorkflowOptimizationError(f"已完成项目最新产品异常：{latest}")
            latest = latest.resolve()
        return WorkflowProjectInfo(
            project_path=project,
            display_name=self.archive_service.archived_project_name(project),
            record_path=record.resolve(),
            original_requirements_path=original.resolve(),
            latest_product_path=latest,
            feedback_rounds=self.feedback_service.scan_rounds(project),
        )

    def _project_snapshot(self, project: WorkflowProjectInfo) -> dict[str, Any]:
        return {
            "display_name": project.display_name,
            "project_path": str(project.project_path),
            "record_path": str(project.record_path),
            "record_sha256": self.file_sha256(project.record_path),
            "original_requirements_path": str(project.original_requirements_path),
            "original_requirements": list(
                self._snapshot_directory(project.original_requirements_path)
            ),
            "latest_product_path": (
                str(project.latest_product_path)
                if project.latest_product_path is not None
                else ""
            ),
            "latest_product_sha256": (
                self.file_sha256(project.latest_product_path)
                if project.latest_product_path is not None
                else ""
            ),
            "feedback_rounds": [
                {
                    "round_number": number,
                    "materials": list(
                        self._snapshot_directory(
                            project.project_path / "客户反馈" / f"第{number}轮"
                        )
                    ),
                }
                for number in project.feedback_rounds
            ],
        }

    def _material_snapshot(
        self, material: WorkflowMaterialInfo, copied_path: Path
    ) -> dict[str, Any]:
        return {
            "file_name": material.file_name,
            "size_bytes": material.size,
            "sha256": material.sha256,
            "path": str(copied_path.resolve()),
        }

    def _group_snapshot(
        self, group: Path, common_paths: tuple[Path, Path, Path]
    ) -> dict[str, Any]:
        labels = ("workflow", "template", "validate")
        return {
            "path": str(group),
            "tools": [
                {
                    "role": role,
                    "path": str(path),
                    "sha256": self.file_sha256(path),
                }
                for role, path in zip(labels, common_paths)
            ],
        }

    def _current_input_context(
        self,
        group: Path,
        stored_context: dict[str, Any],
        materials_root: Path,
    ) -> dict[str, Any]:
        stored_projects = stored_context.get("reference_projects", [])
        project_paths = [
            Path(str(item.get("project_path", "")))
            for item in stored_projects
            if isinstance(item, dict)
        ]
        projects = self.prepare_projects(group, project_paths)
        stored_materials = stored_context.get("materials", [])
        material_snapshots = self._current_material_snapshots(
            materials_root, stored_materials
        )
        common_paths = self._common_tool_paths(group)
        return {
            "group": self._group_snapshot(group, common_paths),
            "user_description": str(stored_context.get("user_description", "")),
            "reference_projects": [
                self._project_snapshot(project) for project in projects
            ],
            "materials": list(material_snapshots),
        }

    def _current_material_snapshots(
        self, materials_root: Path, stored_materials: Any
    ) -> tuple[dict[str, Any], ...]:
        if not isinstance(stored_materials, list):
            raise ValueError("优化任务快照中的材料清单无效")
        if materials_root.exists() and (
            materials_root.is_symlink() or not materials_root.is_dir()
        ):
            raise ValueError("补充材料目录不安全")
        expected_names = [
            str(item.get("file_name", ""))
            for item in stored_materials
            if isinstance(item, dict)
        ]
        actual_entries = list(materials_root.iterdir()) if materials_root.is_dir() else []
        if any(path.is_symlink() for path in actual_entries):
            raise ValueError("补充材料目录中存在符号链接")
        if any(not path.is_file() for path in actual_entries):
            raise ValueError("补充材料目录中存在非普通文件")
        actual_files = actual_entries
        if {path.name for path in actual_files} != set(expected_names):
            raise ValueError("补充材料已删除或变化")
        snapshots: list[dict[str, Any]] = []
        by_name = {path.name: path.resolve() for path in actual_files}
        for name in expected_names:
            path = by_name[name]
            snapshots.append(
                {
                    "file_name": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": self.file_sha256(path),
                    "path": str(path),
                }
            )
        return tuple(snapshots)

    def _snapshot_directory(self, root: Path) -> tuple[dict[str, Any], ...]:
        directory = Path(root).resolve()
        if not directory.is_dir():
            return ()
        snapshots: list[dict[str, Any]] = []
        for raw in sorted(
            directory.rglob("*"),
            key=lambda item: item.relative_to(directory).as_posix().casefold(),
        ):
            if raw.is_symlink():
                raise WorkflowOptimizationError(
                    f"参考项目中存在符号链接，无法安全复盘：{raw}"
                )
            if not raw.is_file():
                continue
            path = raw.resolve()
            try:
                relative = path.relative_to(directory).as_posix()
            except ValueError as exc:
                raise WorkflowOptimizationError(
                    f"参考项目文件越出登记目录：{raw}"
                ) from exc
            snapshots.append(
                {
                    "relative_path": relative,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "sha256": self.file_sha256(path),
                }
            )
        return tuple(snapshots)

    def _prepare_optimization_root(self, group: Path) -> Path:
        root = group / self.DIRECTORY_NAME
        if root.exists():
            if root.is_symlink() or not root.is_dir():
                raise WorkflowOptimizationError(f"工作流优化路径不是安全目录：{root}")
        else:
            root.mkdir()
        resolved = root.resolve()
        try:
            resolved.relative_to(group)
        except ValueError as exc:
            raise WorkflowOptimizationError("工作流优化目录超出当前项目组。") from exc
        return resolved

    @staticmethod
    def _validate_managed_paths(
        current_task: Path,
        current_snapshot: Path,
        current_materials: Path,
        history_root: Path,
    ) -> None:
        for path in (current_task, current_snapshot):
            if path.exists() and (path.is_symlink() or not path.is_file()):
                raise WorkflowOptimizationError(f"控制台管理文件路径异常：{path}")
        for path in (current_materials, history_root):
            if path.exists() and (path.is_symlink() or not path.is_dir()):
                raise WorkflowOptimizationError(f"控制台管理路径不是安全目录：{path}")

    @staticmethod
    def _validate_group_root(group_root: Path) -> Path:
        group = Path(group_root).expanduser().resolve()
        if not group.is_dir():
            raise FileNotFoundError(f"项目组目录不存在：{group}")
        return group

    def _archive_group_root(self, group: Path) -> Path:
        root = self.archive_service.archive_root(group) / group.name
        if root.exists() and (root.is_symlink() or not root.is_dir()):
            raise WorkflowOptimizationError(f"已完成项目目录不安全：{root}")
        return root.resolve(strict=False)

    @staticmethod
    def _common_tool_paths(group: Path) -> tuple[Path, Path, Path]:
        paths = (
            group / "公共工具" / "WORKFLOW.md",
            group / "公共工具" / "template.html",
            group / "公共工具" / "validate-tool.js",
        )
        resolved: list[Path] = []
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise WorkflowOptimizationError(f"公共工具不存在或不安全：{path}")
            resolved.append(path.resolve())
        return tuple(resolved)

    @staticmethod
    def _allocate_history_path(history_root: Path) -> Path:
        stem = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        candidate = history_root / stem
        suffix = 1
        while candidate.exists():
            candidate = history_root / f"{stem}-{suffix:02d}"
            suffix += 1
        return candidate

    @staticmethod
    def _validate_task_markers(content: str) -> None:
        required = (
            "# 当前工作流优化任务",
            "## 当前项目组",
            "## 本轮优化目标",
            "## 参考项目",
            "## 补充材料",
            "## 执行顺序",
        )
        missing = [marker for marker in required if marker not in content]
        if missing:
            raise ValueError("当前优化任务语义字段缺失：" + "、".join(missing))

    @staticmethod
    def _context_change_reason(
        stored: dict[str, Any], current: dict[str, Any]
    ) -> str:
        checks = (
            ("group", "公共工具已变化"),
            ("reference_projects", "参考项目已删除或变化"),
            ("materials", "补充材料已删除或变化"),
            ("user_description", "优化说明已变化"),
        )
        for key, message in checks:
            if stored.get(key) != current.get(key):
                return message
        return "优化任务输入快照已变化"

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()

    @staticmethod
    def canonical_sha256(value: Any) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()

    @staticmethod
    def text_sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _safe_read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _move_path(source: Path, destination: Path) -> None:
        source.replace(destination)

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str) -> None:
        if progress:
            progress(message)
