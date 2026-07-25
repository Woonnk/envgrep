from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import __version__, config as config_module, envfile
from .report import Printer, join_locations, truncate
from .scanner import ScanResult, scan

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="envgrep",
        description="Find every environment variable your code reads, and keep .env.example honest.",
    )
    parser.add_argument("--version", action="version", version=f"envgrep {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    for name, help_text in (
        ("scan", "list every environment variable referenced in the codebase"),
        ("check", "fail if the example file does not match the code"),
        ("sync", "rewrite the example file from what the code actually reads"),
        ("diff", "show which variables your local env file is missing"),
    ):
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("path", nargs="?", default=".", help="directory to scan")
        sub.add_argument("--env-file", dest="env_file", help="path to your local env file")
        sub.add_argument(
            "--example-file", dest="example_file", help="path to the tracked example file"
        )
        sub.add_argument(
            "--ignore",
            action="append",
            default=[],
            metavar="NAME",
            help="variable name or glob to skip (repeatable)",
        )
        sub.add_argument(
            "--exclude",
            action="append",
            default=[],
            metavar="GLOB",
            help="path glob to skip (repeatable)",
        )
        sub.add_argument("--json", action="store_true", help="emit machine readable output")
        sub.add_argument("--no-color", action="store_true", help="disable ANSI color")
        if name == "sync":
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="print the file that would be written",
            )
            sub.add_argument(
                "--keep-orphans",
                action="store_true",
                help="retain example entries no longer found in code",
            )
    return parser


def _resolve(args: argparse.Namespace):
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    settings = config_module.load(root)
    if args.env_file:
        settings.env_file = args.env_file
    if args.example_file:
        settings.example_file = args.example_file
    settings.ignore = list(settings.ignore) + list(args.ignore)
    settings.excludes = list(settings.excludes) + list(args.exclude)
    return root, settings


def _locations(result: ScanResult, name: str, limit: int = 3) -> str:
    grouped = result.by_name().get(name, [])
    return join_locations(f"{ref.path}:{ref.line}" for ref in grouped)


def _command_scan(args, printer: Printer) -> int:
    root, settings = _resolve(args)
    result = scan(root, settings.excludes, settings.ignore)
    names = result.names()
    defaults = result.defaults()

    if args.json:
        payload = {
            "files_scanned": result.files_scanned,
            "variables": [
                {
                    "name": name,
                    "default": defaults.get(name),
                    "references": [
                        {"file": ref.path, "line": ref.line}
                        for ref in result.by_name()[name]
                    ],
                }
                for name in names
            ],
        }
        printer.line(json.dumps(payload, indent=2))
        return EXIT_OK

    if not names:
        printer.line("No environment variables referenced.")
        return EXIT_OK

    name_width = max(len(name) for name in names)
    default_width = min(20, max((len(defaults.get(n) or "") for n in names), default=0))
    available = shutil.get_terminal_size(fallback=(100, 24)).columns
    location_width = max(24, available - name_width - default_width - 6)
    rows = [
        [
            name,
            truncate(defaults.get(name) or "", 20),
            truncate(_locations(result, name), location_width),
        ]
        for name in names
    ]
    printer.table(rows, ["VARIABLE", "DEFAULT", "USED IN"])
    printer.line()
    printer.line(
        printer.paint(
            f"{len(names)} variables across {result.files_scanned} files", "dim"
        )
    )
    return EXIT_OK


def _compare(result: ScanResult, example: envfile.EnvFile):
    used = result.names()
    documented = example.keys()
    missing = [name for name in used if name not in example]
    orphaned = [name for name in documented if name not in set(used)]
    return missing, orphaned


def _command_check(args, printer: Printer) -> int:
    root, settings = _resolve(args)
    result = scan(root, settings.excludes, settings.ignore)
    example_path = root / settings.example_file
    example = envfile.read(example_path)
    missing, orphaned = _compare(result, example)

    if args.json:
        printer.line(
            json.dumps(
                {
                    "example_file": settings.example_file,
                    "example_exists": example.exists,
                    "missing": missing,
                    "orphaned": orphaned,
                    "ok": not missing and not orphaned and example.exists,
                },
                indent=2,
            )
        )
        return EXIT_OK if not missing and not orphaned and example.exists else EXIT_DRIFT

    if not example.exists:
        printer.line(
            printer.paint("!", "red")
            + f" {settings.example_file} does not exist. Run: envgrep sync"
        )
        return EXIT_DRIFT

    if missing:
        width = max(len(name) for name in missing)
        printer.heading(f"Missing from {settings.example_file}")
        for name in missing:
            where = printer.paint(_locations(result, name), "dim")
            printer.bullet("+", f"{name.ljust(width)}  {where}", "red")
        printer.line()

    if orphaned:
        printer.heading(f"In {settings.example_file} but never read")
        for name in orphaned:
            printer.bullet("-", name, "yellow")
        printer.line()

    if missing or orphaned:
        printer.line(
            printer.paint(
                f"{len(missing)} missing, {len(orphaned)} orphaned. Run: envgrep sync",
                "bold",
            )
        )
        return EXIT_DRIFT

    printer.line(
        printer.paint("\u2713", "green")
        + f" {settings.example_file} matches the code ({len(result.names())} variables)"
    )
    return EXIT_OK


def _command_sync(args, printer: Printer) -> int:
    root, settings = _resolve(args)
    result = scan(root, settings.excludes, settings.ignore)
    example_path = root / settings.example_file
    example = envfile.read(example_path)
    missing, orphaned = _compare(result, example)

    keys = result.names()
    if args.keep_orphans:
        keys = keys + [name for name in example.keys() if name not in set(keys)]

    content = envfile.render(keys, example, result.defaults(), settings.header)

    if args.dry_run:
        printer.line(content.rstrip())
        return EXIT_DRIFT if missing or orphaned else EXIT_OK

    envfile.write(example_path, content)
    added = len(missing)
    removed = 0 if args.keep_orphans else len(orphaned)
    printer.line(
        printer.paint("\u2713", "green")
        + f" wrote {settings.example_file} "
        + printer.paint(f"(+{added} added, -{removed} removed)", "dim")
    )
    return EXIT_OK


def _command_diff(args, printer: Printer) -> int:
    root, settings = _resolve(args)
    result = scan(root, settings.excludes, settings.ignore)
    local = envfile.read(root / settings.env_file)
    example = envfile.read(root / settings.example_file)

    required = result.names() or example.keys()
    defaults = result.defaults()
    absent = [
        name
        for name in required
        if name not in local and not defaults.get(name)
    ]
    optional = [
        name for name in required if name not in local and defaults.get(name)
    ]

    if args.json:
        printer.line(
            json.dumps(
                {
                    "env_file": settings.env_file,
                    "env_exists": local.exists,
                    "required_missing": absent,
                    "optional_missing": optional,
                },
                indent=2,
            )
        )
        return EXIT_DRIFT if absent else EXIT_OK

    if not local.exists:
        printer.line(
            printer.paint("!", "yellow") + f" {settings.env_file} does not exist"
        )

    if absent:
        printer.heading(f"Required, not set in {settings.env_file}")
        for name in absent:
            printer.bullet("\u00d7", name, "red")
        printer.line()

    if optional:
        printer.heading("Missing but defaulted in code")
        for name in optional:
            printer.bullet("~", f"{name}={defaults[name]}", "yellow")
        printer.line()

    if not absent:
        printer.line(
            printer.paint("\u2713", "green") + f" {settings.env_file} has everything required"
        )
        return EXIT_OK
    return EXIT_DRIFT


_COMMANDS = {
    "scan": _command_scan,
    "check": _command_check,
    "sync": _command_sync,
    "diff": _command_diff,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_OK
    printer = Printer(use_color=False if args.no_color or args.json else None)
    try:
        return _COMMANDS[args.command](args, printer)
    except NotADirectoryError as error:
        print(f"envgrep: not a directory: {error}", file=sys.stderr)
        return EXIT_ERROR
    except OSError as error:
        print(f"envgrep: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
