"""Firmware-repo branch identifiers — **internal implementation detail**.

The public API uses ``SmartPowerModel`` (see ``smartpower_modbus.models``);
this enum exists only to carry the exact firmware-repo branch strings that
the slave will report and that ``ModBus.hpp`` was sourced from.

Do not use ``FirmwareBranch`` in new code. The mapping from public models
to firmware branches is in ``smartpower_modbus.models`` and intentionally
non-trivial (e.g. the firmware branch literally called ``Gen_1_5_…`` is
the firmware for the SmartPower Gen 1.5 model, while the branch called
``ProductionPhase1_…`` is the firmware for the Gen 1.0 model).
"""

from __future__ import annotations

from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import SmartPowerModel
    from .registers import Register


class FirmwareBranch(Enum):
    """Exact branch names from the SmartPower firmware repo.

    ``.value`` is the literal branch string as it appears in the firmware
    git repo. That string is the serialized contract — never change it.
    """

    SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE        = "SngleModule_5540_LF_MF_ExtPA_simple"
    GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT   = "Gen_1_5_MOD-5537-110_24_outputs_pwm_limit"
    PRODUCTION_PHASE_1_FAST_1_15_BASE           = "ProductionPhase1_Fast_1_15_base"
    MEGA_MAIN                                   = "MegaMain"

    @classmethod
    def from_name(cls, name: str) -> "FirmwareBranch":
        """Resolve a branch by its firmware-repo branch string or member name."""
        norm = name.strip().lower()
        for member in cls:
            if member.value.lower() == norm or member.name.lower() == norm:
                return member
        from .exceptions import UnsupportedFirmwareBranchError
        raise UnsupportedFirmwareBranchError(
            f"Unknown firmware branch: {name!r}. Known firmware branch strings: "
            + ", ".join(m.value for m in cls)
        )

    @cached_property
    def model(self) -> "SmartPowerModel":
        """The public ``SmartPowerModel`` that runs this firmware."""
        from .models import _BRANCH_TO_MODEL
        return _BRANCH_TO_MODEL[self]

    @cached_property
    def registers(self) -> "frozenset[Register]":
        """Set of every Register exposed by this firmware branch."""
        from .registers import Register
        return frozenset(r for r in Register if self in r.branches)
