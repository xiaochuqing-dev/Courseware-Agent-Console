import tempfile
from pathlib import Path

from services import ArchiveService, ProjectService, ToolBinding
from services.identity_service import write_courseware_meta


def tool_binding(resource_root: Path) -> ToolBinding:
    del resource_root
    tools = Path(tempfile.gettempdir()) / "courseware-agent-console-test-tools-v1"
    tools.mkdir(parents=True, exist_ok=True)
    contents = {
        "WORKFLOW.md": (
            "# 自动化测试工作流\n\n"
            "使用 template.html 作为起点，完成后运行 validate-tool.js。\n"
        ),
        "template.html": (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width\">"
            "<title>测试课件</title></head><body>"
            "<section class=\"slide\">测试页</section></body></html>\n"
        ),
        "validate-tool.js": (
            "#!/usr/bin/env node\n'use strict';\n"
            "const fs = require('node:fs');\n"
            "const target = process.argv[2];\n"
            "if (!target || !fs.existsSync(target)) process.exit(2);\n"
            "const html = fs.readFileSync(target, 'utf8');\n"
            "if (!/<title>[^<]+<\\/title>/i.test(html) || "
            "!/<section[^>]+class=[\"'][^\"']*slide/i.test(html)) process.exit(1);\n"
            "console.log('test fixture validation passed');\n"
        ),
    }
    for name, content in contents.items():
        path = tools / name
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
    return ToolBinding(
        workflow=tools / "WORKFLOW.md",
        template=tools / "template.html",
        validate=tools / "validate-tool.js",
    )


def create_valid_product(
    project_service: ProjectService,
    project,
    version_number: int = 0,
    feedback_round: int = 0,
) -> Path:
    archive = ArchiveService(project_service)
    artifact = archive.allocate_artifact(
        project.path,
        version_number,
        feedback_round,
    )
    product = project.path / "产品迭代" / artifact["expected_name"]
    product.write_text(
        "<!doctype html><html><head><title>测试产品</title></head>"
        "<body><section class=\"slide\">测试产品</section></body></html>\n",
        encoding="utf-8",
    )
    write_courseware_meta(
        product,
        project.project_id,
        artifact["artifact_id"],
        version_number,
        feedback_round,
    )
    return product
