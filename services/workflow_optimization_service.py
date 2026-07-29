from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .resource_paths import bundled_resource_root


ProgressCallback = Callable[[str], None]


class WorkflowOptimizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowMaterialInfo:
    source_path: Path
    file_name: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class WorkflowTaskResult:
    task_path: Path
    material_paths: tuple[Path, ...]
    archived_path: Path | None


class WorkflowOptimizationService:
    DIRECTORY_NAME = "工作流优化"
    CURRENT_TASK_NAME = "当前优化任务.md"
    MATERIALS_DIRECTORY_NAME = "补充材料"
    HISTORY_DIRECTORY_NAME = "历史优化任务"
    TEMPLATE_NAME = "manual_workflow_optimization_task.md"

    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (
            Path(resource_root) if resource_root else bundled_resource_root()
        )
        self.templates_root = self.resource_root / "prompt_templates"
        self._state_lock = threading.Lock()
        self._active_groups: set[str] = set()

    @staticmethod
    def path_key(path: Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def current_task_path(self, group_root: Path) -> Path:
        group = self._validate_group_root(group_root)
        return group / self.DIRECTORY_NAME / self.CURRENT_TASK_NAME

    def prepare_materials(
        self,
        material_paths: Sequence[Path],
        progress: ProgressCallback | None = None,
    ) -> tuple[WorkflowMaterialInfo, ...]:
        self._emit(progress, "正在检查补充材料…")
        seen_sources: set[str] = set()
        seen_names: dict[str, Path] = {}
        materials: list[WorkflowMaterialInfo] = []
        reserved_names = {self.CURRENT_TASK_NAME.casefold()}

        for raw_path in material_paths:
            source = Path(raw_path).expanduser().resolve()
            source_key = self.path_key(source)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)

            if not source.exists():
                raise WorkflowOptimizationError(f"补充材料不存在：{source}")
            if source.is_symlink() or not source.is_file():
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

    def generate_task(
        self,
        group_root: Path,
        user_description: str,
        material_paths: Sequence[Path] = (),
        progress: ProgressCallback | None = None,
    ) -> WorkflowTaskResult:
        group = self._validate_group_root(group_root)
        description = user_description.strip()
        if not description:
            raise WorkflowOptimizationError("请填写优化说明。")

        group_key = self.path_key(group)
        with self._state_lock:
            if group_key in self._active_groups:
                raise WorkflowOptimizationError("当前项目组正在生成优化任务，请稍候。")
            self._active_groups.add(group_key)
        try:
            return self._generate_task(
                group, description, material_paths, progress
            )
        finally:
            with self._state_lock:
                self._active_groups.discard(group_key)

    def _generate_task(
        self,
        group: Path,
        description: str,
        material_paths: Sequence[Path],
        progress: ProgressCallback | None,
    ) -> WorkflowTaskResult:
        materials = self.prepare_materials(material_paths, progress)
        common_paths = self._common_tool_paths(group)
        optimization_root = self._prepare_optimization_root(group)
        current_task = optimization_root / self.CURRENT_TASK_NAME
        current_materials = optimization_root / self.MATERIALS_DIRECTORY_NAME
        history_root = optimization_root / self.HISTORY_DIRECTORY_NAME
        self._validate_managed_paths(current_task, current_materials, history_root)

        transaction_id = uuid4().hex
        staging = optimization_root / f".生成中-{transaction_id}"
        archive_staging = optimization_root / f".归档中-{transaction_id}"
        archived_path: Path | None = None
        moved_old: list[tuple[Path, Path]] = []
        promoted_new: list[tuple[Path, Path]] = []

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
            task_content = self._render_task(
                description, current_materials, copied_paths, common_paths
            )
            staged_task = staging / self.CURRENT_TASK_NAME
            staged_task.write_text(task_content.rstrip() + "\n", encoding="utf-8")

            has_previous = current_task.exists() or current_materials.exists()
            if has_previous:
                archive_staging.mkdir()
                self._emit(progress, "正在归档上一轮优化任务…")
                for source in (current_task, current_materials):
                    if source.exists():
                        destination = archive_staging / source.name
                        self._move_path(source, destination)
                        moved_old.append((destination, source))

            self._move_path(staged_materials, current_materials)
            promoted_new.append((current_materials, staged_materials))
            self._move_path(staged_task, current_task)
            promoted_new.append((current_task, staged_task))

            if has_previous:
                history_root.mkdir(exist_ok=True)
                archived_path = self._allocate_history_path(history_root)
                self._move_path(archive_staging, archived_path)

            self._emit(progress, "当前优化任务已生成")
            return WorkflowTaskResult(
                task_path=current_task.resolve(),
                material_paths=tuple(path.resolve() for path in copied_paths),
                archived_path=archived_path.resolve() if archived_path else None,
            )
        except Exception as exc:
            rollback_errors = self._rollback(
                promoted_new, moved_old, archived_path, archive_staging
            )
            if rollback_errors:
                detail = "；".join(rollback_errors)
                raise WorkflowOptimizationError(
                    f"生成失败且回滚未完整完成：{detail}"
                ) from exc
            if isinstance(exc, WorkflowOptimizationError):
                raise
            raise WorkflowOptimizationError(f"生成当前优化任务失败：{exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
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
        description: str,
        material_directory: Path,
        material_paths: Sequence[Path],
        common_paths: tuple[Path, Path, Path],
    ) -> str:
        template_path = self.templates_root / self.TEMPLATE_NAME
        if not template_path.is_file():
            raise WorkflowOptimizationError(f"缺少任务模板：{self.TEMPLATE_NAME}")
        template = template_path.read_text(encoding="utf-8")
        if material_paths:
            material_list = "\n".join(f"- {path.name}" for path in material_paths)
            material_guidance = (
                "开始前先枚举补充材料目录中的全部文件，并读取当前工具能够读取的材料。"
                "对无法读取的二进制文件，如实说明文件名和原因，不得假装已经读取。"
            )
        else:
            material_list = "本次未提供补充材料，请直接根据用户说明执行。"
            material_guidance = ""
        replacements = {
            "{{MATERIAL_DIRECTORY}}": str(material_directory.resolve()),
            "{{MATERIAL_LIST}}": material_list,
            "{{MATERIAL_GUIDANCE}}": material_guidance,
            "{{WORKFLOW_PATH}}": str(common_paths[0]),
            "{{TEMPLATE_PATH}}": str(common_paths[1]),
            "{{VALIDATOR_PATH}}": str(common_paths[2]),
        }
        for marker, value in replacements.items():
            template = template.replace(marker, value)
        return template.replace("{{USER_DESCRIPTION}}", description)

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
        current_task: Path, current_materials: Path, history_root: Path
    ) -> None:
        if current_task.exists() and (
            current_task.is_symlink() or not current_task.is_file()
        ):
            raise WorkflowOptimizationError(f"当前优化任务路径不是普通文件：{current_task}")
        for path in (current_materials, history_root):
            if path.exists() and (path.is_symlink() or not path.is_dir()):
                raise WorkflowOptimizationError(f"控制台管理路径不是安全目录：{path}")

    @staticmethod
    def _validate_group_root(group_root: Path) -> Path:
        group = Path(group_root).expanduser().resolve()
        if not group.is_dir():
            raise FileNotFoundError(f"项目组目录不存在：{group}")
        return group

    @staticmethod
    def _common_tool_paths(group: Path) -> tuple[Path, Path, Path]:
        paths = (
            group / "公共工具" / "WORKFLOW.md",
            group / "公共工具" / "template.html",
            group / "公共工具" / "validate-tool.js",
        )
        for path in paths:
            if not path.is_file():
                raise WorkflowOptimizationError(f"公共工具不存在：{path}")
        return tuple(path.resolve() for path in paths)

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
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()

    @staticmethod
    def _move_path(source: Path, destination: Path) -> None:
        source.replace(destination)

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str) -> None:
        if progress:
            progress(message)
