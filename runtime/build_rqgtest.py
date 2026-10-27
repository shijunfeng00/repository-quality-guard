"""Build the bundled, pure-Python rqgtest wheel without third-party build tools.

Only the fixed rqgtest source layout is supported. A compliant wheel is used as
an ephemeral installation medium; never keep wheels in Git or .agents.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path
import re
import zipfile


def build_rqgtest_wheel(source: Path, output: Path) -> Path:
    """Validate the fixed source package and produce one PEP 427 wheel."""
    project = (source / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([0-9]+(?:\.[0-9]+){2})"\s*$', project, re.M)
    if match is None or not re.search(r'^name\s*=\s*"rqgtest"\s*$', project, re.M):
        raise ValueError("rqgtest package identity/version missing from pyproject.toml")
    version = match.group(1)
    module = source / "src/rqgtest"
    if not (module / "__init__.py").is_file() or not (
        module / "core/unit.py"
    ).is_file():
        raise ValueError("incomplete rqgtest Python source")
    paths = sorted(module.rglob("*.py"))
    if not paths or any(path.is_symlink() for path in paths):
        raise ValueError("invalid rqgtest source files")
    dist_info = f"rqgtest-{version}.dist-info"
    payload = {
        path.relative_to(module.parent).as_posix(): path.read_bytes()
        for path in paths
    }
    payload[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\nGenerator: rqgtest-stdlib-wheel\n"
        "Root-Is-Purelib: true\nTag: py3-none-any\n"
    ).encode("utf-8")
    payload[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\nName: rqgtest\nVersion: " + version +
        "\nSummary: Standard Case-based Python testing framework\n"
        "Requires-Python: >=3.10\n"
    ).encode("utf-8")
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(payload.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, f"sha256={digest}", len(data)))
    writer.writerow((f"{dist_info}/RECORD", "", ""))
    payload[f"{dist_info}/RECORD"] = record.getvalue().encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / f"rqgtest-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, data)
    return wheel
