from __future__ import annotations

import os
import sys
from typing import Iterable, List, Sequence

_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}


def color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class Printer:
    def __init__(self, stream=None, use_color: bool | None = None):
        self.stream = stream or sys.stdout
        self.use_color = color_enabled(self.stream) if use_color is None else use_color

    def paint(self, text: str, *styles: str) -> str:
        if not self.use_color or not styles:
            return text
        prefix = "".join(_CODES.get(style, "") for style in styles)
        return f"{prefix}{text}{_CODES['reset']}"

    def line(self, text: str = "") -> None:
        print(text, file=self.stream)

    def heading(self, text: str) -> None:
        self.line(self.paint(text, "bold"))

    def bullet(self, symbol: str, text: str, style: str) -> None:
        self.line(f"  {self.paint(symbol, style)} {text}")

    def table(self, rows: Sequence[Sequence[str]], headers: Sequence[str]) -> None:
        if not rows:
            return
        widths = [len(header) for header in headers]
        for row in rows:
            for index, cell in enumerate(row):
                widths[index] = max(widths[index], len(cell))
        header_line = "  ".join(
            header.ljust(widths[index]) for index, header in enumerate(headers)
        )
        self.line(self.paint(header_line.rstrip(), "bold"))
        self.line(self.paint("  ".join("-" * width for width in widths), "dim"))
        for row in rows:
            self.line(
                "  ".join(
                    cell.ljust(widths[index]) for index, cell in enumerate(row)
                ).rstrip()
            )


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def join_locations(locations: Iterable[str], limit: int = 3) -> str:
    items: List[str] = list(locations)
    shown = ", ".join(items[:limit])
    if len(items) > limit:
        shown += f" (+{len(items) - limit} more)"
    return shown
