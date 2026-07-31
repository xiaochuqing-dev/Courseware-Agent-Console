from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .archive_service import ArchiveService
from .prompt_service import PromptService
from .project_service import ProjectService
from .resource_paths import bundled_resource_root


@dataclass(frozen=True, slots=True)
class PreparedTask:
    project_root: Path
    task_path: Path
    config_path: Path
    task_content: str
    config_content: str


class TaskService:
    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (
            Path(resource_root)
            if resource_root
            else bundled_resource_root()
        )
        self.templates_root = self.resource_root / "prompt_templates"
        self.project_service = ProjectService(self.resource_root)
        self.archive_service = ArchiveService(self.project_service)

    def generate_first_build_task(self, project_root: Path, special_requirements: str) -> Path:
        prepared = self.prepare_first_build_task(project_root, special_requirements)
        self.commit_prepared_task(prepared)
        return prepared.task_path

    def prepare_first_build_task(
        self, project_root: Path, special_requirements: str
    ) -> PreparedTask:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        config, artifact = self._prepare_artifact(project_path, 0, 0)
        template = self._read_template("first_build_task.md")
        content = (
            template.replace("{{PROJECT_NAME}}", str(config["display_name"]))
            .replace("{{PROJECT_ID}}", str(config["project_id"]))
            .replace("{{ARTIFACT_ID}}", str(artifact["artifact_id"]))
            .replace("{{EXPECTED_OUTPUT}}", f"产品迭代/{artifact['expected_name']}")
            .replace("{{VERSION_NUMBER}}", "0")
            .replace("{{FEEDBACK_ROUND_NUMBER}}", "0")
            .replace("{{SPECIAL_REQUIREMENTS}}", special_requirements.strip() or "无")
        )
        content = content.rstrip() + self._binding_block(project_path)
        return self._prepared_task(project_path, content, config)

    def generate_feedback_task(
        self, project_root: Path, round_number: int, special_requirements: str
    ) -> Path:
        prepared = self.prepare_feedback_task(
            project_root, round_number, special_requirements
        )
        self.commit_prepared_task(prepared)
        return prepared.task_path

    def prepare_feedback_task(
        self, project_root: Path, round_number: int, special_requirements: str
    ) -> PreparedTask:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        feedback_round = project_path / "客户反馈" / f"第{round_number}轮"
        if round_number <= 0 or not feedback_round.is_dir():
            raise FileNotFoundError(f"客户反馈轮次不存在：第{round_number}轮")
        config, artifact = self._prepare_artifact(
            project_path, round_number, round_number
        )
        template = self._read_template("feedback_task.md")
        content = (
            template.replace("{{PROJECT_NAME}}", str(config["display_name"]))
            .replace("{{PROJECT_ID}}", str(config["project_id"]))
            .replace("{{ARTIFACT_ID}}", str(artifact["artifact_id"]))
            .replace("{{EXPECTED_OUTPUT}}", f"产品迭代/{artifact['expected_name']}")
            .replace("{{VERSION_NUMBER}}", str(round_number))
            .replace("{{FEEDBACK_ROUND_NUMBER}}", str(round_number))
            .replace("{{FEEDBACK_ROUND}}", f"第{round_number}轮")
            .replace("{{SPECIAL_REQUIREMENTS}}", special_requirements.strip() or "无")
        )
        content = content.rstrip() + self._binding_block(project_path)
        prepared = self._prepared_task(project_path, content, config)
        self.validate_prepared_feedback_task(prepared, round_number)
        return prepared

    def commit_prepared_task(self, prepared: PreparedTask) -> None:
        targets = (
            (prepared.config_path, prepared.config_content),
            (prepared.task_path, prepared.task_content),
        )
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
        except Exception:
            for target in reversed(promoted):
                self._restore_file(target, snapshots[target])
            raise
        finally:
            for temporary, _target in staged:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def validate_prepared_feedback_task(
        prepared: PreparedTask, round_number: int
    ) -> None:
        required = (
            "任务类型：反馈修改",
            f"反馈轮次：第{round_number}轮",
            "项目 ID：",
            "预期输出：产品迭代/",
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

    def _read_template(self, name: str) -> str:
        path = self.templates_root / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板：{name}")
        return path.read_text(encoding="utf-8")

    def _prepare_artifact(
        self, project_path: Path, version_number: int, feedback_round: int
    ) -> tuple[dict, dict]:
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
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "aliases": [],
            "ignored_names": [],
        }
        artifacts.append(artifact)
        return config, artifact

    def _prepared_task(
        self, project_path: Path, content: str, config: dict
    ) -> PreparedTask:
        task_content = content.rstrip() + "\n"
        config_content = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        return PreparedTask(
            project_root=project_path,
            task_path=project_path / "当前任务.md",
            config_path=project_path / self.project_service.PROJECT_CONFIG_NAME,
            task_content=task_content,
            config_content=config_content,
        )

    @staticmethod
    def _restore_file(path: Path, content: bytes | None) -> None:
        if content is None:
            path.unlink(missing_ok=True)
            return
        temporary = path.parent / f".{path.name}.restoring-{uuid4().hex}"
        temporary.write_bytes(content)
        temporary.replace(path)

    @staticmethod
    def _binding_block(project_path: Path) -> str:
        group_root = project_path.parent
        service = ProjectService()
        service.validate_group_resources(group_root)
        manifest = service.read_manifest(group_root)
        lines = [
            "",
            "",
            "## 本项目实际绑定的制作工具",
            "",
            "必须先按以下绝对路径读取并执行，禁止使用内置默认文件或其他项目组文件：",
            "",
        ]
        labels = {
            "workflow": "workflow",
            "template": "template",
            "validate": "validate",
        }
        for role, copied_name in service.TOOL_ROLES.items():
            entry = manifest["tools"][role]
            copied = group_root / "公共工具" / copied_name
            lines.extend(
                [
                    f"{labels[role]} 路径：{copied}",
                    f"{labels[role]} SHA-256：{entry['sha256']}",
                    f"{labels[role]} 原始来源：{entry['source_path']}",
                    "",
                ]
            )
        lines.extend(
            [
                "执行顺序：先读取 workflow，再以 template 为唯一页面起点，最后真实运行 validate。",
                "validate 产生的 error 必须全部修复；warning 必须修复或在项目记录中说明教学理由。",
                "",
            ]
        )
        return "\n".join(lines)
