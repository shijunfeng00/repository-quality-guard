from __future__ import annotations

import json
from pathlib import Path

from rqgtest import TestCase
from runtime.src.workflow import _resolve_audit_mode, build_parser


class DebtCliDefaultsCase(TestCase):
    """Keep CLI defaults and explicit audit modes deterministic."""

    def get_enterpoint(self):
        return _resolve_audit_mode

    def get_input_data(self):
        parser = build_parser()
        for argv in (["audit"], ["audit", "--mode", "ignore-diff"], ["audit", "--mode", "cleanup"]):
            yield ((parser.parse_args(argv),), {})

    def get_output_data(self):
        case = json.loads((Path(__file__).parent / "cases/debt-cli-defaults.json").read_text(encoding="utf-8"))
        for expected in case["expected"]["samples"]:
            yield expected

    def equals(self, expected, actual):
        return actual.mode == expected["mode"] and actual.debt_mode == expected["debt_mode"] and actual.staged is False and actual.diff_base is None
