from pathlib import Path

from services import ToolBinding


def tool_binding(resource_root: Path) -> ToolBinding:
    tools = Path(resource_root) / "default_public_tools"
    return ToolBinding(
        workflow=tools / "WORKFLOW.md",
        template=tools / "template.html",
        validate=tools / "validate-tool.js",
    )
