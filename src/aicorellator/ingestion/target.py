"""Target — a normalized scan target.

Accepted kinds:
  folder   — a plain directory of sources
  repo     — a git repository root
  mount    — a mounted filesystem (unpacked image, bind mount)
  image    — a Docker/OCI image (Phase 3 — Agent responsibility)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Target:
    root: Path
    kind: str        # folder | repo | mount | image

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if self.kind not in ("folder", "repo", "mount", "image"):
            raise ValueError(f"unknown target kind: {self.kind}")
        if self.kind != "image" and not self.root.exists():
            raise FileNotFoundError(f"target root does not exist: {self.root}")


def ingest(path: str | Path, kind: str = "folder") -> Target:
    """Normalize a scan target.

    Phase 1 supports folder, repo, mount. `image` is accepted but
    the caller is responsible for unpacking it first.
    """
    return Target(root=Path(path), kind=kind)