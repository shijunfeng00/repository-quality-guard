"""Repository analysis source selection shared by scanners and snapshot caching."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from .config import GuardConfig

SUPPORTED_SOURCE_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
}


def source_language(path: str, config: GuardConfig) -> str:
    """Return the canonical analysis language for a repository-relative path.

    Args:
        path: Repository-relative POSIX path.
        config: Active quality-guard configuration.

    Returns:
        Canonical language name, or an empty string when the path is not analyzed.
    """
    if any(fnmatch(path, pattern) for pattern in config.exclude):
        return ""
    suffix = Path(path).suffix.lower()
    return (
        SUPPORTED_SOURCE_LANGUAGES[suffix]
        if suffix in SUPPORTED_SOURCE_LANGUAGES
        else ""
    )
