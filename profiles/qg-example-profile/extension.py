"""Executable example of project-specific static-analysis extensions."""

from __future__ import annotations

from runtime.src.profile_api import (
    CallableContract,
    QualityGuardProfile,
    StableMappingContract,
)


class Profile(QualityGuardProfile):
    """Demonstrate project-specific static contracts.

    The example keeps rule levels and path policy in ``profile.json`` while
    using Python only for analysis contracts that need executable behavior.
    """

    name = "qg-example-profile"

    def configure(self) -> None:
        """Register example state and adapter contracts from JSON settings.

        Returns:
            None.
        """
        super().configure()
        state_path = str(self.settings["state_contract_path"])
        state_type = str(self.settings["state_contract_type"])
        adapter_path = str(self.settings["adapter_path"])
        adapter_type = str(self.settings["adapter_type"])

        self.add_values(
            "stable_mapping_contracts",
            StableMappingContract(
                state_path,
                state_type,
                channel="state_keys",
                relative_severity="warning",
                relative_code="QG182",
                review_kind=f"{state_type} keys",
                state_type=state_type,
                state_source_suffix="state.py",
                state_conventional_names=("state", "runtime_state"),
                state_constructor_fields=("initial", "resources", "evidence"),
                state_writer_methods=("update", "define_field"),
                state_key_writer_methods=("update", "define_field"),
            ),
        )
        self.add_values(
            "callable_contracts",
            CallableContract(
                adapter_path,
                f"{adapter_type}.generate",
                (
                    ("self", "positional_or_keyword", False),
                    ("messages", "positional_or_keyword", False),
                    ("stop", "positional_or_keyword", True),
                    ("kwargs", "var_keyword", False),
                ),
                ("ModelResponse",),
                False,
            ),
            CallableContract(
                adapter_path,
                f"{adapter_type}.stream",
                (
                    ("self", "positional_or_keyword", False),
                    ("messages", "positional_or_keyword", False),
                    ("stop", "positional_or_keyword", True),
                    ("kwargs", "var_keyword", False),
                ),
                ("Iterator", "ModelChunk"),
                False,
            ),
        )
