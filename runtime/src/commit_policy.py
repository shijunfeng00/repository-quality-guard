"""Profile-configurable commit-message policy for QG984 report validation."""

from __future__ import annotations

from collections.abc import Mapping
import re
from types import MappingProxyType
from typing import Any

_ALLOWED_KEYS = frozenset(
    {
        "subject_regex",
        "subject_example",
        "min_body_bullets",
        "body_bullet_regex",
    }
)
DEFAULT_COMMIT_POLICY: Mapping[str, Any] = MappingProxyType(
    {
        "subject_regex": "",
        "subject_example": "",
        "min_body_bullets": 0,
        "body_bullet_regex": "",
    }
)


def parse_commit_policy(raw: object) -> Mapping[str, Any]:
    """Validate a Profile commit policy at its JSON ingestion boundary.

    Args:
        raw: Optional ``commit_policy`` object from one Profile manifest.

    Returns:
        A frozen, complete mapping containing every supported policy field.

    Raises:
        ValueError: The policy shape, field type, range, or regular expression is
            invalid.
    """
    if raw is None:
        return DEFAULT_COMMIT_POLICY
    if not isinstance(raw, Mapping):
        raise ValueError("profile commit_policy must be an object")
    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"unknown profile commit_policy fields: {', '.join(unknown)}")

    values = dict(DEFAULT_COMMIT_POLICY)
    values.update(raw)
    subject_regex = values["subject_regex"]
    subject_example = values["subject_example"]
    min_body_bullets = values["min_body_bullets"]
    body_bullet_regex = values["body_bullet_regex"]
    if not isinstance(subject_regex, str):
        raise ValueError("profile commit_policy.subject_regex must be a string")
    if not isinstance(subject_example, str):
        raise ValueError("profile commit_policy.subject_example must be a string")
    if type(min_body_bullets) is not int or not 0 <= min_body_bullets <= 20:
        raise ValueError(
            "profile commit_policy.min_body_bullets must be an integer from 0 to 20"
        )
    if not isinstance(body_bullet_regex, str):
        raise ValueError("profile commit_policy.body_bullet_regex must be a string")
    for field, pattern in (
        ("subject_regex", subject_regex),
        ("body_bullet_regex", body_bullet_regex),
    ):
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(
                f"invalid profile commit_policy.{field}: {error}"
            ) from error

    return MappingProxyType(values)


def validate_commit_command(command: str, policy: Mapping[str, Any]) -> list[str]:
    """Validate one report commit command against a canonical Profile policy.

    Args:
        command: Complete ``git commit -m`` command captured from the report.
        policy: Complete policy produced by :func:`parse_commit_policy`.

    Returns:
        Human-readable validation errors; an empty list means the command satisfies
        the structural contract and the selected Profile policy.
    """
    lines = command.splitlines()
    subject_line = lines[0] if lines else ""
    prefix = 'git commit -m "'
    if not subject_line.startswith(prefix) or not command.endswith('"'):
        return ['提交命令必须保持一个完整的 `git commit -m "..."` 参数。']
    subject = subject_line[len(prefix) :].strip()
    if not subject:
        return ["提交主题不能为空。"]

    subject_regex = str(policy["subject_regex"])
    if subject_regex and re.fullmatch(subject_regex, subject) is None:
        example = str(policy["subject_example"]).strip()
        suffix = f"，例如 `{example}`" if example else ""
        return [f"提交主题不符合当前 Profile 的 commit_policy{suffix}。"]

    bullets = [line for line in lines[1:] if line.startswith("- ")]
    minimum = int(policy["min_body_bullets"])
    if len(bullets) < minimum:
        return [f"当前 Profile 要求提交正文至少包含 {minimum} 条 `- ` 摘要。"]
    bullet_regex = str(policy["body_bullet_regex"])
    if bullet_regex and any(
        re.fullmatch(bullet_regex, line) is None for line in bullets
    ):
        return ["提交正文摘要不符合当前 Profile 的 commit_policy。"]
    return []
