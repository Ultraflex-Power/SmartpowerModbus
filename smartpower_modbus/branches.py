"""Firmware branches that this library knows how to address.

The set of registers a SmartPower module exposes depends on which firmware
branch was flashed. Use the ``FirmwareBranch`` enum to select the right map
when constructing a ``SmartPowerClient``.

When a new firmware branch ships:
1. Add a member here with the exact branch name from the firmware repo.
2. In ``registers.py``, append it to the ``branches=`` set of every register
   the new firmware exposes (or add new ``Register`` members for any
   genuinely new addresses).
"""

from __future__ import annotations

from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registers import Register


class FirmwareBranch(Enum):
    SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE = "SngleModule_5540_LF_MF_ExtPA_simple"
    MEGA_MAIN = "MegaMain"
    PRODUCTION_PHASE_1_FAST_1_15_BASE = "ProductionPhase1_Fast_1_15_base"
    GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT = "Gen_1_5_MOD-5537-110_24_outputs_pwm_limit"

    @classmethod
    def from_name(cls, name: str) -> "FirmwareBranch":
        """Resolve a branch by its exact firmware-repo name (case-insensitive)."""
        norm = name.strip().lower()
        for member in cls:
            if member.value.lower() == norm:
                return member
        from .exceptions import UnsupportedFirmwareBranchError
        raise UnsupportedFirmwareBranchError(
            f"Unknown firmware branch: {name!r}. Known: "
            + ", ".join(m.value for m in cls)
        )

    @cached_property
    def registers(self) -> "frozenset[Register]":
        """Set of every Register exposed by this branch (derived from registers.py)."""
        from .registers import Register
        return frozenset(r for r in Register if self in r.branches)
