"""Register-data sanity checks pinned against the firmware ModBus.hpp enums.

The four supported firmware branches:

- SngleModule_5540_LF_MF_ExtPA_simple  (SmartPowerSolo)
- ProductionPhase1_Fast_1_15_base      (SmartPowerGen_1.0)
- Gen_1_5_MOD-5537-110_24_outputs_pwm_limit  (SmartPowerGen_1.5)
- MegaMain                              (SmartPowerGen_2.0)
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    InvalidValueError,
    Register,
    RegisterKind,
    SmartPowerModel,
    UnsupportedRegisterError,
)
from smartpower_modbus.branches import FirmwareBranch
from smartpower_modbus.registers import (
    assert_supported,
    signed16,
    unsigned16,
)


SOLO    = SmartPowerModel.SOLO
GEN_1_0 = SmartPowerModel.GEN_1_0
GEN_1_5 = SmartPowerModel.GEN_1_5
GEN_2_0 = SmartPowerModel.GEN_2_0


def test_inputs_all_models_have_identical_set():
    inputs_by_model = {m: {r for r in m.registers if r.kind is RegisterKind.DISCRETE_INPUT} for m in SmartPowerModel}
    expected = inputs_by_model[SOLO]
    for m in (GEN_1_0, GEN_1_5, GEN_2_0):
        assert inputs_by_model[m] == expected, f"{m.value} differs from {SOLO.value} on discrete inputs"
    addrs = sorted(r.addr for r in expected)
    assert addrs == list(range(0x0000, 0x0010)), "16 contiguous discrete inputs at 0x0000-0x000F"


def test_coils_all_models_have_identical_set():
    coils_by_model = {m: {r for r in m.registers if r.kind is RegisterKind.COIL} for m in SmartPowerModel}
    expected = coils_by_model[SOLO]
    for m in (GEN_1_0, GEN_1_5, GEN_2_0):
        assert coils_by_model[m] == expected
    addrs = sorted(r.addr for r in expected)
    assert addrs == list(range(0x1000, 0x1009)), "9 contiguous coils at 0x1000-0x1008"


@pytest.mark.parametrize(
    "reg, expected_addr",
    [
        (Register.INPUT_CONFIG, 0x0000),
        (Register.INPUT_THERMO_REG_ON, 0x000F),
        (Register.COIL_CONFIG, 0x1000),
        (Register.COIL_SW_BOX_RIGHT_ON, 0x1008),
        (Register.INPUT_REG_ERROR, 0x2000),
        (Register.INPUT_REG_PA_COOLANT_FLOW, 0x200E),
        (Register.INPUT_REG_ACTIVE_PROFILE, 0x201E),
        (Register.INPUT_REG_HS2_COOLANT_FLOW, 0x2020),
        (Register.INPUT_REG_THERMO_REG_LIMIT, 0x2021),
        (Register.HOLD_REG_ERROR, 0x3000),
        (Register.HOLD_REG_SP_P, 0x3007),
        (Register.HOLD_REG_REQ_PROFILE, 0x3017),
        (Register.HOLD_REG_THERMO_REG_EXT_SP, 0x3018),
        (Register.HOLD_REG_THERMO_REG_EXT_LIMIT, 0x3019),
    ],
)
def test_register_address_matches_firmware(reg, expected_addr):
    assert reg.addr == expected_addr


def test_extended_thermo_only_on_solo_and_gen_1_0():
    ext_regs = {
        Register.INPUT_REG_THERMO_REG_LIMIT,
        Register.HOLD_REG_THERMO_REG_EXT_SP,
        Register.HOLD_REG_THERMO_REG_EXT_LIMIT,
    }
    for reg in ext_regs:
        # Verified via the public model surface.
        assert reg.models == frozenset({SOLO, GEN_1_0}), (
            f"{reg.name} should be available only on SOLO and GEN_1_0"
        )
        # And via the internal firmware-branch field.
        assert reg.branches == frozenset({
            FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE,
            FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE,
        })
        assert reg not in GEN_1_5.registers
        assert reg not in GEN_2_0.registers


def test_pa_coolant_flow_has_mcb_alias():
    reg = Register.INPUT_REG_PA_COOLANT_FLOW
    assert reg.models == frozenset({SOLO, GEN_1_0, GEN_1_5, GEN_2_0})
    assert "MCB_COOLANT_FLOW" in reg.legacy_names or "INPUT_REG_MCB_COOLANT_FLOW" in reg.legacy_names
    assert Register.from_name("MCB_COOLANT_FLOW") is reg
    assert Register.from_name("INPUT_REG_MCB_COOLANT_FLOW") is reg


def test_active_profile_typo_alias():
    reg = Register.INPUT_REG_ACTIVE_PROFILE
    assert reg.addr == 0x201E
    assert Register.from_name("ACIVE_PROFILE") is reg
    assert Register.from_name("INPUT_REG_ACIVE_PROFILE") is reg
    assert Register.from_name("ACTIVE_PROFILE") is reg


def test_from_name_accepts_canonical_and_legacy_and_app_addr():
    assert Register.from_name("OUT_P") is Register.INPUT_REG_OUT_P
    assert Register.from_name("INPUT_REG_OUT_P") is Register.INPUT_REG_OUT_P
    assert Register.from_name("APP_ADDR_INPUT_REG_OUT_P") is Register.INPUT_REG_OUT_P
    assert Register.from_name("hold_reg_sp_p") is Register.HOLD_REG_SP_P


def test_from_name_rejects_unknown():
    with pytest.raises(UnsupportedRegisterError):
        Register.from_name("BOGUS_NAME")


def test_signed_flag_set_on_temperatures():
    for reg in (
        Register.INPUT_REG_IN_COOLANT_T,
        Register.INPUT_REG_OUT_COOLANT_T,
        Register.INPUT_REG_CABINET_T,
        Register.INPUT_REG_DEW_POINT_T,
        Register.INPUT_REG_THERMO_REG_SP,
        Register.INPUT_REG_THERMO_REG_SENSOR_T,
        Register.HOLD_REG_THERMO_REG_EXT_SP,
    ):
        assert reg.signed, f"{reg.name} should be marked signed (temperature-like)"


def test_assert_supported_accepts_model_and_branch():
    # Model path (public API).
    with pytest.raises(UnsupportedRegisterError):
        assert_supported(Register.INPUT_REG_THERMO_REG_LIMIT, GEN_2_0)
    assert_supported(Register.INPUT_REG_THERMO_REG_LIMIT, SOLO)

    # Firmware-branch path (internal compatibility).
    with pytest.raises(UnsupportedRegisterError):
        assert_supported(
            Register.INPUT_REG_THERMO_REG_LIMIT, FirmwareBranch.MEGA_MAIN,
        )
    assert_supported(
        Register.INPUT_REG_THERMO_REG_LIMIT,
        FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE,
    )


def test_signed16_round_trip():
    assert signed16(0x0000) == 0
    assert signed16(0x7FFF) == 32767
    assert signed16(0x8000) == -32768
    assert signed16(0xFFFF) == -1
    assert unsigned16(-1) == 0xFFFF
    assert unsigned16(-32768) == 0x8000
    assert unsigned16(32767) == 0x7FFF
    assert unsigned16(0xFFFF) == 0xFFFF
    with pytest.raises(InvalidValueError):
        unsigned16(70000)
    with pytest.raises(InvalidValueError):
        unsigned16(-50000)


def test_branch_registers_membership_set_is_consistent():
    """Every register's ``branches`` field agrees with the derived
    ``FirmwareBranch.registers`` set."""
    for branch in FirmwareBranch:
        for reg in Register:
            in_branch_set = reg in branch.registers
            in_reg_branches = branch in reg.branches
            assert in_branch_set == in_reg_branches, (
                f"Inconsistency: {reg.name} branch={branch.value} "
                f"set-membership={in_branch_set} field-membership={in_reg_branches}"
            )


def test_register_address_kinds_match_layout():
    """Address ranges agree with the firmware's enum layout."""
    for reg in Register:
        if reg.kind is RegisterKind.DISCRETE_INPUT:
            assert 0x0000 <= reg.addr <= 0x0FFF
        elif reg.kind is RegisterKind.COIL:
            assert 0x1000 <= reg.addr <= 0x1FFF
        elif reg.kind is RegisterKind.INPUT_REG:
            assert 0x2000 <= reg.addr <= 0x2FFF
        elif reg.kind is RegisterKind.HOLDING_REG:
            assert 0x3000 <= reg.addr <= 0x3FFF


def test_unique_address_per_kind():
    """No two registers of the same kind share an address."""
    seen: dict[tuple, str] = {}
    for reg in Register:
        key = (reg.kind, reg.addr)
        assert key not in seen, f"{reg.name} clashes with {seen[key]} at {key}"
        seen[key] = reg.name
