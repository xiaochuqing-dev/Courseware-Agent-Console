from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from models import ProjectEntry, ProjectGroup

from .feedback_service import FeedbackService, PendingFeedback
from .project_service import ProjectService
from .task_service import BatchTaskContext, PreparedTask, TaskService
from .task_types import TaskType


class BatchFeedbackError(RuntimeError):
    pass


class BatchPlanChangedError(BatchFeedbackError):
    pass


@dataclass(frozen=True, slots=True)
class BatchRoundTarget:
    project_id: str
    display_name: str
    project_path: Path
    latest_round: int | None
    target_round: int
    strategy: str

    @property
    def action_text(self) -> str:
        return f"新建第{self.target_round}轮"


@dataclass(frozen=True, slots=True)
class BatchFeedbackSaveResult:
    batch_id: str
    batch_directory: Path
    record_path: Path
    targets: tuple[BatchRoundTarget, ...]
    material_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BatchTaskGenerationResult:
    batch_id: str
    record_path: Path
    batch_task_path: Path
    project_task_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _PreparedMaterial:
    item: PendingFeedback
    name: str
    size: int
    sha256: str
    source_path: Path | None


ProgressCallback = Callable[[str], None]


class BatchFeedbackService:
    STRATEGY_NEXT = "next"
    # 仅用于识别旧版调用和记录；新批次不再允许追加历史轮次。
    STRATEGY_APPEND = "append"
    STRATEGIES = {STRATEGY_NEXT}
    DIRECTORY_NAME = "批量反馈"
    RECORD_NAME = "批量反馈记录.json"
    TASK_NAME = "批量反馈任务.md"
    WINDOWS_SAFE_PATH_LIMIT = 240
    BATCH_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")

    def __init__(
        self,
        project_service: ProjectService | None = None,
        feedback_service: FeedbackService | None = None,
        task_service: TaskService | None = None,
    ) -> None:
        self.project_service = project_service or ProjectService()
        self.feedback_service = feedback_service or FeedbackService()
        self.task_service = task_service or TaskService(
            self.project_service.resource_root
        )
        self._operation_lock = threading.Lock()

    def preview_rounds(
        self,
        group_root: Path,
        project_ids: Iterable[str],
        strategy: str = STRATEGY_NEXT,
    ) -> tuple[BatchRoundTarget, ...]:
        if strategy != self.STRATEGY_NEXT:
            raise BatchFeedbackError(
                "批量反馈固定为每个项目独立创建下一轮，不支持追加历史轮次。"
            )
        group = self.project_service.load_project_group(Path(group_root))
        selected = self._selected_projects(group, project_ids)
        targets: list[BatchRoundTarget] = []
        for project in selected:
            latest = self.feedback_service.latest_round(project.path)
            target = (latest or 0) + 1
            targets.append(
                BatchRoundTarget(
                    project_id=project.project_id,
                    display_name=project.display_name,
                    project_path=project.path.resolve(),
                    latest_round=latest,
                    target_round=int(target),
                    strategy=strategy,
                )
            )
        return tuple(targets)

    def save_batch(
        self,
        group_root: Path,
        project_ids: Iterable[str],
        strategy: str,
        items: Iterable[PendingFeedback],
        batch_note: str = "",
        project_hints: dict[str, str] | None = None,
        expected_targets: Iterable[BatchRoundTarget] | None = None,
        batch_id: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> BatchFeedbackSaveResult:
        if not self._operation_lock.acquire(blocking=False):
            raise BatchFeedbackError("已有批量反馈操作正在进行，请勿重复提交。")
        try:
            return self._save_batch_locked(
                group_root,
                project_ids,
                strategy,
                items,
                batch_note,
                project_hints or {},
                expected_targets,
                batch_id,
                progress,
            )
        finally:
            self._operation_lock.release()

    def generate_tasks(
        self,
        record_path: Path,
        progress: ProgressCallback | None = None,
    ) -> BatchTaskGenerationResult:
        if not self._operation_lock.acquire(blocking=False):
            raise BatchFeedbackError("已有批量反馈操作正在进行，请勿重复提交。")
        try:
            return self._generate_tasks_locked(Path(record_path), progress)
        finally:
            self._operation_lock.release()

    def batch_execution_instruction(self, record_path: Path) -> str:
        record_file, record = self._load_record(record_path)
        if record.get("task_generation_status") != "success":
            raise BatchFeedbackError("批量反馈任务尚未全部生成。")
        current_context_hash = self.task_service.batch_record_context_sha256(record)
        if current_context_hash != str(record.get("feedback_context_sha256", "")):
            raise BatchFeedbackError("批量反馈记录内容已变化，当前批量任务已失效。")
        task_path = Path(str(record.get("batch_task_path", ""))).resolve()
        if not task_path.is_file():
            raise FileNotFoundError(f"批量反馈任务文件不存在：{task_path}")
        expected_batch_hash = str(record.get("batch_task_sha256", ""))
        if not expected_batch_hash or self._file_sha256(task_path) != expected_batch_hash:
            raise BatchFeedbackError("批量反馈任务文件已变化，当前批量任务已失效。")
        for target in record.get("targets", []):
            if not isinstance(target, dict):
                raise BatchFeedbackError("批次记录中的项目数据无效。")
            task = Path(str(target.get("task_path", ""))).resolve()
            if not task.is_file():
                raise FileNotFoundError(f"项目当前任务不存在：{task}")
            expected_hash = str(target.get("task_sha256", ""))
            if not expected_hash or self._file_sha256(task) != expected_hash:
                raise BatchFeedbackError(
                    f"{target.get('display_name', task.parent.name)} 的当前任务已被改写，"
                    "批量任务已失效。"
                )
            snapshot = Path(str(target.get("task_snapshot_path", ""))).resolve()
            expected_snapshot_hash = str(target.get("task_snapshot_sha256", ""))
            if (
                not snapshot.is_file()
                or not expected_snapshot_hash
                or self._file_sha256(snapshot) != expected_snapshot_hash
            ):
                raise BatchFeedbackError(
                    f"{target.get('display_name', task.parent.name)} 的任务快照已变化，"
                    "批量任务已失效。"
                )
            project_path = Path(str(target.get("project_path", ""))).resolve()
            if task.parent != project_path:
                raise BatchFeedbackError("项目任务路径与批次记录不一致。")
            try:
                self.task_service.require_valid_current_task(
                    project_path,
                    expected_task_type=TaskType.FEEDBACK_MODIFICATION,
                    expected_round=int(target.get("target_round", 0)),
                    expected_special_requirements=str(
                        target.get("project_hint", "")
                    ),
                    expected_batch_id=str(record.get("batch_id", "")),
                )
            except Exception as exc:
                raise BatchFeedbackError(
                    f"{target.get('display_name', task.parent.name)} 的反馈任务已失效：{exc}"
                ) from exc
        if record_file.parent != task_path.parent:
            raise BatchFeedbackError("批次记录与批量任务文件不在同一目录。")
        return f"请读取并完整执行以下批量任务文件：\n\n{task_path}"

    def is_batch_instruction_valid(self, record_path: Path) -> bool:
        try:
            self.batch_execution_instruction(record_path)
        except Exception:
            return False
        return True

    def _save_batch_locked(
        self,
        group_root: Path,
        project_ids: Iterable[str],
        strategy: str,
        items: Iterable[PendingFeedback],
        batch_note: str,
        project_hints: dict[str, str],
        expected_targets: Iterable[BatchRoundTarget] | None,
        batch_id: str | None,
        progress: ProgressCallback | None,
    ) -> BatchFeedbackSaveResult:
        self._emit(progress, "正在重新校验项目和反馈轮次…")
        group = self.project_service.load_project_group(Path(group_root))
        targets = self.preview_rounds(group.root, project_ids, strategy)
        if expected_targets is not None and not self._same_plan(
            tuple(expected_targets), targets
        ):
            raise BatchPlanChangedError(
                "反馈轮次在预览后发生变化，已停止保存并刷新目标轮次。"
            )

        prepared_materials = self._prepare_materials(items)
        if not prepared_materials:
            raise BatchFeedbackError("请至少添加一项统一反馈材料。")
        batch_identifier = batch_id or self._new_batch_id()
        if not self.BATCH_ID_PATTERN.fullmatch(batch_identifier):
            raise BatchFeedbackError("批次 ID 格式无效。")
        batch_parent = group.root / self.DIRECTORY_NAME
        final_batch_dir = batch_parent / f"批次-{batch_identifier}"
        record_path = final_batch_dir / self.RECORD_NAME
        if final_batch_dir.exists():
            raise BatchFeedbackError(f"该批次已经存在，不能重复提交：{final_batch_dir}")

        hints = {
            target.project_id: str(project_hints.get(target.project_id, "")).strip()
            for target in targets
        }
        description_name = f"批量反馈说明-{batch_identifier}.txt"
        self._preflight_targets(
            group, targets, prepared_materials, description_name, final_batch_dir
        )

        batch_parent.mkdir(parents=True, exist_ok=True)
        staging_root = batch_parent / f".{batch_identifier}.saving-{uuid4().hex[:8]}"
        projects_stage = staging_root / "projects"
        staged_projects: list[tuple[BatchRoundTarget, Path, Path]] = []
        record: dict[str, object] = {}
        promotions: list[tuple[str, Path]] = []
        try:
            projects_stage.mkdir(parents=True)
            self._emit(progress, "正在暂存并校验共享反馈材料…")
            target_names = [target.display_name for target in targets]
            record_targets: list[dict[str, object]] = []
            for index, target in enumerate(targets, start=1):
                project_stage = projects_stage / f"p{index}"
                project_stage.mkdir()
                target_round = (
                    target.project_path / "客户反馈" / f"第{target.target_round}轮"
                )
                saved_paths: list[str] = []
                for material in prepared_materials:
                    staged_file = project_stage / material.name
                    self._copy_material(material, staged_file)
                    if (
                        staged_file.stat().st_size != material.size
                        or self._file_sha256(staged_file) != material.sha256
                    ):
                        raise BatchFeedbackError(
                            f"{target.display_name} 的材料复制校验失败：{material.name}"
                        )
                    saved_paths.append(str((target_round / material.name).resolve()))
                explanation = self._project_explanation(
                    batch_identifier,
                    target,
                    target_names,
                    batch_note,
                    hints[target.project_id],
                )
                explanation_path = project_stage / description_name
                explanation_path.write_text(explanation, encoding="utf-8")
                if explanation_path.read_text(encoding="utf-8") != explanation:
                    raise BatchFeedbackError(
                        f"{target.display_name} 的批量反馈说明校验失败。"
                    )
                saved_paths.append(str((target_round / description_name).resolve()))
                staged_projects.append((target, project_stage, target_round))
                record_targets.append(
                    {
                        "project_id": target.project_id,
                        "display_name": target.display_name,
                        "project_path": str(target.project_path),
                        "latest_round_before_submit": target.latest_round,
                        "target_round": target.target_round,
                        "project_hint": hints[target.project_id],
                        "saved_paths": saved_paths,
                        "status": "saved",
                        "feedback_save_status": "success",
                        "task_generation_status": "not_generated",
                        "task_path": "",
                        "task_sha256": "",
                        "task_snapshot_path": "",
                        "task_snapshot_sha256": "",
                    }
                )

            record = {
                "schema_version": 2,
                "batch_id": batch_identifier,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "group_id": group.group_id,
                "group_path": str(group.root.resolve()),
                "round_strategy": strategy,
                "shared_materials": [
                    {
                        "name": material.name,
                        "source_path": str(material.source_path or ""),
                        "size": material.size,
                        "sha256": material.sha256,
                    }
                    for material in prepared_materials
                ],
                "batch_note": batch_note.strip(),
                "targets": record_targets,
                "feedback_save_status": "success",
                "task_generation_status": "not_generated",
                "batch_task_path": "",
                "batch_task_sha256": "",
            }
            record["feedback_context_sha256"] = (
                self.task_service.batch_record_context_sha256(record)
            )
            (staging_root / self.RECORD_NAME).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            self._emit(progress, "正在提交各课件反馈轮次…")
            for target, project_stage, target_round in staged_projects:
                self._promote_new_round(project_stage, target_round)
                promotions.append(("directory", target_round))
            if projects_stage.exists():
                projects_stage.rmdir()
            staging_root.replace(final_batch_dir)
        except Exception as exc:
            rollback_errors = self._rollback_feedback_promotions(promotions)
            if staging_root.exists():
                shutil.rmtree(staging_root, ignore_errors=True)
            suffix = (
                "\n回滚异常：" + "；".join(rollback_errors) if rollback_errors else ""
            )
            if isinstance(exc, BatchFeedbackError):
                raise BatchFeedbackError(f"{exc}{suffix}") from exc
            raise BatchFeedbackError(f"批量反馈保存失败，已回滚：{exc}{suffix}") from exc

        self._emit(progress, "批量反馈已全部保存")
        return BatchFeedbackSaveResult(
            batch_id=batch_identifier,
            batch_directory=final_batch_dir.resolve(),
            record_path=record_path.resolve(),
            targets=targets,
            material_names=tuple(material.name for material in prepared_materials),
        )

    def _generate_tasks_locked(
        self, record_path: Path, progress: ProgressCallback | None
    ) -> BatchTaskGenerationResult:
        record_file, record = self._load_record(record_path)
        if record.get("feedback_save_status") != "success":
            raise BatchFeedbackError("批量反馈尚未完整保存，不能生成任务。")
        if self.task_service.batch_record_context_sha256(record) != str(
            record.get("feedback_context_sha256", "")
        ):
            raise BatchFeedbackError("批量反馈记录内容已变化，不能生成过期任务。")
        if record.get("task_generation_status") == "success":
            if self.is_batch_instruction_valid(record_file):
                return BatchTaskGenerationResult(
                    batch_id=str(record["batch_id"]),
                    record_path=record_file,
                    batch_task_path=Path(str(record["batch_task_path"])).resolve(),
                    project_task_paths=tuple(
                        Path(str(target["task_path"])).resolve()
                        for target in record.get("targets", [])
                    ),
                )
            raise BatchFeedbackError("批量任务记录已存在但任务文件失效，请开始新批次。")

        group_root = Path(str(record.get("group_path", ""))).resolve()
        group = self.project_service.load_project_group(group_root)
        projects_by_id = {project.project_id: project for project in group.projects}
        batch_id = str(record.get("batch_id", ""))
        targets = record.get("targets")
        if not isinstance(targets, list) or len(targets) < 2:
            raise BatchFeedbackError("批次记录缺少有效的目标课件。")

        self._emit(progress, "正在预检各课件反馈和当前任务…")
        prepared_tasks: list[tuple[dict, PreparedTask]] = []
        for target in targets:
            if not isinstance(target, dict):
                raise BatchFeedbackError("批次记录中的项目数据无效。")
            project_id = str(target.get("project_id", ""))
            project = projects_by_id.get(project_id)
            if project is None:
                raise BatchFeedbackError(
                    f"目标课件已被移动、删除或归档：{target.get('display_name', project_id)}"
                )
            recorded_path = Path(str(target.get("project_path", ""))).resolve()
            if project.path.resolve() != recorded_path:
                raise BatchFeedbackError(
                    f"目标课件路径已变化：{target.get('display_name', project.display_name)}"
                )
            self.project_service.validate_project_structure(group.root, project.path)
            round_number = int(target.get("target_round", 0))
            if self.feedback_service.latest_round(project.path) != round_number:
                raise BatchFeedbackError(
                    f"{project.display_name} 的最新反馈轮次已变化，不能生成过期批次任务。"
                )
            for raw_saved_path in target.get("saved_paths", []):
                saved_path = Path(str(raw_saved_path)).resolve()
                if not saved_path.is_file() or saved_path.is_symlink():
                    raise BatchFeedbackError(
                        f"{project.display_name} 的批量反馈材料缺失：{saved_path}"
                    )
            hint = str(target.get("project_hint", "")).strip()
            prepared = self.task_service.prepare_feedback_task(
                project.path,
                round_number,
                hint,
                batch_context=BatchTaskContext(record_file),
            )
            prepared_tasks.append((target, prepared))

        batch_task_path = record_file.parent / self.TASK_NAME
        if batch_task_path.exists():
            raise BatchFeedbackError(f"批量任务文件已存在，不能覆盖：{batch_task_path}")
        batch_task_content = self._batch_task_content(record, prepared_tasks)
        staged_files: list[tuple[Path, Path]] = []
        staged_hashes: dict[Path, str] = {}
        snapshots: dict[Path, bytes | None] = {}
        promoted: list[Path] = []
        staged_batch_task = record_file.parent / f".{self.TASK_NAME}.preparing-{uuid4().hex}"
        staged_record = record_file.parent / f".{self.RECORD_NAME}.preparing-{uuid4().hex}"
        original_record = record_file.read_bytes()
        try:
            self._emit(progress, "正在暂存并校验所有项目任务…")
            for _target, prepared in prepared_tasks:
                for destination, content in prepared.outputs():
                    temporary = destination.parent / (
                        f".{destination.name}.batch-{batch_id}-{uuid4().hex[:6]}"
                    )
                    temporary.write_text(content, encoding="utf-8")
                    if temporary.read_text(encoding="utf-8") != content:
                        raise BatchFeedbackError(f"任务暂存校验失败：{destination}")
                    staged_files.append((temporary, destination))
                    staged_hashes[destination] = self._file_sha256(temporary)
                    snapshots.setdefault(
                        destination,
                        destination.read_bytes() if destination.is_file() else None,
                    )

            staged_batch_task.write_text(batch_task_content, encoding="utf-8")
            batch_task_hash = self._file_sha256(staged_batch_task)
            for target, prepared in prepared_tasks:
                target["task_generation_status"] = "success"
                target["task_path"] = str(prepared.task_path.resolve())
                target["task_sha256"] = staged_hashes[prepared.task_path]
                target["task_snapshot_path"] = str(prepared.snapshot_path.resolve())
                target["task_snapshot_sha256"] = staged_hashes[
                    prepared.snapshot_path
                ]
            record["task_generation_status"] = "success"
            record["batch_task_path"] = str(batch_task_path.resolve())
            record["batch_task_sha256"] = batch_task_hash
            staged_record.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            self._emit(progress, "正在统一提交各课件任务…")
            for temporary, destination in staged_files:
                self._promote_file(temporary, destination, allow_replace=True)
                promoted.append(destination)
            self._promote_file(staged_batch_task, batch_task_path)
            promoted.append(batch_task_path)
            for target, prepared in prepared_tasks:
                validation = self.task_service.validate_current_task(
                    prepared.project_root,
                    expected_task_type=TaskType.FEEDBACK_MODIFICATION,
                    expected_round=prepared.feedback_round,
                    expected_special_requirements=prepared.special_requirements,
                    expected_batch_id=batch_id,
                )
                if not validation.valid:
                    raise BatchFeedbackError(
                        f"{target.get('display_name', prepared.project_root.name)} "
                        f"任务写入后校验失败：{validation.reason}"
                    )
            self._promote_file(staged_record, record_file, allow_replace=True)
            promoted.append(record_file)
        except Exception as exc:
            restore_errors: list[str] = []
            for destination in reversed(promoted):
                try:
                    if destination == record_file:
                        self._restore_bytes(record_file, original_record)
                    elif destination == batch_task_path:
                        destination.unlink(missing_ok=True)
                    else:
                        self._restore_bytes(destination, snapshots.get(destination))
                except Exception as rollback_exc:
                    restore_errors.append(f"{destination}：{rollback_exc}")
            suffix = (
                "\n回滚异常：" + "；".join(restore_errors) if restore_errors else ""
            )
            if isinstance(exc, BatchFeedbackError):
                raise BatchFeedbackError(f"{exc}{suffix}") from exc
            raise BatchFeedbackError(f"批量任务生成失败，已恢复原任务：{exc}{suffix}") from exc
        finally:
            for temporary, _destination in staged_files:
                temporary.unlink(missing_ok=True)
            staged_batch_task.unlink(missing_ok=True)
            staged_record.unlink(missing_ok=True)

        self._emit(progress, "所有课件反馈任务已生成")
        return BatchTaskGenerationResult(
            batch_id=batch_id,
            record_path=record_file,
            batch_task_path=batch_task_path.resolve(),
            project_task_paths=tuple(
                prepared.task_path.resolve() for _target, prepared in prepared_tasks
            ),
        )

    def _preflight_targets(
        self,
        group: ProjectGroup,
        targets: tuple[BatchRoundTarget, ...],
        materials: tuple[_PreparedMaterial, ...],
        description_name: str,
        final_batch_dir: Path,
    ) -> None:
        errors: list[str] = []
        for target in targets:
            try:
                self.project_service.validate_project_structure(
                    group.root, target.project_path
                )
                config = self.project_service.read_project_config(target.project_path)
                if str(config.get("project_id", "")) != target.project_id:
                    raise BatchFeedbackError("项目稳定 ID 与批次选择不一致。")
            except Exception as exc:
                errors.append(f"{target.display_name}：{exc}")
                continue
            feedback_root = target.project_path / "客户反馈"
            target_round = feedback_root / f"第{target.target_round}轮"
            if target.strategy == self.STRATEGY_NEXT and target_round.exists():
                errors.append(f"{target.display_name}：目标反馈轮次已存在：{target_round}")
                continue
            if target.strategy == self.STRATEGY_APPEND and not target_round.is_dir():
                errors.append(f"{target.display_name}：要追加的反馈轮次不存在：{target_round}")
                continue
            existing_names = {
                path.name.casefold()
                for path in target_round.iterdir()
            } if target_round.is_dir() else set()
            for name in [*(material.name for material in materials), description_name]:
                destination = target_round / name
                if name.casefold() in existing_names:
                    errors.append(
                        f"{target.display_name}：第{target.target_round}轮已存在同名文件 {name}"
                    )
                if self._path_too_long(destination):
                    errors.append(f"{target.display_name}：目标路径过长：{destination}")
            if not os.access(feedback_root, os.W_OK):
                errors.append(f"{target.display_name}：客户反馈目录不可写：{feedback_root}")
        for path in (final_batch_dir, final_batch_dir / self.RECORD_NAME):
            if self._path_too_long(path):
                errors.append(f"批次记录路径过长：{path}")
        if errors:
            raise BatchFeedbackError("批量保存预检失败：\n" + "\n".join(errors))

    def _prepare_materials(
        self, items: Iterable[PendingFeedback]
    ) -> tuple[_PreparedMaterial, ...]:
        candidates: list[tuple[PendingFeedback, str | None]] = []
        seen_paths: set[str] = set()
        for item in items:
            source_key: str | None = None
            if item.source_path is not None:
                raw = Path(item.source_path).expanduser()
                if raw.is_symlink():
                    raise BatchFeedbackError(
                        f"{item.name}：不支持符号链接，请选择真实反馈文件。"
                    )
                try:
                    source = raw.resolve(strict=True)
                except FileNotFoundError as exc:
                    raise BatchFeedbackError(f"{item.name}：源文件已不存在：{raw}") from exc
                if not source.is_file():
                    raise BatchFeedbackError(f"{item.name}：源路径不是普通文件：{source}")
                source_key = os.path.normcase(str(source))
                if source_key in seen_paths:
                    continue
                seen_paths.add(source_key)
            candidates.append((item, source_key))

        source_by_name: dict[str, str] = {}
        for item, source_key in candidates:
            key = item.name.casefold()
            identity = source_key or f"memory:{item.item_id}"
            previous = source_by_name.get(key)
            if previous is not None and previous != identity:
                raise BatchFeedbackError(
                    f"存在不同来源的同名材料：{item.name}。请先重命名源文件后重试。"
                )
            source_by_name[key] = identity

        prepared: list[_PreparedMaterial] = []
        seen_hashes: set[str] = set()
        for item, _source_key in candidates:
            source: Path | None = None
            if item.source_path is not None:
                source = Path(item.source_path).resolve(strict=True)
                try:
                    size = source.stat().st_size
                    if size <= 0:
                        raise BatchFeedbackError(f"{item.name}：源文件为空。")
                    with source.open("rb") as handle:
                        handle.read(1)
                except OSError as exc:
                    raise BatchFeedbackError(f"{item.name}：源文件不可读取：{source}") from exc
                suffix = source.suffix.casefold()
                if suffix not in self.feedback_service.SUPPORTED_SUFFIXES:
                    raise BatchFeedbackError(f"{item.name}：反馈文件格式不受支持。")
                if size > self.feedback_service._size_limit(suffix):
                    raise BatchFeedbackError(
                        f"{item.name}：文件超过 {self.feedback_service.format_size(self.feedback_service._size_limit(suffix))} 上限。"
                    )
                if suffix == ".docx":
                    self.feedback_service._validate_docx(source)
                digest = self._file_sha256(source)
                if item.fingerprint and digest != item.fingerprint:
                    raise BatchFeedbackError(
                        f"{item.name}：源文件在选择后发生变化，请重新添加。"
                    )
            elif item.content is not None:
                payload = bytes(item.content)
                if not payload:
                    raise BatchFeedbackError(f"{item.name}：待保存内容为空。")
                size = len(payload)
                digest = hashlib.sha256(payload).hexdigest().upper()
            else:
                raise BatchFeedbackError(f"{item.name}：反馈项没有可保存内容。")
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            prepared.append(
                _PreparedMaterial(
                    item=item,
                    name=Path(item.name).name,
                    size=size,
                    sha256=digest,
                    source_path=source,
                )
            )
        return tuple(prepared)

    @staticmethod
    def _selected_projects(
        group: ProjectGroup, project_ids: Iterable[str]
    ) -> tuple[ProjectEntry, ...]:
        requested = tuple(dict.fromkeys(str(value) for value in project_ids if value))
        if len(requested) < 2:
            raise BatchFeedbackError("批量反馈至少需要选择 2 个进行中课件。")
        projects_by_id = {project.project_id: project for project in group.projects}
        missing = [project_id for project_id in requested if project_id not in projects_by_id]
        if missing:
            raise BatchFeedbackError(
                "以下目标课件已不存在、被移动或归档：" + "、".join(missing)
            )
        requested_set = set(requested)
        return tuple(
            project for project in group.projects if project.project_id in requested_set
        )

    @staticmethod
    def _same_plan(
        expected: tuple[BatchRoundTarget, ...], actual: tuple[BatchRoundTarget, ...]
    ) -> bool:
        def normalized(values: tuple[BatchRoundTarget, ...]) -> dict[str, tuple]:
            return {
                value.project_id: (
                    str(value.project_path.resolve()),
                    value.latest_round,
                    value.target_round,
                    value.strategy,
                )
                for value in values
            }

        return normalized(expected) == normalized(actual)

    @staticmethod
    def _project_explanation(
        batch_id: str,
        target: BatchRoundTarget,
        target_names: list[str],
        batch_note: str,
        project_hint: str,
    ) -> str:
        names = "、".join(target_names)
        return (
            "批量反馈边界说明\n\n"
            f"批次 ID：{batch_id}\n"
            f"当前课件：{target.display_name}\n"
            f"项目稳定 ID：{target.project_id}\n"
            f"本次目标轮次：第{target.target_round}轮\n"
            f"本批次全部目标课件：{names}\n"
            f"批量补充说明：{batch_note.strip() or '无'}\n"
            f"本课件提示：{project_hint.strip() or '无'}\n\n"
            "只处理能够明确归属于当前课件的反馈。不得猜测，不得把其他课件的修改要求应用到当前项目；"
            "无法归属的内容必须明确报告。原始 Word 是权威材料，无法读取时必须如实说明。\n"
        )

    @staticmethod
    def _batch_task_content(
        record: dict, prepared_tasks: list[tuple[dict, PreparedTask]]
    ) -> str:
        materials = "、".join(
            str(item.get("name", ""))
            for item in record.get("shared_materials", [])
            if isinstance(item, dict)
        ) or "无"
        lines = [
            "# 批量反馈执行任务",
            "",
            "## 批次信息",
            "",
            f"- 批次 ID：{record.get('batch_id', '')}",
            f"- 项目组：{record.get('group_path', '')}",
            f"- 课件数量：{len(prepared_tasks)}",
            f"- 反馈材料：{materials}",
            "",
            "## 执行项目",
            "",
        ]
        for index, (target, prepared) in enumerate(prepared_tasks, start=1):
            lines.extend(
                [
                    f"{index}. {target.get('display_name', prepared.project_root.name)}",
                    f"   - 本次反馈轮次：第{int(target.get('target_round', 0))}轮",
                    f"   - 任务文件：{prepared.task_path.resolve()}",
                    "",
                ]
            )
        lines.extend(
            [
                "## 执行边界",
                "",
                "- 必须逐个项目读取并完整执行各自的“当前任务.md”。",
                "- 每个项目独立修改、独立输出、独立验证、独立记录。",
                "- 不得把一个项目的反馈、源文件、产品或修改结果写入另一个项目。",
                "- 完成一个项目后再处理下一个项目。",
                "- 某个项目失败时应明确报告，不得把其他项目标记为已完成。",
                "- 不得合并不同项目的产品文件或反馈轮次。",
                "",
            ]
        )
        return "\n".join(lines)

    def _load_record(self, record_path: Path) -> tuple[Path, dict]:
        path = Path(record_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"批量反馈记录不存在：{path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BatchFeedbackError(f"批量反馈记录无法读取：{path}") from exc
        if not isinstance(record, dict) or not record.get("batch_id"):
            raise BatchFeedbackError("批量反馈记录格式无效。")
        group_root = Path(str(record.get("group_path", ""))).resolve()
        expected_root = group_root / self.DIRECTORY_NAME
        try:
            path.relative_to(expected_root.resolve())
        except ValueError as exc:
            raise BatchFeedbackError("批量反馈记录不在登记的项目组目录内。") from exc
        return path, record

    def _copy_material(self, material: _PreparedMaterial, destination: Path) -> None:
        if material.source_path is not None:
            shutil.copy2(material.source_path, destination)
        elif material.item.content is not None:
            destination.write_bytes(material.item.content)
        else:
            raise BatchFeedbackError(f"{material.name}：反馈项没有可保存内容。")

    @staticmethod
    def _promote_new_round(staged_directory: Path, destination: Path) -> None:
        if destination.exists():
            raise FileExistsError(f"目标反馈轮次已存在：{destination}")
        staged_directory.replace(destination)

    @staticmethod
    def _promote_file(
        staged_file: Path, destination: Path, allow_replace: bool = False
    ) -> None:
        if destination.exists() and not allow_replace:
            raise FileExistsError(f"目标文件已存在：{destination}")
        staged_file.replace(destination)

    @staticmethod
    def _rollback_feedback_promotions(
        promotions: list[tuple[str, Path]]
    ) -> list[str]:
        errors: list[str] = []
        for kind, path in reversed(promotions):
            try:
                if kind == "directory":
                    if path.exists():
                        shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except Exception as exc:
                errors.append(f"{path}：{exc}")
        return errors

    @staticmethod
    def _restore_bytes(path: Path, content: bytes | None) -> None:
        if content is None:
            path.unlink(missing_ok=True)
            return
        temporary = path.parent / f".{path.name}.restoring-{uuid4().hex}"
        temporary.write_bytes(content)
        temporary.replace(path)

    def _path_too_long(self, path: Path) -> bool:
        return len(str(path.absolute())) >= self.WINDOWS_SAFE_PATH_LIMIT

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest().upper()

    @staticmethod
    def _new_batch_id() -> str:
        return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str) -> None:
        if progress:
            progress(message)
