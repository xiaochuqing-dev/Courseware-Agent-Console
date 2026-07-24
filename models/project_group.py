from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectEntry:
    index: int
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class ProjectGroup:
    root: Path
    projects: tuple[ProjectEntry, ...]

    @property
    def name(self) -> str:
        return self.root.name

