from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .process_utils import run_hidden_process
from .resource_paths import bundled_resource_root


APP_VERSION = "1.1.0"


@dataclass(frozen=True, slots=True)
class BuildInfo:
    version: str
    commit_sha: str
    build_date: str

    @property
    def short_sha(self) -> str:
        return self.commit_sha[:8] if self.commit_sha else "dev"

    @property
    def display_text(self) -> str:
        identity = self.short_sha if self.commit_sha else self.build_date
        return f"v{self.version} · {identity}"


@lru_cache(maxsize=1)
def current_build_info() -> BuildInfo:
    injected = _read_injected_build_info()
    if injected is not None:
        return injected
    commit = os.environ.get("COURSEWARE_BUILD_SHA", "").strip()
    build_date = os.environ.get("COURSEWARE_BUILD_DATE", "").strip()
    if not commit:
        commit = _development_commit_sha()
    return BuildInfo(
        os.environ.get("COURSEWARE_APP_VERSION", APP_VERSION).strip() or APP_VERSION,
        commit,
        build_date or date.today().isoformat(),
    )


def _read_injected_build_info() -> BuildInfo | None:
    path = bundled_resource_root() / "build_info.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            return None
        return BuildInfo(
            str(value.get("version") or APP_VERSION),
            str(value.get("commit_sha") or ""),
            str(value.get("build_date") or ""),
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _development_commit_sha() -> str:
    repository = Path(__file__).resolve().parents[1]
    try:
        result = run_hidden_process(
            ["git", "rev-parse", "--short=12", "HEAD"], timeout=2
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
