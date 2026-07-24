from __future__ import annotations

import sys
from pathlib import Path


def bundled_resource_root() -> Path:
    """Return the resource directory in source and frozen deployments."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "resources"
    return Path(__file__).resolve().parents[1] / "resources"
