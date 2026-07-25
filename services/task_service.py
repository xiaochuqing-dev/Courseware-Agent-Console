from __future__ import annotations

from pathlib import Path

from .prompt_service import PromptService
from .project_service import ProjectService
from .resource_paths import bundled_resource_root


class TaskService:
    def __init__(self, resource_root: Path | None = None) -> None:
        self.resource_root = (
            Path(resource_root)
            if resource_root
            else bundled_resource_root()
        )
        self.templates_root = self.resource_root / "prompt_templates"

    def generate_first_build_task(self, project_root: Path, special_requirements: str) -> Path:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        template = self._read_template("first_build_task.md")
        content = (
            template.replace("{{PROJECT_NAME}}", project_path.name)
            .replace("{{SPECIAL_REQUIREMENTS}}", special_requirements.strip() or "无")
        )
        content = content.rstrip() + self._binding_block(project_path)
        target = project_path / "当前任务.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)
        return target

    def generate_feedback_task(
        self, project_root: Path, round_number: int, special_requirements: str
    ) -> Path:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        feedback_round = project_path / "客户反馈" / f"第{round_number}轮"
        if round_number <= 0 or not feedback_round.is_dir():
            raise FileNotFoundError(f"客户反馈轮次不存在：第{round_number}轮")
        template = self._read_template("feedback_task.md")
        content = (
            template.replace("{{PROJECT_NAME}}", project_path.name)
            .replace("{{FEEDBACK_ROUND}}", f"第{round_number}轮")
            .replace("{{SPECIAL_REQUIREMENTS}}", special_requirements.strip() or "无")
        )
        content = content.rstrip() + self._binding_block(project_path)
        target = project_path / "当前任务.md"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
        temporary.replace(target)
        return target

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
                "validate 产生的 error 必须全部修复；warning 必须修复或在验收记录中说明教学理由。",
                "",
            ]
        )
        return "\n".join(lines)
