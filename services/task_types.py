from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    FIRST_BUILD = "first_build"
    FEEDBACK_MODIFICATION = "feedback_modification"

    @property
    def display_name(self) -> str:
        if self is TaskType.FEEDBACK_MODIFICATION:
            return "反馈修改"
        return "首次制作"
