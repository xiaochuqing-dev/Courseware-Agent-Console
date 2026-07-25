from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .archive_service import ArchiveService
from .feedback_service import FeedbackService
from .project_service import ProjectService
from .process_utils import run_hidden_process


@dataclass(frozen=True, slots=True)
class AcceptanceItem:
    status: str
    title: str
    detail: str
    path: str = ""
    suggestion: str = ""


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    passed: bool
    checked_at: str
    project_path: str
    product_path: str
    product_sha256: str
    validator_output: str
    items: tuple[AcceptanceItem, ...]
    json_path: Path
    markdown_path: Path

    @property
    def passed_count(self) -> int:
        return sum(item.status == "passed" for item in self.items)

    @property
    def warning_count(self) -> int:
        return sum(item.status == "warning" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.items)


class AcceptanceService:
    RESULT_PATTERN = re.compile(
        r"Result:\s*(\d+) error\(s\),\s*(\d+) warning\(s\)"
    )

    def __init__(
        self,
        project_service: ProjectService | None = None,
        archive_service: ArchiveService | None = None,
        feedback_service: FeedbackService | None = None,
        state_root: Path | None = None,
    ) -> None:
        self.project_service = project_service or ProjectService()
        self.archive_service = archive_service or ArchiveService()
        self.feedback_service = feedback_service or FeedbackService()
        if state_root is None:
            local_data = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
            state_root = Path(local_data or (Path.home() / ".courseware-agent-console"))
        self.state_root = Path(state_root) / "acceptance"

    def run(self, group_root: Path, project_root: Path) -> AcceptanceReport:
        group = Path(group_root).resolve()
        project = Path(project_root).resolve()
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        items: list[AcceptanceItem] = []
        validator_output = ""

        try:
            self.project_service.validate_project_structure(group, project)
            items.append(
                AcceptanceItem(
                    "passed",
                    "项目结构",
                    "必需目录、原始需求和工具绑定记录完整。",
                    str(project),
                )
            )
        except Exception as exc:
            items.append(
                AcceptanceItem(
                    "failed",
                    "项目结构",
                    str(exc),
                    str(project),
                    "恢复缺失文件或目录后重新验收。",
                )
            )

        manifest: dict | None = None
        try:
            manifest = self.project_service.read_manifest(group)
            for role, name in self.project_service.TOOL_ROLES.items():
                copied = group / "公共工具" / name
                entry = manifest["tools"][role]
                digest = self.project_service.file_sha256(copied)
                if digest != str(entry["sha256"]).upper():
                    raise ValueError(f"{role} 当前哈希与创建记录不一致：{copied}")
                source = Path(str(entry["source_path"]))
                if not source.is_file():
                    items.append(
                        AcceptanceItem(
                            "warning",
                            f"{role} 原始来源",
                            "原始来源当前不可访问，但项目组内绑定副本完整。",
                            str(source),
                            "需要追溯来源时恢复该路径。",
                        )
                    )
                elif self.project_service.file_sha256(source) != digest:
                    items.append(
                        AcceptanceItem(
                            "warning",
                            f"{role} 原始来源",
                            "原始来源内容已变化，项目仍使用创建时复制的绑定版本。",
                            str(source),
                            "如需升级工具，请创建新的项目组版本。",
                        )
                    )
                items.append(
                    AcceptanceItem(
                        "passed",
                        f"{role} 绑定",
                        f"已核对真实来源、项目副本和 SHA-256：{digest}",
                        str(copied),
                    )
                )
        except Exception as exc:
            items.append(
                AcceptanceItem(
                    "failed",
                    "真实工具绑定",
                    str(exc),
                    str(group / self.project_service.MANIFEST_NAME),
                    "重新绑定三份真实工具并创建新项目组。",
                )
            )

        source_root = project / "原始需求"
        source_files = sorted(source_root.glob("*.json")) if source_root.is_dir() else []
        if not source_files:
            items.append(
                AcceptanceItem(
                    "failed",
                    "原始需求",
                    "没有可解析的 JSON 原始需求。",
                    str(source_root),
                    "恢复该项目对应的原始 JSON。",
                )
            )
        else:
            try:
                for source in source_files:
                    data = json.loads(source.read_text(encoding="utf-8-sig"))
                    if not isinstance(data, dict) or not data:
                        raise ValueError(f"JSON 根对象为空或类型错误：{source.name}")
                items.append(
                    AcceptanceItem(
                        "passed",
                        "原始需求",
                        f"已解析 {len(source_files)} 个 JSON 文件。",
                        str(source_root),
                    )
                )
            except Exception as exc:
                items.append(
                    AcceptanceItem(
                        "failed",
                        "原始需求",
                        str(exc),
                        str(source_root),
                        "修复 JSON 编码或结构后重新验收。",
                    )
                )

        product = self.archive_service.latest_product(project)
        product_hash = ""
        if product is None:
            items.append(
                AcceptanceItem(
                    "failed",
                    "课件成品",
                    "产品迭代中没有可验收的 HTML 课件。",
                    str(project / "产品迭代"),
                    "根据当前任务生成课件后重新验收。",
                )
            )
        else:
            try:
                raw = product.read_bytes()
                if not raw.strip():
                    raise ValueError("课件文件为空。")
                html = raw.decode("utf-8-sig").lower()
                if "<html" not in html or "<body" not in html or "</html>" not in html:
                    raise ValueError("HTML 文档结构不完整。")
                product_hash = self.project_service.file_sha256(product)
                items.append(
                    AcceptanceItem(
                        "passed",
                        "课件成品",
                        f"HTML 可读取，SHA-256：{product_hash}",
                        str(product),
                    )
                )
            except Exception as exc:
                items.append(
                    AcceptanceItem(
                        "failed",
                        "课件成品",
                        str(exc),
                        str(product),
                        "修复或重新生成 HTML 文件。",
                    )
                )

        if product is not None and manifest is not None:
            validator = group / "公共工具" / self.project_service.TOOL_ROLES["validate"]
            try:
                result = run_hidden_process(
                    ["node", str(validator), str(product)], timeout=120
                )
                validator_output = "\n".join(
                    part.strip()
                    for part in (result.stdout, result.stderr)
                    if part.strip()
                )
                match = self.RESULT_PATTERN.search(validator_output)
                errors = int(match.group(1)) if match else int(result.returncode != 0)
                warnings = int(match.group(2)) if match else 0
                if result.returncode != 0 or errors:
                    items.append(
                        AcceptanceItem(
                            "failed",
                            "validate 实际执行",
                            validator_output or f"validate 退出码：{result.returncode}",
                            str(validator),
                            "按 validate 输出修复课件后重新执行。",
                        )
                    )
                elif warnings:
                    items.append(
                        AcceptanceItem(
                            "warning",
                            "validate 实际执行",
                            f"0 个错误，{warnings} 个警告。\n{validator_output}",
                            str(validator),
                            "确认每条警告具有明确教学理由，否则应修复。",
                        )
                    )
                else:
                    items.append(
                        AcceptanceItem(
                            "passed",
                            "validate 实际执行",
                            "真实 validate 返回 0 个错误、0 个警告。",
                            str(validator),
                        )
                    )
            except Exception as exc:
                items.append(
                    AcceptanceItem(
                        "failed",
                        "validate 实际执行",
                        str(exc),
                        str(validator),
                        "确认 Node.js 与 validate 文件可执行。",
                    )
                )

        rounds = self.feedback_service.scan_rounds(project)
        task_path = project / "当前任务.md"
        task_text = (
            task_path.read_text(encoding="utf-8-sig") if task_path.is_file() else ""
        )
        if rounds and f"第{rounds[-1]}轮" not in task_text:
            items.append(
                AcceptanceItem(
                    "warning",
                    "反馈与任务一致性",
                    f"最新反馈为第{rounds[-1]}轮，但当前任务未明确引用该轮。",
                    str(task_path),
                    "重新生成对应反馈轮次任务。",
                )
            )
        else:
            items.append(
                AcceptanceItem(
                    "passed",
                    "反馈与任务一致性",
                    f"已核对 {len(rounds)} 个反馈轮次与当前任务。",
                    str(task_path),
                )
            )

        items.append(
            AcceptanceItem(
                "warning",
                "浏览器视觉检查",
                "当前验收服务未执行真实浏览器打开与人工视觉检查；仅完成 validate 静态规则检查。此项不阻断自动验收结论。",
                str(product or project),
                "请在浏览器中打开课件，人工检查内容可见性、布局、交互和动画。",
            )
        )
        passed = not any(item.status == "failed" for item in items)
        return self._write_report(
            project,
            passed,
            checked_at,
            product,
            product_hash,
            validator_output,
            tuple(items),
        )

    def _write_report(
        self,
        project: Path,
        passed: bool,
        checked_at: str,
        product: Path | None,
        product_hash: str,
        validator_output: str,
        items: tuple[AcceptanceItem, ...],
    ) -> AcceptanceReport:
        report_root = self._state_directory(project)
        report_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        json_path = report_root / f"验收-{stamp}.json"
        markdown_path = project / "项目记录.md"
        payload = {
            "passed": passed,
            "checked_at": checked_at,
            "project_path": str(project),
            "product_path": str(product or ""),
            "product_sha256": product_hash,
            "validator_output": validator_output,
            "items": [asdict(item) for item in items],
        }
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        passed_count = sum(item.status == "passed" for item in items)
        warning_count = sum(item.status == "warning" for item in items)
        failed_count = sum(item.status == "failed" for item in items)
        lines = [
            "",
            "## 完整产品验收",
            "",
            f"- 时间：{checked_at}",
            f"- 验收产品：{product.name if product else '未找到'}",
            f"- validate 结果：{'通过' if passed else '存在问题'}",
            f"- 通过项：{passed_count}",
            f"- 警告项：{warning_count}",
            f"- 失败项：{failed_count}",
            "- 人工视觉检查：未执行",
        ]
        with markdown_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
        return AcceptanceReport(
            passed,
            checked_at,
            str(project),
            str(product or ""),
            product_hash,
            validator_output,
            items,
            json_path,
            markdown_path,
        )

    def latest_report(self, project_root: Path) -> dict | None:
        report_root = self._state_directory(Path(project_root).resolve())
        reports = sorted(report_root.glob("验收-*.json")) if report_root.is_dir() else []
        if not reports:
            return None
        try:
            data = json.loads(reports[-1].read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def has_current_passing_report(self, project_root: Path) -> bool:
        report = self.latest_report(project_root)
        if not report or report.get("passed") is not True:
            return False
        product = self.archive_service.latest_product(project_root)
        return bool(
            product
            and report.get("product_sha256")
            == self.project_service.file_sha256(product)
        )

    def _state_directory(self, project_root: Path) -> Path:
        project = Path(project_root).resolve()
        try:
            identity = str(self.project_service.read_project_config(project)["project_id"])
        except Exception:
            identity = str(project).casefold()
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.state_root / key
