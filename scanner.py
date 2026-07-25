from __future__ import annotations

import fnmatch
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from .parsers import EnvRef, extract, language_for

DEFAULT_EXCLUDES = (
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".gradle",
    "__pycache__",
    "node_modules",
    "vendor",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "coverage",
    "htmlcov",
    "site-packages",
)

MAX_FILE_BYTES = 2_000_000


@dataclass
class ScanResult:
    refs: List[EnvRef] = field(default_factory=list)
    files_scanned: int = 0

    def names(self) -> List[str]:
        seen: Dict[str, None] = {}
        for ref in self.refs:
            seen.setdefault(ref.name, None)
        return sorted(seen)

    def by_name(self) -> Dict[str, List[EnvRef]]:
        grouped: Dict[str, List[EnvRef]] = defaultdict(list)
        for ref in self.refs:
            grouped[ref.name].append(ref)
        return dict(grouped)

    def defaults(self) -> Dict[str, Optional[str]]:
        resolved: Dict[str, Optional[str]] = {}
        for ref in self.refs:
            if ref.default and not resolved.get(ref.name):
                resolved[ref.name] = ref.default
            resolved.setdefault(ref.name, None)
        return resolved


def _is_excluded(relative: Path, patterns: Sequence[str]) -> bool:
    text = relative.as_posix()
    for pattern in patterns:
        if fnmatch.fnmatch(text, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in relative.parts):
            return True
    return False


def _readable_text(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace")


def iter_source_files(
    root: Path, excludes: Sequence[str]
) -> Iterable[Path]:
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            relative = child.relative_to(root)
            if _is_excluded(relative, excludes):
                continue
            if child.is_symlink():
                continue
            if child.is_dir():
                stack.append(child)
            elif language_for(child.name):
                yield child


def scan(
    root: Path,
    excludes: Sequence[str] = DEFAULT_EXCLUDES,
    ignore: Sequence[str] = (),
) -> ScanResult:
    root = root.resolve()
    result = ScanResult()
    ignored = set(ignore)
    for path in iter_source_files(root, excludes):
        language = language_for(path.name)
        if not language:
            continue
        source = _readable_text(path)
        if source is None:
            continue
        result.files_scanned += 1
        relative = path.relative_to(root).as_posix()
        for ref in extract(source, relative, language):
            if ref.name in ignored:
                continue
            if any(fnmatch.fnmatch(ref.name, pattern) for pattern in ignored):
                continue
            result.refs.append(ref)
    result.refs.sort(key=lambda ref: (ref.name, ref.path, ref.line))
    return result
