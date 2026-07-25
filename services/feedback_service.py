from __future__ import annotations

import re
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class PendingFeedback:
    item_id: str
    name: str
    kind: str
    source_path: Path | None = None
    content: bytes | None = None
    preview: str = ""
    size_bytes: int = 0
    detail: str = ""
    status: str = "等待保存"
    error: str = ""
    fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class FeedbackSaveResult:
    saved_item_ids: tuple[str, ...]
    saved_paths: tuple[Path, ...]
    errors: tuple[str, ...]


class FeedbackService:
    ROUND_PATTERN = re.compile(r"^第([1-9]\d*)轮$")
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
    SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | {".pdf", ".txt"}
    MAX_FILE_SIZE = 50 * 1024 * 1024
    MAX_IMAGE_SIZE = 25 * 1024 * 1024
    MAX_TEXT_SIZE = 5 * 1024 * 1024

    def scan_rounds(self, project_root: Path) -> tuple[int, ...]:
        feedback_root = Path(project_root) / "客户反馈"
        if not feedback_root.is_dir():
            return ()
        rounds: list[int] = []
        for path in feedback_root.iterdir():
            if not path.is_dir():
                continue
            match = self.ROUND_PATTERN.fullmatch(path.name)
            if match:
                rounds.append(int(match.group(1)))
        return tuple(sorted(rounds))

    def latest_round(self, project_root: Path) -> int | None:
        rounds = self.scan_rounds(project_root)
        return rounds[-1] if rounds else None

    def saved_items(
        self, project_root: Path, round_number: int
    ) -> tuple[PendingFeedback, ...]:
        round_root = Path(project_root) / "客户反馈" / f"第{round_number}轮"
        if not round_root.is_dir():
            return ()
        items: list[PendingFeedback] = []
        for path in sorted(round_root.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file():
                continue
            try:
                parsed = self.pending_from_file(path)
                items.append(
                    PendingFeedback(
                        item_id=parsed.item_id,
                        name=parsed.name,
                        kind=parsed.kind,
                        source_path=parsed.source_path,
                        preview=parsed.preview,
                        size_bytes=parsed.size_bytes,
                        detail=parsed.detail,
                        status="已保存",
                        fingerprint=parsed.fingerprint,
                    )
                )
            except Exception as exc:
                size = path.stat().st_size if path.exists() else 0
                items.append(
                    PendingFeedback(
                        item_id=uuid4().hex,
                        name=path.name,
                        kind="file",
                        source_path=path,
                        size_bytes=size,
                        detail=self.format_size(size),
                        status="解析失败",
                        error=str(exc),
                    )
                )
        return tuple(items)

    def pending_from_file(
        self, source: Path, reserved_names: set[str] | None = None
    ) -> PendingFeedback:
        path = Path(source).resolve()
        if path.is_dir():
            raise ValueError("请拖入具体文件，不支持导入文件夹。")
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")
        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise ValueError(
                f"不支持 {suffix or '无扩展名'} 格式。当前支持 PDF、TXT、PNG、JPG、JPEG。"
            )
        size = path.stat().st_size
        if size == 0:
            raise ValueError("文件为空，无法作为反馈材料导入。")
        limit = self._size_limit(suffix)
        if size > limit:
            raise ValueError(
                f"文件大小为 {self.format_size(size)}，超过 {self.format_size(limit)} 上限。"
            )
        name = self.unique_name(path.name, reserved_names or set())
        fingerprint = self._file_sha256(path)
        if suffix in self.IMAGE_SUFFIXES:
            from PySide6.QtGui import QImageReader

            reader = QImageReader(str(path))
            if not reader.canRead():
                raise ValueError("无法读取该图片，请确认文件未损坏且扩展名正确。")
            dimensions = reader.size()
            detail = f"{suffix.removeprefix('.').upper()} · {dimensions.width()}×{dimensions.height()} · {self.format_size(size)}"
            return PendingFeedback(
                uuid4().hex,
                name,
                "image",
                source_path=path,
                size_bytes=size,
                detail=detail,
                fingerprint=fingerprint,
            )
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader

                reader = PdfReader(str(path))
                if reader.is_encrypted:
                    raise ValueError("无法读取该 PDF，请确认文件未加密或损坏。")
                pages = len(reader.pages)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(
                    "无法读取该 PDF，请确认文件未加密或损坏。"
                ) from exc
            if pages <= 0:
                raise ValueError("PDF 没有可读取页面。")
            return PendingFeedback(
                uuid4().hex,
                name,
                "pdf",
                source_path=path,
                size_bytes=size,
                detail=f"PDF · {pages} 页 · {self.format_size(size)}",
                fingerprint=fingerprint,
            )
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise ValueError("TXT 必须是可读取的 UTF-8 文本。") from exc
        if not text.strip():
            raise ValueError("TXT 文件为空，无法作为反馈材料导入。")
        return PendingFeedback(
            uuid4().hex,
            name,
            "text",
            source_path=path,
            preview=" ".join(text.split())[:120],
            size_bytes=size,
            detail=f"TXT · {len(text)} 字 · {self.format_size(size)}",
            fingerprint=fingerprint,
        )

    def pending_from_text(
        self, text: str, reserved_names: set[str] | None = None
    ) -> PendingFeedback:
        content = text.strip()
        if not content:
            raise ValueError("剪贴板中没有可保存的文字。")
        occupied = reserved_names or set()
        name = "补充说明.txt"
        index = 2
        while name in occupied:
            name = f"补充说明-{index}.txt"
            index += 1
        preview = " ".join(content.split())[:80]
        return PendingFeedback(
            uuid4().hex,
            name,
            "text",
            content=(content + "\n").encode("utf-8"),
            preview=preview,
            size_bytes=len((content + "\n").encode("utf-8")),
            detail=f"粘贴文字 · {len(content)} 字",
            fingerprint=hashlib.sha256(content.encode("utf-8")).hexdigest().upper(),
        )

    def pending_from_bytes(
        self,
        name: str,
        content: bytes,
        kind: str = "file",
        reserved_names: set[str] | None = None,
    ) -> PendingFeedback:
        if not content:
            raise ValueError("待保存内容为空。")
        safe_name = Path(name).name.strip()
        if not safe_name:
            raise ValueError("文件名不能为空。")
        unique = self.unique_name(safe_name, reserved_names or set())
        payload = bytes(content)
        if len(payload) > self.MAX_IMAGE_SIZE:
            raise ValueError(
                f"剪贴板图片超过 {self.format_size(self.MAX_IMAGE_SIZE)} 上限。"
            )
        detail = f"{Path(unique).suffix.removeprefix('.').upper() or kind} · {self.format_size(len(payload))}"
        if kind == "image":
            from PySide6.QtGui import QImage

            image = QImage.fromData(payload)
            if image.isNull():
                raise ValueError("剪贴板图片无法解析。")
            detail = f"PNG · {image.width()}×{image.height()} · {self.format_size(len(payload))}"
        return PendingFeedback(
            uuid4().hex,
            unique,
            kind,
            content=payload,
            size_bytes=len(payload),
            detail=detail,
            fingerprint=hashlib.sha256(payload).hexdigest().upper(),
        )

    def save_pending(
        self,
        project_root: Path,
        round_number: int,
        items: list[PendingFeedback],
    ) -> FeedbackSaveResult:
        project_path = Path(project_root).resolve()
        if not project_path.is_dir():
            raise FileNotFoundError(f"项目目录不存在：{project_path}")
        if round_number <= 0:
            raise ValueError("反馈轮次必须大于 0。")
        if not items:
            raise ValueError("待保存反馈列表为空。")

        target_root = project_path / "客户反馈" / f"第{round_number}轮"
        round_existed = target_root.exists()
        target_root.mkdir(parents=True, exist_ok=True)
        saved_ids: list[str] = []
        saved_paths: list[Path] = []
        errors: list[str] = []
        for item in items:
            target = target_root / self.unique_name(
                item.name, {path.name for path in target_root.iterdir()}
            )
            if len(str(target.resolve())) >= 240:
                errors.append(
                    f"{item.name}：目标路径过长，请缩短项目组、轮次资料或文件名称。"
                    f"目标路径：{target}"
                )
                continue
            temporary = target_root / f".{target.name}.saving-{uuid4().hex}"
            try:
                if item.source_path is not None:
                    if not item.source_path.is_file():
                        raise FileNotFoundError(f"源文件已不存在：{item.source_path}")
                    shutil.copy2(item.source_path, temporary)
                elif item.content is not None:
                    temporary.write_bytes(item.content)
                else:
                    raise ValueError("反馈项没有可保存内容。")
                temporary.replace(target)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                errors.append(f"{item.name}：{exc}")
                continue
            saved_ids.append(item.item_id)
            saved_paths.append(target)
        if not round_existed and not saved_paths:
            try:
                target_root.rmdir()
            except OSError:
                pass
        return FeedbackSaveResult(tuple(saved_ids), tuple(saved_paths), tuple(errors))

    @staticmethod
    def unique_name(name: str, occupied: set[str]) -> str:
        if name not in occupied:
            return name
        path = Path(name)
        stem = path.stem
        suffix = path.suffix
        index = 2
        while True:
            candidate = f"{stem} ({index}){suffix}"
            if candidate not in occupied:
                return candidate
            index += 1

    @classmethod
    def _size_limit(cls, suffix: str) -> int:
        if suffix == ".txt":
            return cls.MAX_TEXT_SIZE
        if suffix in cls.IMAGE_SUFFIXES:
            return cls.MAX_IMAGE_SIZE
        return cls.MAX_FILE_SIZE

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest().upper()

    @staticmethod
    def format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"
