from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .archive_service import ArchiveService
from .feedback_service import FeedbackService
from .identity_service import file_sha256, read_courseware_meta
from .project_service import ProjectService
from .prompt_service import PromptService
from .resource_paths import bundled_resource_root
from .task_types import TaskType


@dataclass(frozen=True, slots=True)
class BatchTaskContext:
    record_path: Path


@dataclass(frozen=True, slots=True)
class PreparedTask:
    project_root: Path
    task_path: Path
    snapshot_path: Path
    config_path: Path
    task_content: str
    snapshot_content: str
    config_content: str
    task_type: TaskType
    feedback_round: int
    special_requirements: str
    batch_id: str = ""

    def outputs(self) -> tuple[tuple[Path, str], ...]:
        return (
            (self.config_path, self.config_content),
            (self.task_path, self.task_content),
            (self.snapshot_path, self.snapshot_content),
        )


@dataclass(frozen=True, slots=True)
class TaskValidationResult:
    valid: bool
    exists: bool
    reason: str = ""
    task_type: TaskType | None = None
    feedback_round: int = 0
    metadata: dict[str, Any] | None = None

    @property
    def status_text(self) -> str:
        if not self.exists:
            return "尚未生成任务"
        if not self.valid:
            suffix = f"：{self.reason}" if self.reason else ""
            return f"当前任务已过期，需要重新生成{suffix}"
        if self.task_type is TaskType.FEEDBACK_MODIFICATION:
            return f"已有第{self.feedback_round}轮反馈修改任务"
        return "已有首次制作任务"


class TaskService:
    SNAPSHOT_NAME = "当前任务快照.json"
    SNAPSHOT_SCHEMA_VERSION = 2
    UNRESOLVED_MARKERS = ("{{", "}}")

    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (
            Path(resource_root)
            if resource_root
            else bundled_resource_root()
        )
        self.templates_root = self.resource_root / "prompt_templates"
        self.project_service = ProjectService(self.resource_root)
        self.archive_service = ArchiveService(self.project_service)
        self.feedback_service = FeedbackService()

    def generate_first_build_task(
        self, project_root: Path, special_requirements: str
    ) -> Path:
        prepared = self.prepare_first_build_task(project_root, special_requirements)
        self.commit_prepared_task(prepared)
        return prepared.task_path

    def prepare_first_build_task(
        self, project_root: Path, special_requirements: str
    ) -> PreparedTask:
        project_path = self._validated_project_path(project_root)
        config, artifact = self._prepare_artifact(project_path, 0, 0)
        special = self._normalize_special_requirements(special_requirements)
        generated_at = self._now()
        original_materials = self._snapshot_files(project_path / "原始需求")
        if not original_materials:
            raise ValueError("原始需求目录没有可用材料，不能生成首次制作任务。")
        output = self._output_snapshot(project_path, artifact)
        context = self._input_context(
            project_path=project_path,
            config=config,
            task_type=TaskType.FIRST_BUILD,
            feedback_round=0,
            special_requirements=special,
            output=output,
            original_materials=original_materials,
            feedback_materials=(),
            feedback_directory="",
            baseline_product=None,
            batch_context=None,
        )
        input_hash = self._canonical_sha256(context)
        template = self._read_template("first_build_task.md")
        replacements = {
            "{{PROJECT_NAME}}": str(config["display_name"]),
            "{{PROJECT_ID}}": str(config["project_id"]),
            "{{ARTIFACT_ID}}": str(artifact["artifact_id"]),
            "{{EXPECTED_OUTPUT}}": str(output["relative_path"]),
            "{{EXPECTED_OUTPUT_ABSOLUTE}}": str(output["path"]),
            "{{VERSION_NUMBER}}": "0",
            "{{FEEDBACK_ROUND_NUMBER}}": "0",
            "{{GENERATED_AT}}": generated_at,
            "{{INPUT_SNAPSHOT_SHA256}}": input_hash,
            "{{SPECIAL_REQUIREMENTS}}": special,
            "{{ORIGINAL_MATERIALS}}": self._format_materials(original_materials),
            "{{BINDING_BLOCK}}": self._binding_block(project_path),
        }
        content = self._replace_template(template, replacements)
        return self._prepared_task(
            project_path,
            content,
            config,
            TaskType.FIRST_BUILD,
            0,
            special,
            generated_at,
            input_hash,
            context,
        )

    def generate_feedback_task(
        self,
        project_root: Path,
        round_number: int,
        special_requirements: str,
        batch_context: BatchTaskContext | None = None,
    ) -> Path:
        prepared = self.prepare_feedback_task(
            project_root,
            round_number,
            special_requirements,
            batch_context=batch_context,
        )
        self.commit_prepared_task(prepared)
        return prepared.task_path

    def prepare_feedback_task(
        self,
        project_root: Path,
        round_number: int,
        special_requirements: str,
        batch_context: BatchTaskContext | None = None,
    ) -> PreparedTask:
        project_path = self._validated_project_path(project_root)
        if round_number <= 0:
            raise ValueError("反馈轮次必须大于 0。")
        feedback_round = project_path / "客户反馈" / f"第{round_number}轮"
        if not feedback_round.is_dir():
            raise FileNotFoundError(f"客户反馈轮次不存在：第{round_number}轮")
        feedback_materials = self._snapshot_files(feedback_round, feedback=True)
        if not feedback_materials:
            raise ValueError(
                f"客户反馈第{round_number}轮没有任何有效材料，不能生成反馈修改任务。"
            )

        current_config = self.project_service.read_project_config(project_path)
        baseline = self._baseline_product_snapshot(project_path, current_config)
        if baseline is None:
            raise FileNotFoundError(
                "当前项目没有最新有效产品，不能生成反馈修改任务。请先完成首次制作。"
            )

        config, artifact = self._prepare_feedback_artifact(
            project_path, round_number
        )
        output = self._output_snapshot(project_path, artifact)
        if Path(str(baseline["path"])).resolve() == Path(str(output["path"])).resolve():
            raise FileExistsError(
                "本轮预期输出已经是当前最新产品，不能覆盖历史版本。请创建新的反馈轮次。"
            )
        if Path(str(output["path"])).exists():
            raise FileExistsError(f"反馈任务预期输出已经存在，不能覆盖：{output['path']}")
        special = self._normalize_special_requirements(special_requirements)
        generated_at = self._now()
        original_materials = self._snapshot_files(project_path / "原始需求")
        batch_snapshot = (
            self._batch_context_snapshot(
                Path(batch_context.record_path),
                str(config["project_id"]),
                round_number,
            )
            if batch_context is not None
            else None
        )
        context = self._input_context(
            project_path=project_path,
            config=config,
            task_type=TaskType.FEEDBACK_MODIFICATION,
            feedback_round=round_number,
            special_requirements=special,
            output=output,
            original_materials=original_materials,
            feedback_materials=feedback_materials,
            feedback_directory=str(feedback_round.resolve()),
            baseline_product=baseline,
            batch_context=batch_snapshot,
        )
        input_hash = self._canonical_sha256(context)
        template = self._read_template("feedback_task.md")
        replacements = {
            "{{PROJECT_NAME}}": str(config["display_name"]),
            "{{PROJECT_ID}}": str(config["project_id"]),
            "{{ARTIFACT_ID}}": str(artifact["artifact_id"]),
            "{{EXPECTED_OUTPUT}}": str(output["relative_path"]),
            "{{EXPECTED_OUTPUT_ABSOLUTE}}": str(output["path"]),
            "{{VERSION_NUMBER}}": str(artifact["version_number"]),
            "{{FEEDBACK_ROUND_NUMBER}}": str(round_number),
            "{{FEEDBACK_ROUND}}": f"第{round_number}轮",
            "{{FEEDBACK_DIRECTORY}}": str(feedback_round.resolve()),
            "{{GENERATED_AT}}": generated_at,
            "{{INPUT_SNAPSHOT_SHA256}}": input_hash,
            "{{SPECIAL_REQUIREMENTS}}": special,
            "{{FEEDBACK_MATERIALS}}": self._format_materials(feedback_materials),
            "{{BATCH_CONTEXT}}": self._format_batch_context(batch_snapshot),
            "{{BASELINE_PRODUCT_NAME}}": str(baseline["name"]),
            "{{BASELINE_PRODUCT_PATH}}": str(baseline["path"]),
            "{{BASELINE_PRODUCT_VERSION}}": str(baseline["version_number"]),
            "{{BASELINE_PRODUCT_ARTIFACT_ID}}": str(
                baseline.get("artifact_id") or "未登记（兼容旧项目）"
            ),
            "{{BASELINE_PRODUCT_SHA256}}": str(baseline["sha256"]),
            "{{PROJECT_RECORD_PATH}}": str((project_path / "项目记录.md").resolve()),
            "{{ORIGINAL_REQUIREMENTS_PATH}}": str(
                (project_path / "原始需求").resolve()
            ),
            "{{BINDING_BLOCK}}": self._binding_block(project_path),
        }
        content = self._replace_template(template, replacements)
        return self._prepared_task(
            project_path,
            content,
            config,
            TaskType.FEEDBACK_MODIFICATION,
            round_number,
            special,
            generated_at,
            input_hash,
            context,
            batch_id=str(batch_snapshot.get("batch_id", "")) if batch_snapshot else "",
        )

    def commit_prepared_task(self, prepared: PreparedTask) -> None:
        targets = prepared.outputs()
        snapshots = {
            path: path.read_bytes() if path.is_file() else None for path, _ in targets
        }
        staged: list[tuple[Path, Path]] = []
        promoted: list[Path] = []
        try:
            for target, content in targets:
                temporary = target.parent / f".{target.name}.preparing-{uuid4().hex}"
                temporary.write_text(content, encoding="utf-8")
                if temporary.read_text(encoding="utf-8") != content:
                    raise OSError(f"任务暂存校验失败：{target}")
                staged.append((temporary, target))
            for temporary, target in staged:
                temporary.replace(target)
                promoted.append(target)
            validation = self.validate_current_task(
                prepared.project_root,
                expected_task_type=prepared.task_type,
                expected_round=prepared.feedback_round,
                expected_special_requirements=prepared.special_requirements,
                expected_batch_id=prepared.batch_id or None,
            )
            if not validation.valid:
                raise ValueError(f"任务写入后自校验失败：{validation.reason}")
        except Exception:
            for target in reversed(promoted):
                self._restore_file(target, snapshots[target])
            raise
        finally:
            for temporary, _target in staged:
                temporary.unlink(missing_ok=True)

    def validate_current_task(
        self,
        project_root: Path,
        expected_task_type: TaskType | None = None,
        expected_round: int | None = None,
        expected_special_requirements: str | None = None,
        expected_batch_id: str | None = None,
    ) -> TaskValidationResult:
        project_path = Path(project_root).expanduser().resolve()
        task_path = project_path / "当前任务.md"
        snapshot_path = project_path / self.SNAPSHOT_NAME
        if not task_path.is_file():
            return TaskValidationResult(False, False, "尚未生成任务")
        try:
            if not task_path.read_bytes().strip():
                return TaskValidationResult(False, False, "尚未生成任务")
        except OSError as exc:
            return TaskValidationResult(False, True, f"当前任务无法读取：{exc}")
        if not snapshot_path.is_file():
            return TaskValidationResult(
                False,
                True,
                "旧版任务缺少结构化快照",
                self._task_type_from_text(self._safe_read_text(task_path)),
            )
        try:
            content = task_path.read_text(encoding="utf-8")
            metadata = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
            if not isinstance(metadata, dict):
                raise ValueError("任务快照格式无效")
            task_type = TaskType(str(metadata.get("task_type", "")))
            feedback_round = int(metadata.get("feedback_round", 0))
            self._validate_metadata_identity(project_path, metadata)
            if int(metadata.get("schema_version", 0)) != self.SNAPSHOT_SCHEMA_VERSION:
                raise ValueError("任务快照版本过旧")
            expected_task_hash = str(metadata.get("task_sha256", ""))
            if not expected_task_hash or self._text_sha256(content) != expected_task_hash:
                raise ValueError("当前任务文件自身哈希不匹配")
            if any(marker in content for marker in self.UNRESOLVED_MARKERS):
                raise ValueError("任务中仍有未替换模板变量")
            self._validate_content_markers(content, task_type, feedback_round)
            if expected_task_type is not None and task_type is not expected_task_type:
                raise ValueError(
                    f"当前选择为{expected_task_type.display_name}，任务实际为{task_type.display_name}"
                )
            if expected_round is not None and feedback_round != int(expected_round):
                raise ValueError(
                    f"当前选择第{int(expected_round)}轮，任务绑定第{feedback_round}轮"
                )
            stored_special = str(metadata.get("special_requirements", "无"))
            if expected_special_requirements is not None:
                expected_special = self._normalize_special_requirements(
                    expected_special_requirements
                )
                if stored_special != expected_special:
                    raise ValueError("特殊要求已变化")
            stored_batch_id = str(metadata.get("batch_id", ""))
            if expected_batch_id is not None and stored_batch_id != expected_batch_id:
                raise ValueError("批次 ID 与当前批次不一致")
            current_context = self._current_input_context(project_path, metadata)
            current_hash = self._canonical_sha256(current_context)
            if current_hash != str(metadata.get("input_snapshot_sha256", "")):
                raise ValueError(self._context_change_reason(metadata, current_context))
            return TaskValidationResult(
                True,
                True,
                task_type=task_type,
                feedback_round=feedback_round,
                metadata=metadata,
            )
        except Exception as exc:
            metadata = self._safe_read_json(snapshot_path)
            task_type = None
            feedback_round = 0
            try:
                task_type = TaskType(str(metadata.get("task_type", "")))
                feedback_round = int(metadata.get("feedback_round", 0))
            except Exception:
                task_type = self._task_type_from_text(self._safe_read_text(task_path))
            return TaskValidationResult(
                False,
                True,
                str(exc),
                task_type,
                feedback_round,
                metadata,
            )

    def require_valid_current_task(
        self,
        project_root: Path,
        expected_task_type: TaskType | None = None,
        expected_round: int | None = None,
        expected_special_requirements: str | None = None,
        expected_batch_id: str | None = None,
    ) -> TaskValidationResult:
        result = self.validate_current_task(
            project_root,
            expected_task_type,
            expected_round,
            expected_special_requirements,
            expected_batch_id,
        )
        if not result.valid:
            raise ValueError(result.status_text)
        return result

    @staticmethod
    def validate_prepared_feedback_task(
        prepared: PreparedTask, round_number: int
    ) -> None:
        required = (
            "任务类型：反馈修改",
            f"反馈轮次：第{round_number}轮",
            "项目 ID：",
            "输入快照 SHA-256：",
            "## 本轮反馈材料",
        )
        missing = [marker for marker in required if marker not in prepared.task_content]
        if missing or "{{" in prepared.task_content:
            detail = "、".join(missing) or "存在未替换模板变量"
            raise ValueError(f"反馈任务内容校验失败：{detail}")

    def execution_prompt(self, project_name: str) -> str:
        return PromptService(self.resource_root).execution_prompt(project_name)

    def read_rules(self, group_root: Path) -> str:
        return (Path(group_root) / "AGENT任务规则.md").read_text(encoding="utf-8")

    def save_rules(self, group_root: Path, content: str) -> None:
        target = Path(group_root) / "AGENT任务规则.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)

    def restore_default_rules(self, group_root: Path) -> str:
        content = self._read_template("AGENT任务规则.md")
        self.save_rules(group_root, content)
        return content

    @classmethod
    def batch_record_context_sha256(cls, record: dict[str, Any]) -> str:
        targets: list[dict[str, Any]] = []
        for raw in record.get("targets", []):
            if not isinstance(raw, dict):
                continue
            targets.append(
                {
                    "project_id": str(raw.get("project_id", "")),
                    "display_name": str(raw.get("display_name", "")),
                    "project_path": str(raw.get("project_path", "")),
                    "latest_round_before_submit": raw.get(
                        "latest_round_before_submit"
                    ),
                    "target_round": int(raw.get("target_round", 0)),
                    "project_hint": str(raw.get("project_hint", "")),
                    "saved_paths": [str(value) for value in raw.get("saved_paths", [])],
                }
            )
        shared_materials = []
        for raw in record.get("shared_materials", []):
            if isinstance(raw, dict):
                shared_materials.append(
                    {
                        "name": str(raw.get("name", "")),
                        "size": int(raw.get("size", 0)),
                        "sha256": str(raw.get("sha256", "")),
                    }
                )
        semantic = {
            "schema_version": int(record.get("schema_version", 0)),
            "batch_id": str(record.get("batch_id", "")),
            "group_id": str(record.get("group_id", "")),
            "group_path": str(record.get("group_path", "")),
            "round_strategy": str(record.get("round_strategy", "next")),
            "batch_note": str(record.get("batch_note", "")),
            "shared_materials": shared_materials,
            "targets": targets,
        }
        return cls._canonical_sha256(semantic)

    def _validated_project_path(self, project_root: Path) -> Path:
        project_path = Path(project_root).expanduser().resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        self.project_service.validate_project_structure(
            project_path.parent, project_path
        )
        return project_path

    def _read_template(self, name: str) -> str:
        path = self.templates_root / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板：{name}")
        return path.read_text(encoding="utf-8")

    def _prepare_artifact(
        self, project_path: Path, version_number: int, feedback_round: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        config = copy.deepcopy(self.project_service.read_project_config(project_path))
        artifacts = config.setdefault("artifacts", [])
        if not isinstance(artifacts, list):
            artifacts = []
            config["artifacts"] = artifacts
        for artifact in artifacts:
            if (
                isinstance(artifact, dict)
                and int(artifact.get("version_number", -1)) == version_number
            ):
                return config, artifact
        expected = self.archive_service.expected_product_name(config, version_number)
        artifact = {
            "artifact_id": str(uuid4()),
            "project_id": str(config["project_id"]),
            "type": "courseware_html",
            "version_number": version_number,
            "feedback_round": feedback_round,
            "expected_name": expected,
            "current_name": expected,
            "sha256": "",
            "created_at": self._now(),
            "aliases": [],
            "ignored_names": [],
        }
        artifacts.append(artifact)
        return config, artifact

    def _prepare_feedback_artifact(
        self, project_path: Path, feedback_round: int
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        config = copy.deepcopy(self.project_service.read_project_config(project_path))
        artifacts = config.setdefault("artifacts", [])
        if not isinstance(artifacts, list):
            artifacts = []
            config["artifacts"] = artifacts

        matching = sorted(
            (
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict)
                and int(artifact.get("feedback_round", -1)) == feedback_round
            ),
            key=lambda artifact: int(artifact.get("version_number", -1)),
            reverse=True,
        )
        for artifact in matching:
            if not self._artifact_product_exists(project_path, artifact):
                return config, artifact

        used_versions = {
            int(artifact.get("version_number", -1))
            for artifact in artifacts
            if isinstance(artifact, dict)
        }
        version_number = feedback_round
        if version_number in used_versions:
            version_number = max([0, *used_versions]) + 1
        while True:
            expected = self.archive_service.expected_product_name(config, version_number)
            if (
                version_number not in used_versions
                and not (project_path / "产品迭代" / expected).exists()
            ):
                break
            version_number += 1
        artifact = {
            "artifact_id": str(uuid4()),
            "project_id": str(config["project_id"]),
            "type": "courseware_html",
            "version_number": version_number,
            "feedback_round": feedback_round,
            "expected_name": expected,
            "current_name": expected,
            "sha256": "",
            "created_at": self._now(),
            "aliases": [],
            "ignored_names": [],
        }
        artifacts.append(artifact)
        return config, artifact

    @staticmethod
    def _artifact_product_exists(
        project_path: Path, artifact: dict[str, Any]
    ) -> bool:
        product_root = project_path / "产品迭代"
        if not product_root.is_dir():
            return False
        artifact_id = str(artifact.get("artifact_id", ""))
        recorded_hash = str(artifact.get("sha256", ""))
        names = {
            str(artifact.get("expected_name", "")),
            str(artifact.get("current_name", "")),
            *(str(value) for value in artifact.get("aliases", [])),
        }
        for product in product_root.iterdir():
            if not product.is_file() or product.suffix.casefold() != ".html":
                continue
            if product.name in names:
                return True
            meta = read_courseware_meta(product)
            if artifact_id and str(meta.get("courseware-artifact-id", "")) == artifact_id:
                return True
            if recorded_hash and file_sha256(product) == recorded_hash:
                return True
        return False

    def _prepared_task(
        self,
        project_path: Path,
        content: str,
        config: dict[str, Any],
        task_type: TaskType,
        feedback_round: int,
        special_requirements: str,
        generated_at: str,
        input_hash: str,
        context: dict[str, Any],
        batch_id: str = "",
    ) -> PreparedTask:
        task_content = content.rstrip() + "\n"
        metadata = {
            "schema_version": self.SNAPSHOT_SCHEMA_VERSION,
            "task_type": task_type.value,
            "task_type_display": task_type.display_name,
            "project_id": str(config["project_id"]),
            "project_path": str(project_path.resolve()),
            "feedback_round": feedback_round,
            "special_requirements": special_requirements,
            "generated_at": generated_at,
            "input_snapshot_sha256": input_hash,
            "task_sha256": self._text_sha256(task_content),
            "task_path": str((project_path / "当前任务.md").resolve()),
            "snapshot_path": str((project_path / self.SNAPSHOT_NAME).resolve()),
            "batch_id": batch_id,
            "input_context": context,
        }
        snapshot_content = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
        config_content = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        prepared = PreparedTask(
            project_root=project_path,
            task_path=project_path / "当前任务.md",
            snapshot_path=project_path / self.SNAPSHOT_NAME,
            config_path=project_path / self.project_service.PROJECT_CONFIG_NAME,
            task_content=task_content,
            snapshot_content=snapshot_content,
            config_content=config_content,
            task_type=task_type,
            feedback_round=feedback_round,
            special_requirements=special_requirements,
            batch_id=batch_id,
        )
        self._validate_prepared_content(prepared)
        return prepared

    def _validate_prepared_content(self, prepared: PreparedTask) -> None:
        metadata = json.loads(prepared.snapshot_content)
        if self._text_sha256(prepared.task_content) != metadata.get("task_sha256"):
            raise ValueError("任务暂存哈希校验失败。")
        self._validate_content_markers(
            prepared.task_content, prepared.task_type, prepared.feedback_round
        )
        if any(marker in prepared.task_content for marker in self.UNRESOLVED_MARKERS):
            raise ValueError("任务中存在未替换模板变量。")
        if prepared.task_type is TaskType.FEEDBACK_MODIFICATION:
            self.validate_prepared_feedback_task(prepared, prepared.feedback_round)

    def _input_context(
        self,
        *,
        project_path: Path,
        config: dict[str, Any],
        task_type: TaskType,
        feedback_round: int,
        special_requirements: str,
        output: dict[str, Any],
        original_materials: tuple[dict[str, Any], ...],
        feedback_materials: tuple[dict[str, Any], ...],
        feedback_directory: str,
        baseline_product: dict[str, Any] | None,
        batch_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        record_path = project_path / "项目记录.md"
        record_snapshot = {
            "path": str(record_path.resolve()),
            "sha256": file_sha256(record_path) if record_path.is_file() else "",
        }
        return {
            "task_type": task_type.value,
            "project": {
                "display_name": str(config.get("display_name", project_path.name)),
                "project_id": str(config["project_id"]),
                "path": str(project_path.resolve()),
                "config_sha256": self._canonical_sha256(config),
            },
            "feedback_round": feedback_round,
            "feedback_directory": feedback_directory,
            "special_requirements": special_requirements,
            "original_requirements": list(original_materials),
            "feedback_materials": list(feedback_materials),
            "batch_context": batch_context,
            "baseline_product": baseline_product,
            "project_record": record_snapshot,
            "output": output,
            "tool_bindings": list(self._tool_snapshots(project_path)),
        }

    def _current_input_context(
        self, project_path: Path, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        stored = metadata.get("input_context")
        if not isinstance(stored, dict):
            raise ValueError("任务快照缺少 input_context")
        task_type = TaskType(str(metadata.get("task_type", "")))
        feedback_round = int(metadata.get("feedback_round", 0))
        config = self.project_service.read_project_config(project_path)
        output = stored.get("output")
        if not isinstance(output, dict):
            raise ValueError("任务快照缺少预期输出身份")
        self._validate_output_path(project_path, output)
        original_materials = self._snapshot_files(project_path / "原始需求")
        if task_type is TaskType.FEEDBACK_MODIFICATION:
            feedback_root = project_path / "客户反馈" / f"第{feedback_round}轮"
            if not feedback_root.is_dir():
                raise FileNotFoundError(f"反馈目录已不存在：{feedback_root}")
            feedback_materials = self._snapshot_files(feedback_root, feedback=True)
            if not feedback_materials:
                raise ValueError("当前反馈轮次已无有效材料")
            baseline = self._baseline_product_snapshot(project_path, config)
            if baseline is None:
                raise FileNotFoundError("最新有效产品已不存在")
            stored_batch = stored.get("batch_context")
            batch_snapshot = None
            if isinstance(stored_batch, dict):
                record_path = Path(str(stored_batch.get("record_path", "")))
                batch_snapshot = self._batch_context_snapshot(
                    record_path,
                    str(config["project_id"]),
                    feedback_round,
                )
            return self._input_context(
                project_path=project_path,
                config=config,
                task_type=task_type,
                feedback_round=feedback_round,
                special_requirements=str(metadata.get("special_requirements", "无")),
                output=output,
                original_materials=original_materials,
                feedback_materials=feedback_materials,
                feedback_directory=str(feedback_root.resolve()),
                baseline_product=baseline,
                batch_context=batch_snapshot,
            )
        return self._input_context(
            project_path=project_path,
            config=config,
            task_type=task_type,
            feedback_round=0,
            special_requirements=str(metadata.get("special_requirements", "无")),
            output=output,
            original_materials=original_materials,
            feedback_materials=(),
            feedback_directory="",
            baseline_product=None,
            batch_context=None,
        )

    def _snapshot_files(
        self, root: Path, feedback: bool = False
    ) -> tuple[dict[str, Any], ...]:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            return ()
        snapshots: list[dict[str, Any]] = []
        for path in sorted(
            (item for item in root_path.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(root_path).as_posix().casefold(),
        ):
            if path.is_symlink():
                raise ValueError(f"任务输入不允许符号链接：{path}")
            resolved = path.resolve()
            try:
                resolved.relative_to(root_path)
            except ValueError as exc:
                raise ValueError(f"任务输入越出登记目录：{path}") from exc
            size = resolved.stat().st_size
            relative = resolved.relative_to(root_path).as_posix()
            suffix = resolved.suffix.casefold()
            snapshots.append(
                {
                    "file_name": resolved.name,
                    "relative_path": relative,
                    "kind": self._material_kind(suffix),
                    "size_bytes": size,
                    "size_display": FeedbackService.format_size(size),
                    "sha256": file_sha256(resolved),
                    "path": str(resolved),
                    "is_batch_feedback_note": bool(
                        feedback
                        and resolved.name.startswith("批量反馈说明-")
                        and suffix == ".txt"
                    ),
                }
            )
        return tuple(snapshots)

    def _baseline_product_snapshot(
        self, project_path: Path, config: dict[str, Any]
    ) -> dict[str, Any] | None:
        product = self.archive_service.latest_product(project_path)
        if product is None or not product.is_file():
            return None
        digest = file_sha256(product)
        meta = read_courseware_meta(product)
        artifact_id = str(meta.get("courseware-artifact-id", ""))
        version = self._safe_int(meta.get("courseware-version"), -1)
        feedback_round = self._safe_int(
            meta.get("courseware-feedback-round"), version
        )
        matched: dict[str, Any] | None = None
        for raw in config.get("artifacts", []):
            if not isinstance(raw, dict):
                continue
            names = {
                str(raw.get("expected_name", "")),
                str(raw.get("current_name", "")),
                *(str(value) for value in raw.get("aliases", [])),
            }
            if (
                artifact_id
                and str(raw.get("artifact_id", "")) == artifact_id
            ) or (
                str(raw.get("sha256", ""))
                and str(raw.get("sha256", "")) == digest
            ) or product.name in names:
                matched = raw
                break
        if matched is not None:
            artifact_id = str(matched.get("artifact_id", artifact_id))
            version = int(matched.get("version_number", version if version >= 0 else 0))
            feedback_round = int(matched.get("feedback_round", feedback_round))
        if version < 0:
            version = self._infer_product_version(config, product.name)
        if feedback_round < 0:
            feedback_round = version
        return {
            "name": product.name,
            "path": str(product.resolve()),
            "relative_path": product.resolve().relative_to(project_path).as_posix(),
            "sha256": digest,
            "artifact_id": artifact_id,
            "version_number": version,
            "feedback_round": feedback_round,
        }

    def _output_snapshot(
        self, project_path: Path, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        output = project_path / "产品迭代" / str(artifact["expected_name"])
        return {
            "path": str(output.resolve()),
            "relative_path": f"产品迭代/{artifact['expected_name']}",
            "expected_name": str(artifact["expected_name"]),
            "artifact_id": str(artifact["artifact_id"]),
            "version_number": int(artifact["version_number"]),
            "feedback_round": int(artifact.get("feedback_round", 0)),
        }

    def _tool_snapshots(self, project_path: Path) -> tuple[dict[str, Any], ...]:
        group_root = project_path.parent
        self.project_service.validate_group_resources(group_root)
        manifest = self.project_service.read_manifest(group_root)
        result: list[dict[str, Any]] = []
        for role, copied_name in self.project_service.TOOL_ROLES.items():
            entry = manifest["tools"][role]
            copied = (group_root / "公共工具" / copied_name).resolve()
            result.append(
                {
                    "role": role,
                    "path": str(copied),
                    "sha256": file_sha256(copied),
                    "source_path": str(entry.get("source_path", "")),
                }
            )
        rules = (group_root / "AGENT任务规则.md").resolve()
        result.append(
            {
                "role": "rules",
                "path": str(rules),
                "sha256": file_sha256(rules),
                "source_path": "项目组任务规则",
            }
        )
        return tuple(result)

    def _batch_context_snapshot(
        self, record_path: Path, project_id: str, round_number: int
    ) -> dict[str, Any]:
        path = Path(record_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"批量反馈记录不存在：{path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"批量反馈记录无法读取：{path}") from exc
        if not isinstance(record, dict):
            raise ValueError("批量反馈记录格式无效")
        context_hash = self.batch_record_context_sha256(record)
        recorded_hash = str(record.get("feedback_context_sha256", ""))
        if recorded_hash and recorded_hash != context_hash:
            raise ValueError("批量反馈记录内容已变化")
        targets = [raw for raw in record.get("targets", []) if isinstance(raw, dict)]
        target = next(
            (raw for raw in targets if str(raw.get("project_id", "")) == project_id),
            None,
        )
        if target is None:
            raise ValueError("批次记录中没有当前项目")
        if int(target.get("target_round", 0)) != round_number:
            raise ValueError("批次记录中的目标轮次与当前任务不一致")
        peer_names = [
            str(raw.get("display_name", ""))
            for raw in targets
            if str(raw.get("project_id", "")) != project_id
        ]
        project_hint = str(target.get("project_hint", "")).strip()
        return {
            "batch_id": str(record.get("batch_id", "")),
            "record_path": str(path),
            "feedback_context_sha256": context_hash,
            "batch_note": str(record.get("batch_note", "")).strip() or "无",
            "project_hint": project_hint or "无",
            "material_position": project_hint or "未单独指定",
            "other_project_names": peer_names,
        }

    def _format_materials(self, materials: tuple[dict[str, Any], ...]) -> str:
        lines: list[str] = []
        for index, item in enumerate(materials, start=1):
            lines.extend(
                [
                    f"### 材料 {index}",
                    f"- 文件名：{item['file_name']}",
                    f"- 类型：{item['kind']}",
                    f"- 大小：{item['size_display']}（{item['size_bytes']} 字节）",
                    f"- SHA-256：{item['sha256']}",
                    f"- 实际路径：{item['path']}",
                    "- 批量反馈说明文件："
                    + ("是" if item.get("is_batch_feedback_note") else "否"),
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    @staticmethod
    def _format_batch_context(batch: dict[str, Any] | None) -> str:
        if batch is None:
            return ""
        peers = "、".join(batch.get("other_project_names", [])) or "无"
        return (
            "## 批量反馈上下文\n\n"
            f"- 批次 ID：{batch['batch_id']}\n"
            f"- 批次记录：{batch['record_path']}\n"
            f"- 批量补充说明：{batch['batch_note']}\n"
            f"- 本项目提示：{batch['project_hint']}\n"
            f"- 当前项目在统一材料中的位置：{batch['material_position']}\n"
            f"- 本批次其他项目名称（仅用于边界识别）：{peers}\n"
            "- 项目隔离边界：只处理当前项目，禁止读取、修改或引用其他项目的产品、反馈和输出。"
        )

    def _binding_block(self, project_path: Path) -> str:
        labels = {
            "rules": "任务规则",
            "workflow": "workflow",
            "template": "template",
            "validate": "validate",
        }
        lines = ["## 本项目实际绑定的制作工具", ""]
        for item in self._tool_snapshots(project_path):
            label = labels.get(str(item["role"]), str(item["role"]))
            lines.extend(
                [
                    f"- {label} 路径：{item['path']}",
                    f"- {label} SHA-256：{item['sha256']}",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _replace_template(template: str, replacements: dict[str, str]) -> str:
        content = template
        for marker, value in replacements.items():
            content = content.replace(marker, value)
        return content.rstrip()

    def _validate_metadata_identity(
        self, project_path: Path, metadata: dict[str, Any]
    ) -> None:
        if Path(str(metadata.get("project_path", ""))).resolve() != project_path:
            raise ValueError("项目路径与任务快照不一致")
        config = self.project_service.read_project_config(project_path)
        if str(metadata.get("project_id", "")) != str(config.get("project_id", "")):
            raise ValueError("项目 ID 与任务快照不一致")
        if Path(str(metadata.get("task_path", ""))).resolve() != (
            project_path / "当前任务.md"
        ).resolve():
            raise ValueError("任务文件路径与快照不一致")

    @staticmethod
    def _validate_content_markers(
        content: str, task_type: TaskType, feedback_round: int
    ) -> None:
        common = ("# 当前任务", "项目 ID：", "预期输出：", "输入快照 SHA-256：")
        missing = [marker for marker in common if marker not in content]
        if task_type is TaskType.FIRST_BUILD:
            if "任务类型：首次制作" not in content:
                missing.append("任务类型：首次制作")
            if "反馈轮次：0" not in content:
                missing.append("反馈轮次：0")
            if "客户反馈/第" in content or "反馈轮次：第" in content:
                raise ValueError("首次制作任务错误包含客户反馈轮次")
        else:
            for marker in (
                "任务类型：反馈修改",
                f"反馈轮次：第{feedback_round}轮",
                "## 本轮反馈材料",
                "## 修改基线",
            ):
                if marker not in content:
                    missing.append(marker)
        if missing:
            raise ValueError("任务语义字段缺失：" + "、".join(missing))

    def _validate_output_path(
        self, project_path: Path, output: dict[str, Any]
    ) -> None:
        output_path = Path(str(output.get("path", ""))).resolve()
        product_root = (project_path / "产品迭代").resolve()
        try:
            output_path.relative_to(product_root)
        except ValueError as exc:
            raise ValueError("预期输出路径越出当前项目") from exc
        if output_path.parent != product_root:
            raise ValueError("预期输出必须直接位于产品迭代目录")

    def _context_change_reason(
        self, metadata: dict[str, Any], current: dict[str, Any]
    ) -> str:
        stored = metadata.get("input_context")
        if not isinstance(stored, dict):
            return "任务输入快照已变化"
        checks = (
            ("project", "项目配置或路径已变化"),
            ("feedback_materials", "当前反馈轮次材料已变化"),
            ("baseline_product", "最新有效产品已变化"),
            ("batch_context", "批量反馈记录或本项目提示已变化"),
            ("original_requirements", "原始需求材料已变化"),
            ("tool_bindings", "项目组绑定工具已变化"),
            ("project_record", "项目记录已变化"),
            ("output", "预期输出身份已变化"),
        )
        for key, message in checks:
            if stored.get(key) != current.get(key):
                return message
        return "任务输入快照已变化"

    @staticmethod
    def _restore_file(path: Path, content: bytes | None) -> None:
        if content is None:
            path.unlink(missing_ok=True)
            return
        temporary = path.parent / f".{path.name}.restoring-{uuid4().hex}"
        temporary.write_bytes(content)
        temporary.replace(path)

    @staticmethod
    def _normalize_special_requirements(value: str) -> str:
        return str(value).strip() or "无"

    @staticmethod
    def _material_kind(suffix: str) -> str:
        if suffix in {".docx", ".doc"}:
            return "Word"
        if suffix == ".pdf":
            return "PDF"
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            return "图片"
        if suffix in {".txt", ".md", ".json", ".csv", ".tsv"}:
            return "文本"
        if suffix in {".ppt", ".pptx"}:
            return "演示文稿"
        if suffix in {".xls", ".xlsx"}:
            return "表格"
        return suffix.removeprefix(".").upper() or "二进制文件"

    @staticmethod
    def _canonical_sha256(value: Any) -> str:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()

    @staticmethod
    def _text_sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()

    @staticmethod
    def _safe_read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8-sig")
        except Exception:
            return ""

    @staticmethod
    def _safe_read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _task_type_from_text(content: str) -> TaskType | None:
        if "任务类型：反馈修改" in content:
            return TaskType.FEEDBACK_MODIFICATION
        if "任务类型：首次制作" in content:
            return TaskType.FIRST_BUILD
        return None

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _infer_product_version(self, config: dict[str, Any], name: str) -> int:
        for raw in config.get("artifacts", []):
            if not isinstance(raw, dict):
                continue
            names = {
                str(raw.get("expected_name", "")),
                str(raw.get("current_name", "")),
                *(str(value) for value in raw.get("aliases", [])),
            }
            if name in names:
                return int(raw.get("version_number", 0))
        if name == "初始版本.html":
            return 0
        if name.startswith("第") and name.endswith("轮修改.html"):
            try:
                return int(name[1 : name.index("轮修改.html")])
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
