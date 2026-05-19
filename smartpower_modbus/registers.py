"""SmartPower register map — single source of truth.

Mirrors ``MODBUS::AppAddress_t`` from
``MOD-537-250(CtrlBoard)/.../App/Communication/ModBus.hpp``.

Layout in the firmware enum:
- Discrete inputs at 0x0000+
- Coils at 0x1000+
- Input registers (read-only telemetry) at 0x2000+
- Holding registers (read/write settings) at 0x3000+

Branch-specific differences are encoded on each register via the ``branches``
field, so the client can reject reads/writes of a register that the selected
firmware branch does not expose.

Canonical names follow the firmware ``APP_ADDR_*_`` prefix with the type
prefix dropped (``APP_ADDR_INPUT_REG_OUT_P`` becomes ``OUT_P``). The two
firmware-side typos (``ACIVE_PROFILE``) and the one rename
(``PA_COOLANT_FLOW`` ↔ ``MCB_COOLANT_FLOW``) are flattened to a single
canonical name with the alternate spelling carried in ``legacy_names`` for
``from_name`` lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .branches import FirmwareBranch
from .exceptions import UnsupportedRegisterError

# Firmware-branch shortcuts used in the Register table below. These
# correspond, via the centralized models.py mapping, to:
#   SOLO firmware    → SmartPowerSolo
#   PRODPHASE1 fw    → SmartPowerGen_1.0
#   GEN_1_5_MOD fw   → SmartPowerGen_1.5
#   MEGAMAIN fw      → SmartPowerGen_2.0
_FB_SOLO       = FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE
_FB_PRODPHASE1 = FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE
_FB_GEN_1_5    = FirmwareBranch.GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT
_FB_MEGAMAIN   = FirmwareBranch.MEGA_MAIN

_ALL = frozenset({_FB_SOLO, _FB_PRODPHASE1, _FB_GEN_1_5, _FB_MEGAMAIN})
# Extended thermo registers exist only in the SngleModule and
# ProductionPhase1 firmware branches — i.e. on the SmartPowerSolo and
# SmartPowerGen_1.0 models.
_WITH_EXT_THERMO = frozenset({_FB_SOLO, _FB_PRODPHASE1})


class RegisterKind(Enum):
    COIL = "coil"
    DISCRETE_INPUT = "discrete_input"
    INPUT_REG = "input_reg"
    HOLDING_REG = "holding_reg"


@dataclass(frozen=True)
class RegisterMeta:
    addr: int
    kind: RegisterKind
    branches: frozenset[FirmwareBranch] = field(default=_ALL)
    signed: bool = False
    scale: float = 1.0
    unit: str = ""
    legacy_names: tuple[str, ...] = ()


class Register(Enum):
    # ---------- Discrete inputs (0x0000+) — read-only bits ----------
    INPUT_CONFIG            = RegisterMeta(0x0000, RegisterKind.DISCRETE_INPUT)
    INPUT_ENABLE            = RegisterMeta(0x0001, RegisterKind.DISCRETE_INPUT)
    INPUT_HEAT              = RegisterMeta(0x0002, RegisterKind.DISCRETE_INPUT)
    INPUT_TOP_PA_ALWAYS_ON  = RegisterMeta(0x0003, RegisterKind.DISCRETE_INPUT)
    INPUT_AIN_ENABLE        = RegisterMeta(0x0004, RegisterKind.DISCRETE_INPUT)
    INPUT_READY             = RegisterMeta(0x0005, RegisterKind.DISCRETE_INPUT)
    INPUT_FAULT             = RegisterMeta(0x0006, RegisterKind.DISCRETE_INPUT)
    INPUT_FW_UPDATE         = RegisterMeta(0x0007, RegisterKind.DISCRETE_INPUT)
    INPUT_INIT              = RegisterMeta(0x0008, RegisterKind.DISCRETE_INPUT)
    INPUT_PA_READY          = RegisterMeta(0x0009, RegisterKind.DISCRETE_INPUT)
    INPUT_RESET             = RegisterMeta(0x000A, RegisterKind.DISCRETE_INPUT)
    INPUT_SW_BOX_READY      = RegisterMeta(0x000B, RegisterKind.DISCRETE_INPUT)
    INPUT_SW_BOX_RIGHT_FB   = RegisterMeta(0x000C, RegisterKind.DISCRETE_INPUT)
    INPUT_SW_BOX_LEFT_FB    = RegisterMeta(0x000D, RegisterKind.DISCRETE_INPUT)
    INPUT_SW_BOX_RIGHT_ON   = RegisterMeta(0x000E, RegisterKind.DISCRETE_INPUT)
    INPUT_THERMO_REG_ON     = RegisterMeta(0x000F, RegisterKind.DISCRETE_INPUT)

    # ---------- Coils (0x1000+) — read/write bits ----------
    COIL_CONFIG            = RegisterMeta(0x1000, RegisterKind.COIL)
    COIL_ENABLE            = RegisterMeta(0x1001, RegisterKind.COIL)
    COIL_HEAT              = RegisterMeta(0x1002, RegisterKind.COIL)
    COIL_TOP_PA_ALWAYS_ON  = RegisterMeta(0x1003, RegisterKind.COIL)
    COIL_AIN_ENABLE        = RegisterMeta(0x1004, RegisterKind.COIL)
    COIL_AIN_4_20MA        = RegisterMeta(0x1005, RegisterKind.COIL)
    COIL_SW_BOX_ENABLE     = RegisterMeta(0x1006, RegisterKind.COIL)
    COIL_SW_BOX_AUTO       = RegisterMeta(0x1007, RegisterKind.COIL)
    COIL_SW_BOX_RIGHT_ON   = RegisterMeta(0x1008, RegisterKind.COIL)

    # ---------- Input registers (0x2000+) — read-only telemetry ----------
    # Scaling and units sourced from SDR-1MOD-537-250-00 rev A7 (USP Modbus).
    # Convention: interpreted_value = raw * scale, expressed in `unit`.
    # Temperatures are stored in Kelvin (×10) on the wire; the client
    # converts to Celsius/Kelvin/Fahrenheit per SmartPowerClient.temperature_unit.
    INPUT_REG_ERROR                  = RegisterMeta(0x2000, RegisterKind.INPUT_REG)
    INPUT_REG_RESET                  = RegisterMeta(0x2001, RegisterKind.INPUT_REG)
    INPUT_REG_AIN_ASSIGN             = RegisterMeta(0x2002, RegisterKind.INPUT_REG)
    INPUT_REG_AOUT_ASSIGN            = RegisterMeta(0x2003, RegisterKind.INPUT_REG)
    INPUT_REG_PA_ENABLE_MASK         = RegisterMeta(0x2004, RegisterKind.INPUT_REG)
    INPUT_REG_PA_MAX_WORK_SET        = RegisterMeta(0x2005, RegisterKind.INPUT_REG)
    INPUT_REG_OUT_100_P              = RegisterMeta(0x2006, RegisterKind.INPUT_REG, scale=100.0, unit="W")
    INPUT_REG_OUT_100_I              = RegisterMeta(0x2007, RegisterKind.INPUT_REG, scale=0.1,   unit="A")
    INPUT_REG_OUT_100_V              = RegisterMeta(0x2008, RegisterKind.INPUT_REG, scale=0.1,   unit="V")
    INPUT_REG_SP_I                   = RegisterMeta(0x2009, RegisterKind.INPUT_REG, scale=0.01,  unit="%")
    INPUT_REG_SP_P                   = RegisterMeta(0x200A, RegisterKind.INPUT_REG, scale=0.01,  unit="%")
    INPUT_REG_IN_COOLANT_T           = RegisterMeta(0x200B, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="K")
    INPUT_REG_OUT_COOLANT_T          = RegisterMeta(0x200C, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="K")
    INPUT_REG_CABINET_T              = RegisterMeta(0x200D, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="K")
    # Same address in every branch; MegaMain spells it MCB_COOLANT_FLOW.
    INPUT_REG_PA_COOLANT_FLOW        = RegisterMeta(
        0x200E, RegisterKind.INPUT_REG, scale=0.1, unit="lps",
        legacy_names=("INPUT_REG_MCB_COOLANT_FLOW", "MCB_COOLANT_FLOW"),
    )
    INPUT_REG_IN_V                   = RegisterMeta(0x200F, RegisterKind.INPUT_REG, scale=0.1, unit="V")
    INPUT_REG_LIMIT                  = RegisterMeta(0x2010, RegisterKind.INPUT_REG)  # bitmask
    INPUT_REG_OUT_P                  = RegisterMeta(0x2011, RegisterKind.INPUT_REG, scale=100.0, unit="W")
    INPUT_REG_OUT_I                  = RegisterMeta(0x2012, RegisterKind.INPUT_REG, scale=0.1,   unit="A")
    INPUT_REG_OUT_V                  = RegisterMeta(0x2013, RegisterKind.INPUT_REG, scale=0.1,   unit="V")
    INPUT_REG_TANK_CAP_V             = RegisterMeta(0x2014, RegisterKind.INPUT_REG, scale=0.1,   unit="V")
    INPUT_REG_FREQ                   = RegisterMeta(0x2015, RegisterKind.INPUT_REG, scale=0.01,  unit="Hz")
    INPUT_REG_DEW_POINT_T            = RegisterMeta(0x2016, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="K")
    INPUT_REG_HUMIDITY               = RegisterMeta(0x2017, RegisterKind.INPUT_REG, scale=1.0, unit="%")
    INPUT_REG_TIMER_REMAIN           = RegisterMeta(0x2018, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="s")
    INPUT_REG_SLAVE_PA_ENABLE_MASK   = RegisterMeta(0x2019, RegisterKind.INPUT_REG)  # bitmask
    INPUT_REG_CONFIG_UPDATE          = RegisterMeta(0x201A, RegisterKind.INPUT_REG)
    INPUT_REG_THERMO_REG_REGULATION  = RegisterMeta(0x201B, RegisterKind.INPUT_REG)  # 0=I, 1=P
    INPUT_REG_THERMO_REG_SP          = RegisterMeta(0x201C, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="K")
    INPUT_REG_THERMO_REG_SENSOR_T    = RegisterMeta(0x201D, RegisterKind.INPUT_REG, signed=True, scale=0.1, unit="K")
    # Firmware typo "ACIVE_PROFILE" fixed in MegaMain and ProductionPhase1.
    INPUT_REG_ACTIVE_PROFILE         = RegisterMeta(
        0x201E, RegisterKind.INPUT_REG,
        legacy_names=("INPUT_REG_ACIVE_PROFILE", "ACIVE_PROFILE"),
    )
    INPUT_REG_HS_COOLANT_FLOW        = RegisterMeta(0x201F, RegisterKind.INPUT_REG, scale=0.1, unit="lps")
    INPUT_REG_HS2_COOLANT_FLOW       = RegisterMeta(0x2020, RegisterKind.INPUT_REG, scale=0.1, unit="lps")
    INPUT_REG_THERMO_REG_LIMIT       = RegisterMeta(
        0x2021, RegisterKind.INPUT_REG, branches=_WITH_EXT_THERMO,
    )

    # ---------- Holding registers (0x3000+) — read/write settings ----------
    HOLD_REG_ERROR                  = RegisterMeta(0x3000, RegisterKind.HOLDING_REG)
    HOLD_REG_RESET                  = RegisterMeta(0x3001, RegisterKind.HOLDING_REG)
    HOLD_REG_AIN_ASSIGN             = RegisterMeta(0x3002, RegisterKind.HOLDING_REG)
    HOLD_REG_AOUT_ASSIGN            = RegisterMeta(0x3003, RegisterKind.HOLDING_REG)
    HOLD_REG_PA_ENABLE_MASK         = RegisterMeta(0x3004, RegisterKind.HOLDING_REG)
    HOLD_REG_PA_MAX_WORK_SET        = RegisterMeta(0x3005, RegisterKind.HOLDING_REG)
    HOLD_REG_SP_I                   = RegisterMeta(0x3006, RegisterKind.HOLDING_REG, scale=0.01, unit="%")
    HOLD_REG_SP_P                   = RegisterMeta(0x3007, RegisterKind.HOLDING_REG, scale=0.01, unit="%")
    # Tank-capacitor value/exponent pair: physical farads = VAL * 10^EXP
    # (spec rev A7, fixed from A6 which incorrectly stated VAL/100 * 10^EXP).
    # Combine the two registers via SmartPowerClient.read_capacitance().
    HOLD_REG_CAP_VAL                = RegisterMeta(0x3008, RegisterKind.HOLDING_REG)
    HOLD_REG_CAP_EXP                = RegisterMeta(0x3009, RegisterKind.HOLDING_REG, signed=True)
    HOLD_REG_CAP_MAX_V              = RegisterMeta(0x300A, RegisterKind.HOLDING_REG, scale=0.1, unit="V")
    HOLD_REG_HS_RATIO               = RegisterMeta(0x300B, RegisterKind.HOLDING_REG, scale=0.01, unit="")
    HOLD_REG_MAINS_NOM_V            = RegisterMeta(0x300C, RegisterKind.HOLDING_REG, scale=0.1, unit="V")
    HOLD_REG_CAP_MAX_I              = RegisterMeta(0x300D, RegisterKind.HOLDING_REG, signed=True, scale=1.0, unit="A")
    HOLD_REG_TIMER_SP               = RegisterMeta(0x300E, RegisterKind.HOLDING_REG, signed=True, scale=0.1, unit="s")
    HOLD_REG_SLAVE_PA_ENABLE_MASK   = RegisterMeta(0x300F, RegisterKind.HOLDING_REG)
    HOLD_REG_SECOND_HS_RATIO        = RegisterMeta(0x3010, RegisterKind.HOLDING_REG, scale=0.01, unit="")
    HOLD_REG_CAP_MAX_P              = RegisterMeta(0x3011, RegisterKind.HOLDING_REG, signed=True, scale=10000.0, unit="VA")
    HOLD_REG_SECOND_CAP_VAL         = RegisterMeta(0x3012, RegisterKind.HOLDING_REG)
    HOLD_REG_SECOND_CAP_EXP         = RegisterMeta(0x3013, RegisterKind.HOLDING_REG, signed=True)
    HOLD_REG_SECOND_CAP_MAX_V       = RegisterMeta(0x3014, RegisterKind.HOLDING_REG, scale=0.1, unit="V")
    HOLD_REG_SECOND_CAP_MAX_I       = RegisterMeta(0x3015, RegisterKind.HOLDING_REG, signed=True, scale=1.0, unit="A")
    HOLD_REG_SECOND_CAP_MAX_P       = RegisterMeta(0x3016, RegisterKind.HOLDING_REG, signed=True, scale=10000.0, unit="VA")
    HOLD_REG_REQ_PROFILE            = RegisterMeta(0x3017, RegisterKind.HOLDING_REG)
    HOLD_REG_THERMO_REG_EXT_SP      = RegisterMeta(
        0x3018, RegisterKind.HOLDING_REG, branches=_WITH_EXT_THERMO, signed=True, scale=0.1, unit="K",
    )
    HOLD_REG_THERMO_REG_EXT_LIMIT   = RegisterMeta(
        0x3019, RegisterKind.HOLDING_REG, branches=_WITH_EXT_THERMO,
    )

    # ---------- Convenience accessors on the enum value ----------
    @property
    def addr(self) -> int:
        return self.value.addr

    @property
    def kind(self) -> RegisterKind:
        return self.value.kind

    @property
    def branches(self) -> frozenset[FirmwareBranch]:
        return self.value.branches

    @property
    def signed(self) -> bool:
        return self.value.signed

    @property
    def scale(self) -> float:
        return self.value.scale

    @property
    def unit(self) -> str:
        return self.value.unit

    @property
    def legacy_names(self) -> tuple[str, ...]:
        return self.value.legacy_names

    @property
    def models(self) -> frozenset:
        """The set of public ``SmartPowerModel`` values that expose this register."""
        from .models import _BRANCH_TO_MODEL
        return frozenset(_BRANCH_TO_MODEL[fb] for fb in self.branches)

    @property
    def is_writable(self) -> bool:
        return self.kind in (RegisterKind.COIL, RegisterKind.HOLDING_REG)

    @property
    def is_bit(self) -> bool:
        return self.kind in (RegisterKind.COIL, RegisterKind.DISCRETE_INPUT)

    # ---------- Lookup helpers ----------
    @classmethod
    def from_name(cls, name: str) -> Register:
        """Resolve a register by its canonical name, legacy name, or the
        firmware ``APP_ADDR_*`` constant.

        Case-insensitive; accepts forms like ``OUT_P``, ``INPUT_REG_OUT_P``,
        and ``APP_ADDR_INPUT_REG_OUT_P``. Also accepts the firmware typo
        ``ACIVE_PROFILE`` and the MegaMain rename ``MCB_COOLANT_FLOW``.

        Suffix-only forms are only accepted when unambiguous. ``OUT_P`` is
        unique (one register), but ``SP_P``, ``ERROR``, ``CONFIG``, … exist
        as both input-register and holding-register members. Passing an
        ambiguous suffix raises :class:`UnsupportedRegisterError` listing
        the colliding canonical names; pick one and pass it in full.
        """
        norm = name.strip().upper().removeprefix("APP_ADDR_")
        try:
            return _NAME_INDEX[norm]
        except KeyError:
            pass
        candidates = _AMBIGUOUS_SUFFIXES.get(norm)
        if candidates is not None:
            raise UnsupportedRegisterError(
                f"Register name {name!r} is ambiguous — it matches multiple "
                f"registers: {', '.join(r.name for r in candidates)}. "
                f"Pass the full canonical name instead."
            )
        raise UnsupportedRegisterError(
            f"No register matches name {name!r}"
        )

    @classmethod
    def for_branch(cls, branch: FirmwareBranch) -> frozenset[Register]:
        """Registers exposed by a given firmware branch (internal helper)."""
        return frozenset(r for r in cls if branch in r.branches)

    @classmethod
    def for_model(cls, model) -> frozenset[Register]:
        """Registers exposed by the given ``SmartPowerModel``."""
        fb = model.firmware_branch
        return frozenset(r for r in cls if fb in r.branches)


def _build_name_index() -> tuple[
    dict[str, Register], dict[str, tuple[Register, ...]]
]:
    """Pre-build the name → Register maps used by ``Register.from_name``.

    Built once at import; keeps lookup O(1). Returns two maps:

    1. ``index`` — canonical names + explicit legacy_names + unambiguous
       suffix-only forms. Canonical/legacy collisions are programmer
       errors and raised at import time so they can't lurk in production.
    2. ``ambiguous`` — suffix-only forms shared by more than one register
       (e.g. ``SP_P`` lives on both ``INPUT_REG_SP_P`` and
       ``HOLD_REG_SP_P``). ``from_name`` rejects these with a message
       that lists the candidates so the caller can disambiguate.

    Suffixes that would shadow a canonical or legacy name are skipped —
    canonical lookups must not be displaced by the looser suffix form.
    """
    index: dict[str, Register] = {}

    # Pass 1: canonical + legacy names — strict.
    for reg in Register:
        if reg.name in index and index[reg.name] is not reg:
            raise RuntimeError(
                f"Register name collision: {reg.name!r} "
                f"already mapped to {index[reg.name].name}"
            )
        index[reg.name] = reg
        for legacy in reg.legacy_names:
            key = legacy.upper()
            existing = index.get(key)
            if existing is not None and existing is not reg:
                raise RuntimeError(
                    f"Legacy register name collision: {key!r} maps to both "
                    f"{existing.name} and {reg.name}"
                )
            index[key] = reg

    # Pass 2: collect suffix candidates per key.
    suffix_candidates: dict[str, list[Register]] = {}
    for reg in Register:
        for prefix in ("INPUT_", "COIL_", "INPUT_REG_", "HOLD_REG_"):
            if reg.name.startswith(prefix):
                suffix = reg.name[len(prefix):]
                if suffix and suffix not in index:
                    suffix_candidates.setdefault(suffix, []).append(reg)

    ambiguous: dict[str, tuple[Register, ...]] = {}
    for suffix, candidates in suffix_candidates.items():
        if len(candidates) == 1:
            index[suffix] = candidates[0]
        else:
            ambiguous[suffix] = tuple(candidates)
    return index, ambiguous


_NAME_INDEX: dict[str, Register]
_AMBIGUOUS_SUFFIXES: dict[str, tuple[Register, ...]]
_NAME_INDEX, _AMBIGUOUS_SUFFIXES = _build_name_index()


def assert_supported(reg: Register, target) -> None:
    """Raise ``UnsupportedRegisterError`` if ``reg`` is not exposed by ``target``.

    ``target`` may be a ``SmartPowerModel`` (public) or a ``FirmwareBranch``
    (internal). Public callers should pass a model.
    """
    from .models import SmartPowerModel
    if isinstance(target, SmartPowerModel):
        fb = target.firmware_branch
        target_label = target.value
    elif isinstance(target, FirmwareBranch):
        fb = target
        target_label = target.value
    else:
        raise TypeError(
            f"assert_supported target must be SmartPowerModel or FirmwareBranch, "
            f"got {type(target).__name__}"
        )
    if fb not in reg.branches:
        raise UnsupportedRegisterError(
            f"Register {reg.name} (0x{reg.addr:04X}) is not available on "
            f"model {target_label}. Available on models: "
            + ", ".join(m.value for m in reg.models)
        )


def signed16(raw: int) -> int:
    """Reinterpret a uint16 as int16."""
    raw &= 0xFFFF
    return raw - 0x10000 if raw & 0x8000 else raw


def unsigned16(value: int) -> int:
    """Reinterpret an int16 as uint16, validating range."""
    if not -0x8000 <= value <= 0xFFFF:
        from .exceptions import InvalidValueError
        raise InvalidValueError(
            f"Value {value} does not fit in a 16-bit register (allowed: -32768..65535)"
        )
    return value & 0xFFFF
