from __future__ import annotations

import re
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


@dataclass(frozen=True, slots=True)
class FeedbackSaveResult:
    saved_item_ids: tuple[str, ...]
    saved_paths: tuple[Path, ...]
    errors: tuple[str, ...]


class FeedbackService:
    ROUND_PATTERN = re.compile(r"^第([1-9]\d*)轮$")
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

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

    def pending_from_file(
        self, source: Path, reserved_names: set[str] | None = None
    ) -> PendingFeedback:
        path = Path(source).resolve()
        if path.is_dir():
            raise ValueError("请拖入具体文件，不支持导入文件夹。")
        if not path.is_file():
            raise FileNotFoundError(f"文件不存在：{path}")
        name = self.unique_name(path.name, reserved_names or set())
        kind = "image" if path.suffix.lower() in self.IMAGE_SUFFIXES else "file"
        return PendingFeedback(uuid4().hex, name, kind, source_path=path)

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
        return PendingFeedback(uuid4().hex, unique, kind, content=bytes(content))

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
