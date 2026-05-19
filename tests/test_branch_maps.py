"""Branch-map and register-data sanity checks.

Pinned against ModBus.hpp / AppAddress_t as of the four supported branches:

- SngleModule_5540_LF_MF_ExtPA_simple
- MegaMain
- ProductionPhase1_Fast_1_15_base
- Gen_1_5_MOD-5537-110_24_outputs_pwm_limit
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    FirmwareBranch,
    Register,
    RegisterKind,
    UnsupportedFirmwareBranchError,
    UnsupportedRegisterError,
)
from smartpower_modbus.registers import (
    assert_supported,
    signed16,
    unsigned16,
)


SOLO    = FirmwareBranch.SMARTPOWER_SOLO
GEN_1_0 = FirmwareBranch.SMARTPOWER_GEN_1_0
GEN_1_5 = FirmwareBranch.SMARTPOWER_GEN_1_5
GEN_2_0 = FirmwareBranch.SMARTPOWER_GEN_2_0


def test_inputs_all_branches_have_identical_set():
    inputs_by_branch = {b: {r for r in b.registers if r.kind is RegisterKind.DISCRETE_INPUT} for b in FirmwareBranch}
    expected = inputs_by_branch[SOLO]
    for b in (GEN_1_0, GEN_1_5, GEN_2_0):
        assert inputs_by_branch[b] == expected, f"{b.name} differs from {SOLO.name} on discrete inputs"
    addrs = sorted(r.addr for r in expected)
    assert addrs == list(range(0x0000, 0x0010)), "16 contiguous discrete inputs at 0x0000-0x000F"


def test_coils_all_branches_have_identical_set():
    coils_by_branch = {b: {r for r in b.registers if r.kind is RegisterKind.COIL} for b in FirmwareBranch}
    expected = coils_by_branch[SOLO]
    for b in (GEN_1_0, GEN_1_5, GEN_2_0):
        assert coils_by_branch[b] == expected
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


def test_extended_thermo_only_on_solo_and_gen_1_5():
    ext_regs = {
        Register.INPUT_REG_THERMO_REG_LIMIT,
        Register.HOLD_REG_THERMO_REG_EXT_SP,
        Register.HOLD_REG_THERMO_REG_EXT_LIMIT,
    }
    for reg in ext_regs:
        assert reg.branches == frozenset({SOLO, GEN_1_5}), (
            f"{reg.name} should be available only on SOLO and GEN_1_5"
        )
        assert reg not in GEN_1_0.registers
        assert reg not in GEN_2_0.registers


def test_pa_coolant_flow_has_mcb_alias():
    reg = Register.INPUT_REG_PA_COOLANT_FLOW
    assert reg.branches == frozenset({SOLO, GEN_1_0, GEN_1_5, GEN_2_0})
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


def test_branch_from_name_round_trip():
    """from_name accepts firmware-repo strings, platform identifiers,
    and legacy identifiers."""
    for b in FirmwareBranch:
        # By firmware branch string (.value):
        assert FirmwareBranch.from_name(b.value) is b
        # By new platform identifier:
        assert FirmwareBranch.from_name(b.name) is b
    # By legacy identifiers (these are aliases on the same members):
    assert FirmwareBranch.from_name("MEGA_MAIN") is FirmwareBranch.SMARTPOWER_GEN_2_0
    assert FirmwareBranch.from_name("SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE") is FirmwareBranch.SMARTPOWER_SOLO
    assert FirmwareBranch.from_name("PRODUCTION_PHASE_1_FAST_1_15_BASE") is FirmwareBranch.SMARTPOWER_GEN_1_5
    assert FirmwareBranch.from_name("GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT") is FirmwareBranch.SMARTPOWER_GEN_1_0
    # Case insensitivity:
    assert FirmwareBranch.from_name("smartpower_gen_2_0") is FirmwareBranch.SMARTPOWER_GEN_2_0
    assert FirmwareBranch.from_name("megamain") is FirmwareBranch.SMARTPOWER_GEN_2_0
    with pytest.raises(UnsupportedFirmwareBranchError):
        FirmwareBranch.from_name("Not_A_Real_Branch")


def test_legacy_branch_identifiers_are_aliases():
    """Old Python identifiers must keep resolving for backward compatibility."""
    assert FirmwareBranch.MEGA_MAIN is FirmwareBranch.SMARTPOWER_GEN_2_0
    assert FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE is FirmwareBranch.SMARTPOWER_SOLO
    assert FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE is FirmwareBranch.SMARTPOWER_GEN_1_5
    assert FirmwareBranch.GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT is FirmwareBranch.SMARTPOWER_GEN_1_0


def test_iteration_yields_only_canonical_members():
    """Aliases must not show up in iteration — only the four canonical
    platform identifiers."""
    names = [b.name for b in FirmwareBranch]
    assert names == [
        "SMARTPOWER_SOLO",
        "SMARTPOWER_GEN_1_0",
        "SMARTPOWER_GEN_1_5",
        "SMARTPOWER_GEN_2_0",
    ]


def test_firmware_branch_value_strings_are_stable():
    """The .value strings are the serialized contract — they must match
    the firmware branch names in the repo exactly."""
    assert FirmwareBranch.SMARTPOWER_SOLO.value    == "SngleModule_5540_LF_MF_ExtPA_simple"
    assert FirmwareBranch.SMARTPOWER_GEN_1_0.value == "Gen_1_5_MOD-5537-110_24_outputs_pwm_limit"
    assert FirmwareBranch.SMARTPOWER_GEN_1_5.value == "ProductionPhase1_Fast_1_15_base"
    assert FirmwareBranch.SMARTPOWER_GEN_2_0.value == "MegaMain"


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


def test_assert_supported_raises_for_unsupported_branch():
    with pytest.raises(UnsupportedRegisterError):
        assert_supported(Register.INPUT_REG_THERMO_REG_LIMIT, GEN_2_0)
    # Same register on a supported branch is fine.
    assert_supported(Register.INPUT_REG_THERMO_REG_LIMIT, SOLO)


def test_signed16_round_trip():
    assert signed16(0x0000) == 0
    assert signed16(0x7FFF) == 32767
    assert signed16(0x8000) == -32768
    assert signed16(0xFFFF) == -1
    assert unsigned16(-1) == 0xFFFF
    assert unsigned16(-32768) == 0x8000
    assert unsigned16(32767) == 0x7FFF
    assert unsigned16(0xFFFF) == 0xFFFF
    import pytest as _pt
    with _pt.raises(Exception):
        unsigned16(70000)
    with _pt.raises(Exception):
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
