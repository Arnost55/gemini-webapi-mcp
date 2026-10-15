"""Read-only preflight and copy-based migration helpers for persistent state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import sqlite3


@dataclass(frozen=True)
class StatePreflight:
    conversations_exists: bool
    conversations_schema: int | None
    registry_exists: bool
    profile_root_exists: bool


def inspect_state(conversations: Path, registry: Path, profile_root: Path) -> StatePreflight:
    schema = None
    if conversations.is_file():
        with sqlite3.connect(f"file:{conversations}?mode=ro", uri=True) as db:
            schema = int(db.execute("PRAGMA user_version").fetchone()[0])
    return StatePreflight(conversations.is_file(), schema, registry.is_file(), profile_root.is_dir())


def copy_for_migration(source: Path, destination: Path) -> Path:
    """Create an exclusive synthetic copy; the source is never mutated."""
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    shutil.copystat(source, destination)
    return destination


def preflight_dict(value: StatePreflight) -> dict[str, object]:
    return asdict(value)
