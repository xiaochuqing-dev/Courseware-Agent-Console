import json
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
from pypdf import PdfWriter

from services import (
    AcceptanceService,
    ArchiveService,
    FeedbackService,
    ProjectService,
    SettingsService,
    TaskService,
    ToolBinding,
    ValidationError,
)
from tests.helpers import tool_binding
from ui.main_window import MainWindow
from ui.pages import CreateProjectPage


@pytest.fixture
def resource_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources"


def create_group(
    root: Path, resource_root: Path, name: str = "真实工具项目组"
):
    source = root / f"{name}.json"
    source.write_text(
        json.dumps({"title": name, "steps": [{"title": "观察"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    service = ProjectService(resource_root)
    return service, service.create_project_group(
        name, 1, root, [source], tool_binding(resource_root)
    )


def write_png(path: Path) -> Path:
    image = QImage(96, 64, QImage.Format.Format_RGB32)
    image.fill(QColor("#bfe7d8"))
    assert image.save(str(path), "PNG")
    return path


def write_pdf(path: Path) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=320, height=240)
    writer.add_blank_page(width=320, height=240)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_real_tool_preflight_rejects_missing_and_empty_files(
    tmp_path: Path, resource_root: Path
) -> None:
    service = ProjectService(resource_root)
    binding = tool_binding(resource_root)
    with pytest.raises(ValidationError, match="尚未选择真实公共工具"):
        service.validate_tool_binding(None)

    missing = ToolBinding(
        tmp_path / "missing-workflow.md", binding.template, binding.validate
    )
    with pytest.raises(ValidationError, match="workflow 文件不存在"):
        service.validate_tool_binding(missing)

    empty = tmp_path / "empty-workflow.md"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError, match="workflow 文件为空"):
        service.validate_tool_binding(
            ToolBinding(empty, binding.template, binding.validate)
        )


def test_create_page_requires_all_three_real_tools(
    tmp_path: Path, resource_root: Path
) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    page = CreateProjectPage(ProjectService(resource_root))
    page.count_input.setValue(1)
    page.add_json_files([source])
    assert not page.create_button.isEnabled()
    binding = tool_binding(resource_root)
    page.set_tool_paths(binding.workflow, binding.template, binding.validate)
    assert page.create_button.isEnabled()
    page.close()
    app.processEvents()


def test_manifest_records_exact_sources_and_hashes(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root)
    manifest = service.read_manifest(group.root)
    binding = tool_binding(resource_root)
    for role, source in binding.paths().items():
        entry = manifest["tools"][role]
        assert Path(entry["source_path"]) == source.resolve()
        assert entry["sha256"] == service.file_sha256(source)
        assert service.file_sha256(
            group.root / "公共工具" / service.TOOL_ROLES[role]
        ) == entry["sha256"]


def test_acceptance_executes_real_validator_and_expires_after_change(
    tmp_path: Path, resource_root: Path
) -> None:
    project_service, group = create_group(tmp_path, resource_root)
    project = group.projects[0]
    product = project.path / "工作文件" / "初始版本.html"
    product.write_bytes((group.root / "公共工具" / "template.html").read_bytes())
    TaskService(resource_root).generate_first_build_task(project.path, "")
    acceptance = AcceptanceService(
        project_service, ArchiveService(), FeedbackService()
    )

    report = acceptance.run(group.root, project.path)

    assert report.passed
    assert "0 个错误、0 个警告" in "\n".join(
        item.detail for item in report.items
    )
    assert report.json_path.is_file()
    assert report.markdown_path.is_file()
    assert acceptance.has_current_passing_report(project.path)
    assert any(
        item.status == "warning" and item.title == "浏览器视觉检查"
        for item in report.items
    )

    product.write_text(
        product.read_text(encoding="utf-8") + "\n<!-- changed -->\n",
        encoding="utf-8",
    )
    assert not acceptance.has_current_passing_report(project.path)


def test_feedback_parser_reports_real_metadata_and_corruption(
    tmp_path: Path,
) -> None:
    service = FeedbackService()
    image = service.pending_from_file(write_png(tmp_path / "反馈截图.png"))
    pdf = service.pending_from_file(write_pdf(tmp_path / "反馈材料.pdf"))
    text_path = tmp_path / "反馈说明.txt"
    text_path.write_text("第一行\n第二行", encoding="utf-8")
    text = service.pending_from_file(text_path)

    assert "96×64" in image.detail
    assert "2 页" in pdf.detail
    assert "7 字" in text.detail
    assert image.status == pdf.status == text.status == "等待保存"

    broken = tmp_path / "损坏.pdf"
    broken.write_bytes(b"not-a-pdf")
    with pytest.raises(ValueError, match="未加密或损坏"):
        service.pending_from_file(broken)
    unsupported = tmp_path / "反馈.docx"
    unsupported.write_bytes(b"docx")
    with pytest.raises(ValueError, match="不支持"):
        service.pending_from_file(unsupported)


def test_group_registry_switch_and_console_removal_are_deterministic(
    tmp_path: Path, resource_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QApplication.instance() or QApplication([])
    service, first = create_group(tmp_path, resource_root, "第一组")
    _, second = create_group(tmp_path, resource_root, "第二组")
    _, third = create_group(tmp_path, resource_root, "第三组")
    settings = SettingsService(
        QSettings(str(tmp_path / "groups.ini"), QSettings.Format.IniFormat)
    )
    window = MainWindow(service, TaskService(resource_root), settings)
    window.load_project_group(first.root)
    window.load_project_group(second.root)
    window.load_project_group(third.root)
    window.load_project_group(first.root)
    monkeypatch.setattr(
        window,
        "_confirm_group_deletion",
        lambda *args: "remove",
    )

    window.delete_project_group(second.root)
    assert window.home_page.group.root == first.root
    assert second.root.is_dir()
    assert second.root not in settings.registered_group_paths()

    window.delete_project_group(first.root)
    assert window.home_page.group.root == third.root
    assert first.root.is_dir()

    window.delete_project_group(third.root)
    assert window.home_page.group is None
    assert settings.registered_group_paths() == ()
    assert third.root.is_dir()
    window.close()
    app.processEvents()


def test_permanent_group_deletion_does_not_touch_sources_or_shared_tools(
    tmp_path: Path, resource_root: Path
) -> None:
    service, group = create_group(tmp_path, resource_root, "永久删除组")
    source = tmp_path / "永久删除组.json"
    shared_template = tool_binding(resource_root).template

    service.delete_project_group(group.root, delete_local_files=True)

    assert not group.root.exists()
    assert source.is_file()
    assert shared_template.is_file()
