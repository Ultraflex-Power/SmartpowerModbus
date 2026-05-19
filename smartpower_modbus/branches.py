"""Firmware branches that this library knows how to address.

Canonical Python identifiers use the *platform name* (SmartPower Solo,
Gen 1.0, Gen 1.5, Gen 2.0) so user code reads in product terms rather than
in firmware-repo branch names. The underlying ``.value`` is still the exact
firmware branch name from the repo — that string is the serialized contract
and must never change.

``FirmwareBranch`` member → firmware branch name → product platform::

    SMARTPOWER_SOLO    → "SngleModule_5540_LF_MF_ExtPA_simple"        SmartPower Solo
    SMARTPOWER_GEN_1_0 → "Gen_1_5_MOD-5537-110_24_outputs_pwm_limit"  SmartPower Gen 1.0
    SMARTPOWER_GEN_1_5 → "ProductionPhase1_Fast_1_15_base"            SmartPower Gen 1.5
    SMARTPOWER_GEN_2_0 → "MegaMain"                                   SmartPower Gen 2.0

Note that the firmware branch names diverged from the product platforms over
time — most notably the firmware repo branch literally called "Gen_1_5_..."
actually carries the Gen 1.0 platform's firmware, and "ProductionPhase1_..."
carries the Gen 1.5 platform's firmware. The normalized Python identifiers
on this enum reflect the *platform*, which is what callers care about.

The previous Python identifiers (e.g. ``MEGA_MAIN``,
``SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE``) are preserved as enum aliases so
existing code keeps working.

When a new firmware branch ships:
1. Add a member here with the platform-style name and the exact branch
   string from the firmware repo as its value.
2. In ``registers.py``, append it to the ``branches=`` set of every
   register the new firmware exposes (or add new ``Register`` members for
   any genuinely new addresses).
"""

from __future__ import annotations

from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registers import Register


class FirmwareBranch(Enum):
    # ---- Canonical platform-name identifiers ----
    SMARTPOWER_SOLO    = "SngleModule_5540_LF_MF_ExtPA_simple"
    SMARTPOWER_GEN_1_0 = "Gen_1_5_MOD-5537-110_24_outputs_pwm_limit"
    SMARTPOWER_GEN_1_5 = "ProductionPhase1_Fast_1_15_base"
    SMARTPOWER_GEN_2_0 = "MegaMain"

    # ---- Legacy aliases (kept for backward compatibility) ----
    # Each one shares its value with a canonical member above, which makes
    # it an Enum alias: ``FirmwareBranch.MEGA_MAIN is FirmwareBranch.SMARTPOWER_GEN_2_0``.
    # Iteration (``for b in FirmwareBranch``) yields only the canonical
    # members, so logs and ``list-branches`` output stay clean.
    SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE        = "SngleModule_5540_LF_MF_ExtPA_simple"
    GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT   = "Gen_1_5_MOD-5537-110_24_outputs_pwm_limit"
    PRODUCTION_PHASE_1_FAST_1_15_BASE           = "ProductionPhase1_Fast_1_15_base"
    MEGA_MAIN                                   = "MegaMain"

    @classmethod
    def from_name(cls, name: str) -> "FirmwareBranch":
        """Resolve a branch by its firmware-repo name, platform identifier,
        or any legacy identifier (case-insensitive).

        Accepts, for example, ``"MegaMain"`` (firmware branch string),
        ``"SMARTPOWER_GEN_2_0"`` (platform identifier), and the legacy
        ``"MEGA_MAIN"`` identifier — all resolve to the same member.
        """
        norm = name.strip().lower()
        # Match by .value (firmware branch string) first.
        for member in cls:
            if member.value.lower() == norm:
                return member
        # Then by canonical or alias member name.
        # ``cls.__members__`` includes aliases, ``cls`` iteration does not.
        for member_name, member in cls.__members__.items():
            if member_name.lower() == norm:
                return member
        from .exceptions import UnsupportedFirmwareBranchError
        raise UnsupportedFirmwareBranchError(
            f"Unknown firmware branch: {name!r}. Known platforms: "
            + ", ".join(m.name for m in cls)
            + ". Known firmware branch strings: "
            + ", ".join(m.value for m in cls)
        )

    @cached_property
    def registers(self) -> "frozenset[Register]":
        """Set of every Register exposed by this branch (derived from registers.py)."""
        from .registers import Register
        return frozenset(r for r in Register if self in r.branches)
