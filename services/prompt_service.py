from __future__ import annotations

from pathlib import Path

from .archive_service import ArchiveService
from .resource_paths import bundled_resource_root


class PromptService:
    def __init__(
        self,
        resource_root: Path | None = None,
        archive_service: ArchiveService | None = None,
    ) -> None:
        self.resource_root = (
            Path(resource_root)
            if resource_root
            else bundled_resource_root()
        )
        self.templates_root = self.resource_root / "prompt_templates"
        self.archive_service = archive_service or ArchiveService()

    def execution_prompt(self, project_name: str) -> str:
        return self._read_template("short_execute_prompt.txt").replace(
            "{{PROJECT_NAME}}", project_name
        ).strip()

    def task_execution_instruction(self, task_path: Path) -> str:
        path = Path(task_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"任务文件不存在：{path}")
        return f"请读取并完整执行以下任务文件：\n{path}"

    def project_task_execution_instruction(self, project_root: Path) -> str:
        project = Path(project_root).expanduser().resolve()
        if not project.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project}")
        return self.task_execution_instruction(project / "当前任务.md")

    def workflow_task_execution_instruction(self, group_root: Path) -> str:
        group = Path(group_root).expanduser().resolve()
        if not group.is_dir():
            raise FileNotFoundError(f"项目组目录不存在：{group}")
        return self.task_execution_instruction(
            group / "工作流优化" / "当前优化任务.md"
        )

    def product_acceptance_prompt(self, project_root: Path) -> str:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        latest = self.archive_service.latest_product(project_path)
        if latest is None:
            raise FileNotFoundError("当前项目没有可用产品版本。")
        try:
            display_name = str(
                self.archive_service.project_service.read_project_config(project_path).get(
                    "display_name", project_path.name
                )
            )
        except Exception:
            display_name = project_path.name
        return (
            self._read_template("product_acceptance_prompt.md")
            .replace("{{PROJECT_NAME}}", display_name)
            .replace("{{LATEST_PRODUCT}}", f"{latest.parent.name}/{latest.name}")
            .strip()
        )

    def workflow_optimization_prompt(
        self, group_root: Path, project_paths: list[Path] | tuple[Path, ...]
    ) -> str:
        group = Path(group_root).resolve()
        if not group.is_dir():
            raise FileNotFoundError(f"项目组目录不存在：{group}")
        selected = self._validate_archived_projects(group, project_paths)
        if not selected:
            raise ValueError("请至少选择一个已完成项目。")
        common = self._common_paths(group)
        project_list = "\n".join(
            f"{index}. {path}" for index, path in enumerate(selected, start=1)
        )
        prompt = self._read_template("workflow_optimization_prompt.md")
        replacements = {
            "{{GROUP_ROOT}}": str(group),
            "{{RULES_PATH}}": str(common[0]),
            "{{WORKFLOW_PATH}}": str(common[1]),
            "{{TEMPLATE_PATH}}": str(common[2]),
            "{{VALIDATOR_PATH}}": str(common[3]),
            "{{SELECTED_PROJECTS}}": project_list,
        }
        for marker, value in replacements.items():
            prompt = prompt.replace(marker, value)
        return prompt.strip()

    def workflow_apply_prompt(self, group_root: Path) -> str:
        group = Path(group_root).resolve()
        if not group.is_dir():
            raise FileNotFoundError(f"项目组目录不存在：{group}")
        common = self._common_paths(group)
        prompt = self._read_template("workflow_apply_prompt.md")
        replacements = {
            "{{GROUP_ROOT}}": str(group),
            "{{RULES_PATH}}": str(common[0]),
            "{{WORKFLOW_PATH}}": str(common[1]),
            "{{TEMPLATE_PATH}}": str(common[2]),
            "{{VALIDATOR_PATH}}": str(common[3]),
        }
        for marker, value in replacements.items():
            prompt = prompt.replace(marker, value)
        return prompt.strip()

    def _validate_archived_projects(
        self, group_root: Path, project_paths: list[Path] | tuple[Path, ...]
    ) -> tuple[Path, ...]:
        archive_root = self.archive_service.archive_root(group_root).resolve()
        selected: list[Path] = []
        for raw_path in project_paths:
            path = Path(raw_path).resolve()
            try:
                path.relative_to(archive_root)
            except ValueError as exc:
                raise ValueError(f"只能选择“已完成项目”中的项目：{path}") from exc
            if not path.is_dir():
                raise FileNotFoundError(f"已完成项目目录不存在：{path}")
            record = path / "项目记录.md"
            if not record.is_file():
                raise FileNotFoundError(
                    f"已完成项目缺少历史索引：{record}。请恢复项目记录后重试。"
                )
            selected.append(path)
        return tuple(dict.fromkeys(selected))

    @staticmethod
    def _common_paths(group_root: Path) -> tuple[Path, Path, Path, Path]:
        paths = (
            group_root / "AGENT任务规则.md",
            group_root / "公共工具" / "WORKFLOW.md",
            group_root / "公共工具" / "template.html",
            group_root / "公共工具" / "validate-tool.js",
        )
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"工作流优化所需公共文件不存在：{path}")
        return paths

    def _read_template(self, name: str) -> str:
        path = self.templates_root / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板：{name}")
        return path.read_text(encoding="utf-8")
