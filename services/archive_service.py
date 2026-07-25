from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .identity_service import (
    file_sha256,
    read_courseware_meta,
    sanitize_project_name,
    write_courseware_meta,
    write_json_object,
)
from .project_service import InvalidProjectGroupError, ProjectService


class ArchiveError(RuntimeError):
    pass


class NoProductVersionError(ArchiveError):
    pass


class ArchiveConflictError(ArchiveError):
    pass


@dataclass(frozen=True, slots=True)
class ProductNotice:
    kind: str
    path: Path
    artifact_id: str = ""
    old_name: str = ""
    new_name: str = ""
    message: str = ""


class ArchiveService:
    LEGACY_VERSION_PATTERN = re.compile(r"^第([1-9]\d*)轮修改\.html$", re.IGNORECASE)
    LEGACY_INITIAL_NAME = "初始版本.html"

    def __init__(self, project_service: ProjectService | None = None) -> None:
        self.project_service = project_service or ProjectService()

    @staticmethod
    def product_root(project_root: Path) -> Path:
        return Path(project_root) / "产品迭代"

    @staticmethod
    def expected_product_name(config: dict, version_number: int) -> str:
        base_name = sanitize_project_name(
            str(config.get("product_base_name") or config.get("display_name") or "课件"),
            96,
        )
        return f"{base_name}.html" if version_number == 0 else f"{base_name}（{version_number}）.html"

    def allocate_artifact(
        self, project_root: Path, version_number: int, feedback_round: int
    ) -> dict:
        project = Path(project_root).resolve()
        config = self.project_service.read_project_config(project)
        artifacts = config.setdefault("artifacts", [])
        if not isinstance(artifacts, list):
            artifacts = []
            config["artifacts"] = artifacts
        for artifact in artifacts:
            if (
                isinstance(artifact, dict)
                and int(artifact.get("version_number", -1)) == version_number
            ):
                return artifact
        expected = self.expected_product_name(config, version_number)
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
        self.project_service.write_project_config(project, config)
        return artifact

    def latest_product(self, project_root: Path) -> Path | None:
        project = Path(project_root).resolve()
        products_root = self.product_root(project)
        if not products_root.is_dir():
            return None
        files = tuple(
            path for path in products_root.iterdir() if path.is_file() and path.suffix.casefold() == ".html"
        )
        if not files:
            return None

        try:
            config = self.project_service.read_project_config(project)
        except InvalidProjectGroupError:
            config = None
        if config:
            artifacts = [
                item for item in config.get("artifacts", []) if isinstance(item, dict)
            ]
            artifacts.sort(key=lambda item: int(item.get("version_number", -1)), reverse=True)
            metadata = {path: read_courseware_meta(path) for path in files}
            hashes: dict[Path, str] = {}
            for artifact in artifacts:
                artifact_id = str(artifact.get("artifact_id", ""))
                if artifact_id:
                    matched = [
                        path
                        for path, meta in metadata.items()
                        if meta.get("courseware-artifact-id") == artifact_id
                    ]
                    if len(matched) == 1:
                        return matched[0]
                recorded_hash = str(artifact.get("sha256", ""))
                if recorded_hash:
                    matched = []
                    for path in files:
                        hashes.setdefault(path, file_sha256(path))
                        if hashes[path] == recorded_hash:
                            matched.append(path)
                    if len(matched) == 1:
                        return matched[0]
                names = {
                    str(artifact.get("current_name", "")),
                    str(artifact.get("expected_name", "")),
                    *(str(value) for value in artifact.get("aliases", [])),
                }
                matched = [path for path in files if path.name in names]
                if len(matched) == 1:
                    return matched[0]

            base = re.escape(
                sanitize_project_name(
                    str(config.get("product_base_name") or config.get("display_name") or "课件"),
                    96,
                )
            )
            current_pattern = re.compile(rf"^{base}（([1-9]\d*)）\.html$", re.IGNORECASE)
            versions = [
                (int(match.group(1)), path)
                for path in files
                if (match := current_pattern.fullmatch(path.name))
            ]
            if versions:
                return max(versions, key=lambda item: item[0])[1]
            initial = products_root / f"{sanitize_project_name(str(config.get('product_base_name') or config.get('display_name') or '课件'), 96)}.html"
            if initial.is_file():
                return initial

        legacy_versions = [
            (int(match.group(1)), path)
            for path in files
            if (match := self.LEGACY_VERSION_PATTERN.fullmatch(path.name))
        ]
        if legacy_versions:
            return max(legacy_versions, key=lambda item: item[0])[1]
        legacy_initial = products_root / self.LEGACY_INITIAL_NAME
        return legacy_initial if legacy_initial.is_file() else None

    def reconcile_product_files(self, project_root: Path) -> tuple[ProductNotice, ...]:
        project = Path(project_root).resolve()
        products_root = self.product_root(project)
        if not products_root.is_dir():
            return ()
        try:
            config = self.project_service.read_project_config(project)
        except InvalidProjectGroupError:
            return ()
        files = tuple(
            path for path in products_root.iterdir() if path.is_file() and path.suffix.casefold() == ".html"
        )
        notices: list[ProductNotice] = []
        changed = False
        matched_paths: set[Path] = set()
        for artifact in config.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id", ""))
            matched = [
                path
                for path in files
                if read_courseware_meta(path).get("courseware-artifact-id") == artifact_id
            ]
            if len(matched) != 1:
                names = {
                    str(artifact.get("current_name", "")),
                    str(artifact.get("expected_name", "")),
                    *(str(value) for value in artifact.get("aliases", [])),
                }
                matched = [path for path in files if path.name in names]
            if len(matched) != 1 and artifact.get("sha256"):
                matched = [path for path in files if file_sha256(path) == artifact["sha256"]]
            if len(matched) != 1:
                continue
            path = matched[0]
            matched_paths.add(path)
            current_name = str(artifact.get("current_name", ""))
            ignored = {str(value) for value in artifact.get("ignored_names", [])}
            if path.name != current_name and path.name not in ignored:
                notices.append(
                    ProductNotice(
                        "renamed",
                        path,
                        artifact_id,
                        current_name,
                        path.name,
                        f"检测到产品文件已更名：{current_name} → {path.name}",
                    )
                )
            digest = file_sha256(path)
            old_digest = str(artifact.get("sha256", ""))
            if old_digest and old_digest != digest:
                notices.append(
                    ProductNotice(
                        "content_changed",
                        path,
                        artifact_id,
                        message="检测到登记产品内容变化，验收状态已失效。",
                    )
                )
            if digest != old_digest:
                artifact["sha256"] = digest
                changed = True
        ignored_unregistered = {str(value) for value in config.get("ignored_unregistered_html", [])}
        for path in files:
            if path in matched_paths or path.name in ignored_unregistered:
                continue
            notices.append(
                ProductNotice(
                    "unregistered",
                    path,
                    new_name=path.name,
                    message=f"发现未登记 HTML，无法确定对应版本：{path.name}",
                )
            )
        if changed:
            self.project_service.write_project_config(project, config)
        return tuple(notices)

    def accept_product_rename(self, project_root: Path, artifact_id: str, new_name: str) -> None:
        project = Path(project_root).resolve()
        config = self.project_service.read_project_config(project)
        for artifact in config.get("artifacts", []):
            if isinstance(artifact, dict) and str(artifact.get("artifact_id")) == artifact_id:
                old_name = str(artifact.get("current_name", ""))
                aliases = artifact.setdefault("aliases", [])
                if old_name and old_name != new_name and old_name not in aliases:
                    aliases.append(old_name)
                artifact["current_name"] = new_name
                artifact["ignored_names"] = []
                self.project_service.write_project_config(project, config)
                self._append_rename_record(project, old_name, new_name, artifact_id)
                return
        raise ArchiveError("未找到对应 artifact_id。")

    def restore_product_name(self, project_root: Path, artifact_id: str) -> Path:
        project = Path(project_root).resolve()
        config = self.project_service.read_project_config(project)
        for artifact in config.get("artifacts", []):
            if not isinstance(artifact, dict) or str(artifact.get("artifact_id")) != artifact_id:
                continue
            expected = str(artifact.get("expected_name", ""))
            current = self._find_artifact_file(project, artifact_id)
            if current is None:
                raise ArchiveError("未找到登记的产品文件。")
            destination = self.product_root(project) / expected
            if destination.exists() and destination != current:
                raise ArchiveConflictError(f"规范名称已被占用，不会覆盖：{destination}")
            old_name = current.name
            current.rename(destination)
            aliases = artifact.setdefault("aliases", [])
            if old_name != expected and old_name not in aliases:
                aliases.append(old_name)
            artifact["current_name"] = expected
            artifact["sha256"] = file_sha256(destination)
            self.project_service.write_project_config(project, config)
            self._append_rename_record(project, old_name, expected, artifact_id)
            return destination
        raise ArchiveError("未找到对应 artifact_id。")

    def ignore_product_notice(self, project_root: Path, notice: ProductNotice) -> None:
        project = Path(project_root).resolve()
        config = self.project_service.read_project_config(project)
        if notice.kind == "renamed":
            for artifact in config.get("artifacts", []):
                if isinstance(artifact, dict) and str(artifact.get("artifact_id")) == notice.artifact_id:
                    ignored = artifact.setdefault("ignored_names", [])
                    if notice.new_name not in ignored:
                        ignored.append(notice.new_name)
        elif notice.kind == "unregistered":
            ignored = config.setdefault("ignored_unregistered_html", [])
            if notice.new_name not in ignored:
                ignored.append(notice.new_name)
        self.project_service.write_project_config(project, config)

    def bind_product(self, project_root: Path, html_path: Path, version_number: int | None = None) -> dict:
        project = Path(project_root).resolve()
        path = Path(html_path).resolve()
        if path.parent != self.product_root(project).resolve() or not path.is_file():
            raise ArchiveError("只能绑定当前项目“产品迭代”中的 HTML。")
        config = self.project_service.read_project_config(project)
        version = self._infer_version(config, path.name) if version_number is None else version_number
        artifacts = config.setdefault("artifacts", [])
        artifact = next(
            (
                item
                for item in artifacts
                if isinstance(item, dict) and int(item.get("version_number", -1)) == version
            ),
            None,
        )
        if artifact is None:
            artifact = self.allocate_artifact(project, version, version)
            config = self.project_service.read_project_config(project)
            artifact = next(
                item for item in config["artifacts"] if item["artifact_id"] == artifact["artifact_id"]
            )
        old_name = str(artifact.get("current_name", ""))
        if old_name and old_name != path.name:
            aliases = artifact.setdefault("aliases", [])
            if old_name not in aliases:
                aliases.append(old_name)
        artifact["current_name"] = path.name
        artifact["sha256"] = file_sha256(path)
        config["ignored_unregistered_html"] = [
            value for value in config.get("ignored_unregistered_html", []) if value != path.name
        ]
        write_courseware_meta(
            path,
            str(config["project_id"]),
            str(artifact["artifact_id"]),
            int(artifact["version_number"]),
            int(artifact.get("feedback_round", artifact["version_number"])),
        )
        artifact["sha256"] = file_sha256(path)
        self.project_service.write_project_config(project, config)
        return artifact

    def archive_root(self, group_root: Path) -> Path:
        group_path = Path(group_root).resolve()
        return group_path.parent / "已完成项目"

    def archive_destination(self, group_root: Path, project_name: str) -> Path:
        group_path = Path(group_root).resolve()
        return self.archive_root(group_path) / group_path.name / sanitize_project_name(project_name)

    def archive_project(self, group_root: Path, project_ref: str) -> Path:
        group_path = Path(group_root).resolve()
        group = self.project_service.load_project_group(group_path, allow_legacy=True)
        project = next(
            (
                item
                for item in group.projects
                if project_ref in {item.project_id, item.name, item.directory_name}
            ),
            None,
        )
        if project is None:
            raise ArchiveError(f"项目不存在或已被改名：{project_ref}")
        source = project.path
        if self.latest_product(source) is None:
            raise NoProductVersionError(
                f"当前项目没有可用产品版本：{source / '产品迭代'}。"
                "请确认已按当前任务生成 HTML。"
            )
        destination = self.archive_destination(group_path, project.display_name)
        if destination.exists():
            raise ArchiveConflictError(f"归档目标已存在，不会覆盖：{destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(source), str(destination))
        except Exception as exc:
            raise ArchiveError(
                f"项目归档失败。源目录：{source}；目标目录：{destination}。\n{exc}"
            ) from exc
        if not group.migration_required:
            manifest = self.project_service.read_manifest(group_path)
            manifest["projects"] = [
                record
                for record in manifest.get("projects", [])
                if not isinstance(record, dict)
                or str(record.get("project_id", "")) != project.project_id
            ]
            write_json_object(group_path / self.project_service.MANIFEST_NAME, manifest)
        return destination

    def archived_group_names(self, group_root: Path) -> tuple[str, ...]:
        root = self.archive_root(group_root)
        if not root.is_dir():
            return ()
        return tuple(sorted(path.name for path in root.iterdir() if path.is_dir()))

    def archived_projects(self, group_root: Path, group_name: str) -> tuple[Path, ...]:
        root = self.archive_root(group_root) / group_name
        if not root.is_dir():
            return ()
        projects = [path for path in root.iterdir() if path.is_dir()]
        return tuple(sorted(projects, key=lambda path: (self._archived_order(path), path.name.casefold())))

    def archived_project_name(self, project_root: Path) -> str:
        try:
            return str(self.project_service.read_project_config(project_root).get("display_name") or Path(project_root).name)
        except InvalidProjectGroupError:
            return Path(project_root).name

    def _find_artifact_file(self, project_root: Path, artifact_id: str) -> Path | None:
        root = self.product_root(project_root)
        if not root.is_dir():
            return None
        matched = [
            path
            for path in root.glob("*.html")
            if read_courseware_meta(path).get("courseware-artifact-id") == artifact_id
        ]
        return matched[0] if len(matched) == 1 else None

    @staticmethod
    def _append_rename_record(project: Path, old_name: str, new_name: str, artifact_id: str) -> None:
        with (project / "项目记录.md").open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n- 文件重命名：{old_name} → {new_name}\n"
                f"- artifact_id 未变化：{artifact_id}\n"
            )

    def _infer_version(self, config: dict, name: str) -> int:
        base = re.escape(sanitize_project_name(str(config.get("product_base_name") or config.get("display_name") or "课件"), 96))
        if re.fullmatch(rf"{base}\.html", name, re.IGNORECASE):
            return 0
        match = re.fullmatch(rf"{base}（([1-9]\d*)）\.html", name, re.IGNORECASE)
        if match:
            return int(match.group(1))
        legacy = self.LEGACY_VERSION_PATTERN.fullmatch(name)
        if legacy:
            return int(legacy.group(1))
        if name.casefold() == self.LEGACY_INITIAL_NAME.casefold():
            return 0
        existing = [
            int(item.get("version_number", -1))
            for item in config.get("artifacts", [])
            if isinstance(item, dict)
        ]
        return max(existing, default=-1) + 1

    def _archived_order(self, path: Path) -> int:
        try:
            return int(self.project_service.read_project_config(path).get("order", 999999))
        except InvalidProjectGroupError:
            legacy = re.fullmatch(r"项目([1-9]\d*)", path.name)
            return int(legacy.group(1)) if legacy else 999999
