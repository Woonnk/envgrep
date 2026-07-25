from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_ASSIGNMENT = re.compile(
    r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"""
)


@dataclass
class EnvEntry:
    key: str
    value: str
    line: int
    comments: List[str] = field(default_factory=list)


@dataclass
class EnvFile:
    path: Path
    entries: Dict[str, EnvEntry] = field(default_factory=dict)
    exists: bool = True

    def keys(self) -> List[str]:
        return list(self.entries)

    def get(self, key: str) -> Optional[EnvEntry]:
        return self.entries.get(key)

    def __contains__(self, key: str) -> bool:
        return key in self.entries


def _strip_value(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    without_comment = raw.split(" #", 1)[0]
    return without_comment.strip()


def parse_text(text: str, path: Path) -> EnvFile:
    entries: Dict[str, EnvEntry] = {}
    pending: List[str] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            pending = []
            continue
        if stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
            continue
        match = _ASSIGNMENT.match(raw_line)
        if not match:
            pending = []
            continue
        key, raw_value = match.group(1), match.group(2)
        entries[key] = EnvEntry(key, _strip_value(raw_value), number, pending)
        pending = []
    return EnvFile(path=path, entries=entries)


def read(path: Path) -> EnvFile:
    if not path.is_file():
        return EnvFile(path=path, entries={}, exists=False)
    return parse_text(path.read_text(encoding="utf-8", errors="replace"), path)


def render(
    keys: List[str],
    previous: EnvFile,
    defaults: Dict[str, Optional[str]],
    header: Optional[str] = None,
) -> str:
    lines: List[str] = []
    if header:
        for entry in header.splitlines():
            lines.append(f"# {entry}".rstrip())
        lines.append("")
    for key in keys:
        existing = previous.get(key)
        if existing is not None:
            for comment in existing.comments:
                lines.append(f"# {comment}")
            lines.append(f"{key}={existing.value}")
        else:
            lines.append(f"{key}={defaults.get(key) or ''}")
    return "\n".join(lines).rstrip() + "\n"


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
