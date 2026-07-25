from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

from .scanner import DEFAULT_EXCLUDES


@dataclass
class Config:
    env_file: str = ".env"
    example_file: str = ".env.example"
    excludes: List[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    ignore: List[str] = field(default_factory=list)
    header: Optional[str] = None


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _apply(config: Config, table: Dict[str, Any]) -> Config:
    config.env_file = str(table.get("env_file", config.env_file))
    config.example_file = str(table.get("example_file", config.example_file))
    config.ignore = _as_list(table.get("ignore", config.ignore))
    extra = _as_list(table.get("exclude", []))
    if extra:
        config.excludes = list(DEFAULT_EXCLUDES) + extra
    header = table.get("header")
    config.header = str(header) if header is not None else config.header
    return config


def load(root: Path) -> Config:
    config = Config()
    if tomllib is None:
        return config
    standalone = root / "envgrep.toml"
    if standalone.is_file():
        try:
            data = tomllib.loads(standalone.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return config
        return _apply(config, data.get("envgrep", data))
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return config
        table = data.get("tool", {}).get("envgrep")
        if isinstance(table, dict):
            return _apply(config, table)
    return config
