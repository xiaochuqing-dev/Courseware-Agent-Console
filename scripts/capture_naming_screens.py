from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMessageBox, QWidget  # noqa: E402

from app import create_application  # noqa: E402
from services import ArchiveService, ProjectService, SettingsService, TaskService  # noqa: E402
from services.identity_service import write_courseware_meta  # noqa: E402
from tests.helpers import tool_binding  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


PROJECT_NAMES = (
    "立体表面最短路径",
    "数轴上点的运动",
    "太阳光线下物体影子的变化规律",
    "小棒和纸片在手电筒照射下的影子变化",
    "正方形纸片的翻折问题",
    "正负球模型（有理数加法）",
)


def save_widget(widget: QWidget, path: Path) -> None:
    QGuiApplication.processEvents()
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    widget.render(painter, QPoint())
    painter.end()
    if image.isNull() or not image.save(str(path)):
        raise RuntimeError(f"截图保存失败：{path}")


def wait_until(predicate, timeout_ms: int = 8000) -> None:
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QGuiApplication.processEvents()
        QTest.qWait(20)
        elapsed += 20
    if not predicate():
        raise RuntimeError("等待界面状态超时。")


def write_product(
    archive: ArchiveService,
    project,
    version: int,
    template: Path,
) -> Path:
    artifact = archive.allocate_artifact(project.path, version, version)
    product = project.path / "产品迭代" / artifact["expected_name"]
    product.write_bytes(template.read_bytes())
    write_courseware_meta(
        product,
        project.project_id,
        artifact["artifact_id"],
        version,
        version,
    )
    archive.reconcile_product_files(project.path)
    return product


def main() -> int:
    artifacts = ROOT / "artifacts"
    artifacts.mkdir(exist_ok=True)
    app = create_application(["capture_naming_screens"])
    resource_root = ROOT / "resources"

    with tempfile.TemporaryDirectory(prefix="naming-ui-") as temp:
        root = Path(temp)
        sources = []
        for name in PROJECT_NAMES:
            source = root / f"{name}.json"
            source.write_text(
                json.dumps({"title": name, "pages": [{"title": "课堂探究"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            sources.append(source)

        project_service = ProjectService(resource_root)
        archive = ArchiveService(project_service)
        tasks = TaskService(resource_root)
        group = project_service.create_project_group(
            "九年级课件命名验收",
            len(sources),
            root,
            sources,
            tool_binding(resource_root),
        )
        settings = SettingsService(
            QSettings(str(root / "naming.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(
            project_service,
            tasks,
            settings,
            archive_service=archive,
        )
        window.load_project_group(group.root)
        window.resize(1366, 820)
        window.show()
        app.processEvents()

        visible_names = [
            window.home_page.project_list.item(index).text()
            for index in range(window.home_page.project_list.count())
        ]
        if visible_names != list(PROJECT_NAMES):
            raise RuntimeError(f"左侧项目名称不正确：{visible_names}")
        save_widget(window, artifacts / "naming-project-list.png")

        window.show_create_page()
        create = window.create_page
        create.count_input.setValue(6)
        create.add_json_files(sources)
        binding = tool_binding(resource_root)
        create.set_tool_paths(binding.workflow, binding.template, binding.validate)
        wait_until(lambda: create._tool_validation_result is not None)
        if create.mapping_list.count() != 6:
            raise RuntimeError("创建页未显示完整六项目映射。")
        save_widget(window, artifacts / "naming-create-projects.png")

        window.show_home_page()
        target = group.projects[-1]
        target_row = len(group.projects) - 1
        window.home_page.project_list.setCurrentRow(target_row)
        template = group.root / "公共工具" / "template.html"
        first = write_product(archive, target, 0, template)
        tasks.generate_first_build_task(target.path, "")
        window.home_page.refresh_current_project()
        if archive.latest_product(target.path) != first:
            raise RuntimeError("首次产品识别失败。")
        save_widget(window, artifacts / "naming-first-product.png")

        write_product(archive, target, 1, template)
        second = write_product(archive, target, 2, template)
        window.home_page.refresh_current_project()
        if archive.latest_product(target.path) != second:
            raise RuntimeError("latest_product 未识别版本 2。")
        save_widget(window, artifacts / "naming-feedback-products.png")

        renamed = second.with_name("正负球最终调整.html")
        second.rename(renamed)
        window.home_page.refresh_current_project()
        if archive.latest_product(target.path) != renamed:
            raise RuntimeError("HTML 重命名后无法识别。")
        if window.home_page.notice_banner.isHidden():
            raise RuntimeError("产品重命名非阻断提示未显示。")
        save_widget(window, artifacts / "naming-rename-notice.png")

        legacy_root = root / "旧项目迁移预览"
        legacy_root.mkdir()
        (legacy_root / "AGENT任务规则.md").write_text("legacy", encoding="utf-8")
        legacy_manifest = {
            "schema_version": 2,
            "group_id": "legacy-preview",
            "group_name": legacy_root.name,
            "product_directory": "产品迭代",
        }
        (legacy_root / project_service.MANIFEST_NAME).write_text(
            json.dumps(legacy_manifest, ensure_ascii=False), encoding="utf-8"
        )
        for index, source in enumerate(sources[:3], start=1):
            project = legacy_root / f"项目{index}"
            (project / "原始需求").mkdir(parents=True)
            (project / "客户反馈").mkdir()
            (project / "产品迭代").mkdir()
            (project / "当前任务.md").write_text("", encoding="utf-8")
            (project / "项目记录.md").write_text("# 项目记录\n", encoding="utf-8")
            (project / "原始需求" / source.name).write_bytes(source.read_bytes())
        preview = project_service.preview_legacy_migration(legacy_root)
        dialog = QMessageBox(window)
        dialog.setWindowTitle("预览项目命名迁移")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText("迁移前将创建完整备份")
        dialog.setInformativeText(
            "\n".join(f"{old} → {new}" for old, new in preview)
        )
        dialog.setStyleSheet("QMessageBox QLabel { min-width: 520px; }")
        dialog.addButton("开始迁移", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        dialog.setMinimumSize(620, 360)
        dialog.resize(620, 360)
        dialog.show()
        app.processEvents()
        save_widget(dialog, artifacts / "naming-migration-preview.png")
        dialog.close()

        if window.home_page.work_scroll.horizontalScrollBar().maximum() != 0:
            raise RuntimeError("主页出现横向溢出。")
        window.close()
        app.processEvents()

    print("项目命名与稳定 ID 截图验收通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
