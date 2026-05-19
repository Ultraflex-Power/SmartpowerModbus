"""Public-facing SmartPower model identifiers and the centralized
public-model ↔ firmware-branch mapping.

This is the only place that translates between what the customer-facing
world calls things ("SmartPowerGen_2.0") and what the firmware repo
internally calls things ("MegaMain"). Adding a new SmartPower model means
adding one enum member and one entry to ``_MODEL_TO_BRANCH`` — nothing
else in the public API changes.

Mapping::

    SmartPowerModel.SOLO    ↔ "SmartPowerSolo"     ↔ SngleModule_5540_LF_MF_ExtPA_simple
    SmartPowerModel.GEN_1_0 ↔ "SmartPowerGen_1.0"  ↔ ProductionPhase1_Fast_1_15_base
    SmartPowerModel.GEN_1_5 ↔ "SmartPowerGen_1.5"  ↔ Gen_1_5_MOD-5537-110_24_outputs_pwm_limit
    SmartPowerModel.GEN_2_0 ↔ "SmartPowerGen_2.0"  ↔ MegaMain

The firmware branch strings are internal implementation details — they may
move or be renamed in the firmware repo. The public model names are the
stable contract.
"""

from __future__ import annotations

import warnings
from enum import Enum
from typing import TYPE_CHECKING

from .branches import FirmwareBranch
from .exceptions import UnsupportedFirmwareBranchError

if TYPE_CHECKING:
    from .registers import Register


class SmartPowerModel(Enum):
    """Public SmartPower product identifiers.

    ``.value`` is the canonical public model name (e.g. ``"SmartPowerGen_2.0"``)
    — that string is the stable identifier for CLI args, config files,
    logs, and any wire protocol that needs to identify the product.
    """

    SOLO    = "SmartPowerSolo"
    GEN_1_0 = "SmartPowerGen_1.0"
    GEN_1_5 = "SmartPowerGen_1.5"
    GEN_2_0 = "SmartPowerGen_2.0"

    @property
    def firmware_branch(self) -> FirmwareBranch:
        """The internal firmware-repo branch that ships on this model."""
        return _MODEL_TO_BRANCH[self]

    @classmethod
    def from_name(cls, name: str) -> "SmartPowerModel":
        """Resolve a model from any of:

        - canonical public name: ``"SmartPowerGen_2.0"``
        - Python enum member name: ``"GEN_2_0"`` or ``"SmartPowerModel.GEN_2_0"``
        - firmware-repo branch string: ``"MegaMain"`` (internal — a
          ``DeprecationWarning`` is issued because the firmware branch is
          implementation detail and may change)
        - legacy ``FirmwareBranch`` member name: ``"MEGA_MAIN"`` (same warning)

        Case-insensitive.
        """
        s = name.strip()
        if s.lower().startswith("smartpowermodel."):
            s = s.split(".", 1)[1]
        norm = s.lower()

        # 1. Match by canonical public value first.
        for m in cls:
            if m.value.lower() == norm:
                return m

        # 2. Match by Python enum member name (e.g. "GEN_2_0").
        for m in cls:
            if m.name.lower() == norm:
                return m

        # 3. Match by firmware branch string or FirmwareBranch member name
        #    (legacy / debug usage — emit a deprecation warning).
        try:
            fb = FirmwareBranch.from_name(s)
        except UnsupportedFirmwareBranchError:
            pass
        else:
            warnings.warn(
                f"Resolving SmartPower model from firmware branch identifier "
                f"{name!r} is deprecated; pass a SmartPowerModel value such as "
                f"{_BRANCH_TO_MODEL[fb].value!r} instead.",
                DeprecationWarning, stacklevel=2,
            )
            return _BRANCH_TO_MODEL[fb]

        raise UnsupportedFirmwareBranchError(
            f"Unknown SmartPower model: {name!r}. Known models: "
            + ", ".join(m.value for m in cls)
        )

    @property
    def registers(self) -> "frozenset[Register]":
        """The set of registers exposed by this model's firmware."""
        return self.firmware_branch.registers


_MODEL_TO_BRANCH: dict[SmartPowerModel, FirmwareBranch] = {
    SmartPowerModel.SOLO:    FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE,
    SmartPowerModel.GEN_1_0: FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE,
    SmartPowerModel.GEN_1_5: FirmwareBranch.GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT,
    SmartPowerModel.GEN_2_0: FirmwareBranch.MEGA_MAIN,
}

_BRANCH_TO_MODEL: dict[FirmwareBranch, SmartPowerModel] = {
    b: m for m, b in _MODEL_TO_BRANCH.items()
}

# Integrity check — the two dicts must be mutual inverses and cover every
# branch / model. Fails loudly at import time if a new branch or model is
# added without updating the mapping.
assert set(_MODEL_TO_BRANCH.keys()) == set(SmartPowerModel), (
    "SmartPowerModel ↔ FirmwareBranch mapping is missing a model"
)
assert set(_MODEL_TO_BRANCH.values()) == set(FirmwareBranch), (
    "SmartPowerModel ↔ FirmwareBranch mapping is missing a firmware branch"
)
