from __future__ import annotations

from pathlib import Path

from .archive_service import ArchiveService


class PromptService:
    def __init__(
        self,
        resource_root: Path | None = None,
        archive_service: ArchiveService | None = None,
    ) -> None:
        self.resource_root = (
            Path(resource_root)
            if resource_root
            else Path(__file__).resolve().parents[1] / "resources"
        )
        self.templates_root = self.resource_root / "prompt_templates"
        self.archive_service = archive_service or ArchiveService()

    def execution_prompt(self, project_name: str) -> str:
        return self._read_template("short_execute_prompt.txt").replace(
            "{{PROJECT_NAME}}", project_name
        ).strip()

    def product_acceptance_prompt(self, project_root: Path) -> str:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        latest = self.archive_service.latest_product(project_path)
        if latest is None:
            raise FileNotFoundError("当前项目没有可用产品版本。")
        return (
            self._read_template("product_acceptance_prompt.md")
            .replace("{{PROJECT_NAME}}", project_path.name)
            .replace("{{LATEST_PRODUCT}}", f"产品迭代/{latest.name}")
            .strip()
        )

    def _read_template(self, name: str) -> str:
        path = self.templates_root / name
        if not path.is_file():
            raise FileNotFoundError(f"缺少模板：{name}")
        return path.read_text(encoding="utf-8")
