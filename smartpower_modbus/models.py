"""Public-facing SmartPower model identifiers and the centralized
public-model ↔ firmware-branch / product-code mapping.

This is the only place that translates between what the customer-facing
world calls things ("SmartPowerGen_2.0"), what the firmware repo
internally calls things ("MegaMain"), and what the device itself reports
over Modbus FC 0x2B/0x0E (PRODUCT_CODE, e.g. "55370112"). Adding a new
SmartPower model means adding one enum member plus one entry to each of
the three mapping tables — nothing else in the public API changes.

Mapping::

    SmartPowerModel.SOLO    ↔ "SmartPowerSolo"     ↔ SngleModule_5540_LF_MF_ExtPA_simple        ↔ "55400400"
    SmartPowerModel.GEN_1_0 ↔ "SmartPowerGen_1.0"  ↔ ProductionPhase1_Fast_1_15_base            ↔ "55370250"
    SmartPowerModel.GEN_1_5 ↔ "SmartPowerGen_1.5"  ↔ Gen_1_5_MOD-5537-110_24_outputs_pwm_limit  ↔ "55370111"
    SmartPowerModel.GEN_2_0 ↔ "SmartPowerGen_2.0"  ↔ MegaMain                                   ↔ "55370112"

The firmware branch strings and the raw PRODUCT_CODE strings are internal
implementation details — they may move or be renamed in the firmware
repo. The public model names are the stable contract.
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

    @property
    def product_code(self) -> str:
        """Normalized device-reported PRODUCT_CODE string for this model.

        This is the value the firmware returns over Modbus FC 0x2B/0x0E
        (Read Device Identification, object ID 1), with any leading
        ``"0x"`` prefix stripped and case normalised.
        """
        return _MODEL_TO_PRODUCT_CODE[self]

    @classmethod
    def from_product_code(cls, code: str) -> "SmartPowerModel":
        """Resolve a model from the raw PRODUCT_CODE string the device
        reports over Modbus FC 0x2B/0x0E.

        Accepts the firmware-side spelling exactly (e.g. ``"55370112"``
        or the literal ``"0x55370250"`` that ProductionPhase1 firmware
        emits); leading ``"0x"`` is stripped, surrounding whitespace is
        trimmed, and the comparison is case-insensitive.
        """
        try:
            return _PRODUCT_CODE_TO_MODEL[_normalize_product_code(code)]
        except KeyError:
            raise UnsupportedFirmwareBranchError(
                f"Unknown SmartPower product code: {code!r}. "
                f"Known codes: " + ", ".join(_PRODUCT_CODE_TO_MODEL.keys())
            ) from None

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

# Device-reported PRODUCT_CODE strings — sourced from PRODUCT_CODE in
# App/app_cnfg.h on each firmware branch. Stored normalised (no "0x"
# prefix, uppercase) so any inbound variant can be matched after a
# single normalisation step.
_MODEL_TO_PRODUCT_CODE: dict[SmartPowerModel, str] = {
    SmartPowerModel.SOLO:    "55400400",
    SmartPowerModel.GEN_1_0: "55370250",
    SmartPowerModel.GEN_1_5: "55370111",
    SmartPowerModel.GEN_2_0: "55370112",
}

_PRODUCT_CODE_TO_MODEL: dict[str, SmartPowerModel] = {
    code: m for m, code in _MODEL_TO_PRODUCT_CODE.items()
}


def _normalize_product_code(code: str) -> str:
    """Strip whitespace and any ``0x`` prefix and uppercase the result."""
    s = code.strip()
    if s[:2].lower() == "0x":
        s = s[2:]
    return s.upper()


# Integrity checks — fail loudly at import time if a new model is added
# without updating any of the three mapping tables.
assert set(_MODEL_TO_BRANCH.keys()) == set(SmartPowerModel), (
    "SmartPowerModel ↔ FirmwareBranch mapping is missing a model"
)
assert set(_MODEL_TO_BRANCH.values()) == set(FirmwareBranch), (
    "SmartPowerModel ↔ FirmwareBranch mapping is missing a firmware branch"
)
assert set(_MODEL_TO_PRODUCT_CODE.keys()) == set(SmartPowerModel), (
    "SmartPowerModel ↔ product_code mapping is missing a model"
)
assert len(_PRODUCT_CODE_TO_MODEL) == len(_MODEL_TO_PRODUCT_CODE), (
    "Two SmartPowerModel members share a product code — codes must be unique"
)
