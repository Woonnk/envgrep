from __future__ import annotations

import ast
import bisect
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Pattern, Tuple


@dataclass(frozen=True)
class EnvRef:
    name: str
    path: str
    line: int
    default: Optional[str] = None


SHELL_BUILTINS = frozenset(
    {
        "BASH", "BASH_SOURCE", "BASHPID", "BASH_VERSION", "COLUMNS", "EDITOR",
        "EUID", "FUNCNAME", "GROUPS", "HISTFILE", "HOME", "HOSTNAME", "IFS",
        "LANG", "LC_ALL", "LC_CTYPE", "LINENO", "LINES", "LOGNAME", "MAIL",
        "OLDPWD", "OPTARG", "OPTIND", "OSTYPE", "PAGER", "PATH", "PIPESTATUS",
        "PPID", "PS1", "PS2", "PS3", "PS4", "PWD", "RANDOM", "REPLY", "SECONDS",
        "SHELL", "SHLVL", "TERM", "TMPDIR", "TZ", "UID", "USER", "VISUAL",
    }
)

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

_JS_PATTERNS = (
    re.compile(r"process\.env\.(" + _IDENT + r")"),
    re.compile(r"process\.env\[\s*['\"`]([^'\"`]+)['\"`]\s*\]"),
    re.compile(r"import\.meta\.env\.(" + _IDENT + r")"),
    re.compile(r"Deno\.env\.get\(\s*['\"`]([^'\"`]+)['\"`]"),
)

_GO_PATTERNS = (re.compile(r"os\.(?:Getenv|LookupEnv)\(\s*\"([^\"]+)\""),)

_RUBY_PATTERNS = (
    re.compile(r"ENV\[\s*['\"]([^'\"]+)['\"]\s*\]"),
    re.compile(r"ENV\.fetch\(\s*['\"]([^'\"]+)['\"]"),
)

_JVM_PATTERNS = (re.compile(r"System\.getenv\(\s*\"([^\"]+)\""),)

_PHP_PATTERNS = (
    re.compile(r"getenv\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"\$_ENV\[\s*['\"]([^'\"]+)['\"]\s*\]"),
)

_RUST_PATTERNS = (
    re.compile(r"env::var(?:_os)?\(\s*\"([^\"]+)\""),
    re.compile(r"(?:option_)?env!\(\s*\"([^\"]+)\""),
)

_SHELL_BRACED = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::?[-=](?P<default>[^}]*))?\}")
_SHELL_BARE = re.compile(r"\$([A-Z][A-Z0-9_]{1,})\b")
_SHELL_ASSIGNMENT = re.compile(
    r"^[ \t]*(?:export[ \t]+|local[ \t]+|readonly[ \t]+|declare[ \t]+(?:-\w+[ \t]+)*)?"
    r"([A-Z][A-Z0-9_]*)=(?P<rhs>.*)$",
    re.MULTILINE,
)
_SHELL_LOOP = re.compile(r"\bfor[ \t]+([A-Z][A-Z0-9_]*)[ \t]+in\b")
_SHELL_READ = re.compile(r"\bread[ \t]+(?:-\w+[ \t]+)*([A-Z][A-Z0-9_]*)")

EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".mts": "javascript",
    ".cts": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".astro": "javascript",
    ".go": "go",
    ".rb": "ruby",
    ".erb": "ruby",
    ".rake": "ruby",
    ".java": "jvm",
    ".kt": "jvm",
    ".kts": "jvm",
    ".scala": "jvm",
    ".php": "php",
    ".rs": "rust",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".bats": "shell",
}


def language_for(filename: str) -> Optional[str]:
    lowered = filename.lower()
    for extension, language in EXTENSIONS.items():
        if lowered.endswith(extension):
            return language
    if lowered in {"makefile", "dockerfile"}:
        return None
    return None


def _line_offsets(source: str) -> List[int]:
    offsets = [0]
    for index, character in enumerate(source):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _line_at(offsets: List[int], position: int) -> int:
    return bisect.bisect_right(offsets, position)


def _scan_patterns(
    source: str, path: str, patterns: Iterable[Pattern[str]]
) -> Iterator[EnvRef]:
    offsets = _line_offsets(source)
    for pattern in patterns:
        for match in pattern.finditer(source):
            yield EnvRef(match.group(1), path, _line_at(offsets, match.start()))


def _dotted_name(node: ast.AST) -> Optional[str]:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _literal_text(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _default_text(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return None
        if isinstance(node.value, bool):
            return str(node.value).lower()
        return str(node.value)
    return None


def _os_bindings(tree: ast.AST) -> Tuple[frozenset, frozenset]:
    mappings: set = set()
    accessors: set = set()

    def register_module(alias: str) -> None:
        mappings.add(f"{alias}.environ")
        accessors.add(f"{alias}.environ.get")
        accessors.add(f"{alias}.getenv")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is None:
                    if alias.name.split(".")[0] == "os":
                        register_module("os")
                elif alias.name == "os":
                    register_module(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            if node.module != "os" or node.level:
                continue
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == "environ":
                    mappings.add(bound)
                    accessors.add(f"{bound}.get")
                elif alias.name == "getenv":
                    accessors.add(bound)
    return frozenset(mappings), frozenset(accessors)


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str, mappings: frozenset, accessors: frozenset):
        self.path = path
        self._MAPPINGS = mappings
        self._ACCESSORS = accessors
        self.refs: List[EnvRef] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _dotted_name(node.func) in self._ACCESSORS and node.args:
            name = _literal_text(node.args[0])
            if name:
                default = _default_text(node.args[1]) if len(node.args) > 1 else None
                self.refs.append(EnvRef(name, self.path, node.lineno, default))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _dotted_name(node.value) in self._MAPPINGS:
            name = _literal_text(node.slice)
            if name:
                self.refs.append(EnvRef(name, self.path, node.lineno))
        self.generic_visit(node)


def _parse_python(source: str, path: str) -> List[EnvRef]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        fallback = (
            re.compile(r"os\.environ\[\s*['\"]([^'\"]+)['\"]\s*\]"),
            re.compile(
                r"(?:os\.environ\.get|os\.getenv|(?<![\w.])getenv)"
                r"\(\s*['\"]([^'\"]+)['\"]"
            ),
        )
        return list(_scan_patterns(source, path, fallback))
    mappings, accessors = _os_bindings(tree)
    if not mappings and not accessors:
        return []
    visitor = _PythonVisitor(path, mappings, accessors)
    visitor.visit(tree)
    return visitor.refs


def _shell_locals(source: str) -> frozenset:
    assigned: set = set()
    for match in _SHELL_ASSIGNMENT.finditer(source):
        name = match.group(1)
        rhs = match.group("rhs")
        if f"${name}" in rhs or "${" + name in rhs:
            continue
        assigned.add(name)
    for pattern in (_SHELL_LOOP, _SHELL_READ):
        for match in pattern.finditer(source):
            assigned.add(match.group(1))
    return frozenset(assigned)


def _parse_shell(source: str, path: str) -> List[EnvRef]:
    offsets = _line_offsets(source)
    skip = SHELL_BUILTINS | _shell_locals(source)
    found: List[EnvRef] = []
    for match in _SHELL_BRACED.finditer(source):
        name = match.group(1)
        if name in skip:
            continue
        default = match.group("default") or None
        found.append(EnvRef(name, path, _line_at(offsets, match.start()), default))
    for match in _SHELL_BARE.finditer(source):
        name = match.group(1)
        if name in skip:
            continue
        found.append(EnvRef(name, path, _line_at(offsets, match.start())))
    return found


_REGEX_LANGUAGES = {
    "javascript": _JS_PATTERNS,
    "go": _GO_PATTERNS,
    "ruby": _RUBY_PATTERNS,
    "jvm": _JVM_PATTERNS,
    "php": _PHP_PATTERNS,
    "rust": _RUST_PATTERNS,
}


def extract(source: str, path: str, language: str) -> List[EnvRef]:
    if language == "python":
        return _parse_python(source, path)
    if language == "shell":
        return _parse_shell(source, path)
    patterns = _REGEX_LANGUAGES.get(language)
    if not patterns:
        return []
    return list(_scan_patterns(source, path, patterns))
