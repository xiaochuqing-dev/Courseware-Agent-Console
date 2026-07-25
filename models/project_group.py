from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectEntry:
    index: int
    name: str
    path: Path
    project_id: str = ""
    directory_name: str = ""
    renamed_from: str = ""

    @property
    def order(self) -> int:
        return self.index

    @property
    def display_name(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class ProjectGroup:
    root: Path
    projects: tuple[ProjectEntry, ...]
    group_id: str = ""
    migration_required: bool = False

    @property
    def name(self) -> str:
        return self.root.name
