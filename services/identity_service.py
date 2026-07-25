from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from uuid import UUID


PROJECT_CONFIG_NAME = "项目配置.json"
INVALID_NAME_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
COURSEWARE_META_NAMES = {
    "courseware-project-id",
    "courseware-artifact-id",
    "courseware-version",
    "courseware-feedback-round",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def sanitize_project_name(value: str, max_length: int = 80) -> str:
    """Return a stable Windows-safe file or directory stem."""
    name = INVALID_NAME_PATTERN.sub("_", str(value)).strip().rstrip(" .")
    if not name:
        name = "未命名项目"
    if name.upper().split(".", 1)[0] in RESERVED_NAMES:
        name += "_"
    if len(name) > max_length:
        suffix = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        name = f"{name[: max(1, max_length - 9)].rstrip()}-{suffix}"
    return name.rstrip(" .")


def unique_project_names(
    values: list[str] | tuple[str, ...], max_length: int = 80
) -> tuple[tuple[str, str], ...]:
    used_display: set[str] = set()
    used_directories: set[str] = set()
    result: list[tuple[str, str]] = []
    for raw_value in values:
        base = str(raw_value).strip()
        if not base:
            raise ValueError("项目名称不能为空。")
        candidate = base
        counter = 2
        while True:
            directory = sanitize_project_name(candidate, max_length)
            if (
                candidate.casefold() not in used_display
                and directory.casefold() not in used_directories
            ):
                break
            candidate = f"{base}（{counter}）"
            counter += 1
        used_display.add(candidate.casefold())
        used_directories.add(directory.casefold())
        result.append((candidate, directory))
    return tuple(result)


def read_json_object(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"配置格式无效：{path}")
    return data


def write_json_object(path: Path, data: dict) -> None:
    target = Path(path)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)


def valid_uuid(value: object) -> bool:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "meta":
            return
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        name = values.get("name", "").casefold()
        if name in COURSEWARE_META_NAMES:
            self.values[name] = values.get("content", "")


def read_courseware_meta(path: Path) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {}
    parser = _MetaParser()
    try:
        parser.feed(text)
    except Exception:
        return {}
    return parser.values


def write_courseware_meta(
    path: Path,
    project_id: str,
    artifact_id: str,
    version_number: int,
    feedback_round: int,
) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8-sig")
    for name in COURSEWARE_META_NAMES:
        text = re.sub(
            rf'<meta\b(?=[^>]*\bname\s*=\s*["\']{re.escape(name)}["\'])[^>]*>\s*',
            "",
            text,
            flags=re.IGNORECASE,
        )
    block = (
        f'<meta name="courseware-project-id" content="{project_id}">\n'
        f'<meta name="courseware-artifact-id" content="{artifact_id}">\n'
        f'<meta name="courseware-version" content="{version_number}">\n'
        f'<meta name="courseware-feedback-round" content="{feedback_round}">\n'
    )
    head_end = re.search(r"</head\s*>", text, flags=re.IGNORECASE)
    if head_end:
        text = text[: head_end.start()] + block + text[head_end.start() :]
    else:
        html_start = re.search(r"<html\b[^>]*>", text, flags=re.IGNORECASE)
        offset = html_start.end() if html_start else 0
        text = text[:offset] + "\n<head>\n" + block + "</head>\n" + text[offset:]
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)
