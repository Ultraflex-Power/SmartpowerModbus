"""Tests for the public ``SmartPowerModel`` API and the centralised
public-model ↔ firmware-branch mapping.
"""

from __future__ import annotations

import warnings

import pytest

from smartpower_modbus import SmartPowerModel, UnsupportedFirmwareBranchError
from smartpower_modbus.branches import FirmwareBranch
from smartpower_modbus.models import (
    _BRANCH_TO_MODEL,
    _MODEL_TO_BRANCH,
    _MODEL_TO_PRODUCT_CODE,
    _PRODUCT_CODE_TO_MODEL,
    _normalize_product_code,
)


def test_public_model_values_are_stable_strings():
    """These are the canonical public identifiers — they must not change."""
    assert SmartPowerModel.SOLO.value    == "SmartPowerSolo"
    assert SmartPowerModel.GEN_1_0.value == "SmartPowerGen_1.0"
    assert SmartPowerModel.GEN_1_5.value == "SmartPowerGen_1.5"
    assert SmartPowerModel.GEN_2_0.value == "SmartPowerGen_2.0"


def test_model_to_branch_mapping_matches_spec():
    """The mapping is the spec; encode it explicitly so any silent edit
    to ``_MODEL_TO_BRANCH`` fails the test."""
    assert SmartPowerModel.SOLO.firmware_branch    is FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE
    assert SmartPowerModel.GEN_1_0.firmware_branch is FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE
    assert SmartPowerModel.GEN_1_5.firmware_branch is FirmwareBranch.GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT
    assert SmartPowerModel.GEN_2_0.firmware_branch is FirmwareBranch.MEGA_MAIN


def test_mapping_is_bijective():
    """Every model has exactly one firmware branch and vice versa."""
    assert len(_MODEL_TO_BRANCH) == len(SmartPowerModel)
    assert len(_BRANCH_TO_MODEL) == len(FirmwareBranch)
    # Round-trip each direction.
    for m in SmartPowerModel:
        assert _BRANCH_TO_MODEL[_MODEL_TO_BRANCH[m]] is m
    for b in FirmwareBranch:
        assert _MODEL_TO_BRANCH[_BRANCH_TO_MODEL[b]] is b


def test_from_name_accepts_public_value():
    for m in SmartPowerModel:
        assert SmartPowerModel.from_name(m.value) is m
    # Case insensitive.
    assert SmartPowerModel.from_name("smartpowergen_2.0") is SmartPowerModel.GEN_2_0
    assert SmartPowerModel.from_name("  SmartPowerSolo  ") is SmartPowerModel.SOLO


def test_from_name_accepts_python_member_name():
    assert SmartPowerModel.from_name("GEN_2_0")  is SmartPowerModel.GEN_2_0
    assert SmartPowerModel.from_name("gen_1_0")  is SmartPowerModel.GEN_1_0
    assert SmartPowerModel.from_name("SOLO")     is SmartPowerModel.SOLO
    assert SmartPowerModel.from_name("SmartPowerModel.GEN_1_5") is SmartPowerModel.GEN_1_5


def test_from_name_accepts_firmware_branch_with_deprecation_warning():
    """Firmware branch strings still resolve (internal/legacy support) but
    must emit a ``DeprecationWarning`` — the public API contract is the
    SmartPowerModel value, not the firmware branch."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = SmartPowerModel.from_name("MegaMain")
        assert result is SmartPowerModel.GEN_2_0
        assert any(issubclass(w.category, DeprecationWarning) for w in caught), (
            "Firmware branch string lookup must emit DeprecationWarning"
        )

    # The other three firmware branches likewise resolve (each to its own model).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert SmartPowerModel.from_name("SngleModule_5540_LF_MF_ExtPA_simple") is SmartPowerModel.SOLO
        assert SmartPowerModel.from_name("ProductionPhase1_Fast_1_15_base") is SmartPowerModel.GEN_1_0
        assert SmartPowerModel.from_name("Gen_1_5_MOD-5537-110_24_outputs_pwm_limit") is SmartPowerModel.GEN_1_5


def test_from_name_accepts_legacy_python_branch_identifiers_with_deprecation():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert SmartPowerModel.from_name("MEGA_MAIN") is SmartPowerModel.GEN_2_0
        assert SmartPowerModel.from_name("PRODUCTION_PHASE_1_FAST_1_15_BASE") is SmartPowerModel.GEN_1_0
        assert SmartPowerModel.from_name("GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT") is SmartPowerModel.GEN_1_5
        assert SmartPowerModel.from_name("SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE") is SmartPowerModel.SOLO


def test_from_name_rejects_unknown():
    with pytest.raises(UnsupportedFirmwareBranchError):
        SmartPowerModel.from_name("NotARealModel")


def test_model_registers_match_firmware_branch_registers():
    """``SmartPowerModel.registers`` is a passthrough to the underlying
    firmware branch's register set."""
    for m in SmartPowerModel:
        assert m.registers == m.firmware_branch.registers


def test_iteration_yields_four_models_in_order():
    """The iteration order is stable (used in ``list-models``)."""
    assert [m.name for m in SmartPowerModel] == ["SOLO", "GEN_1_0", "GEN_1_5", "GEN_2_0"]


def test_register_models_property_is_consistent_with_branches():
    """For every register, ``models`` must equal the model set derived
    from its ``branches`` set via the central mapping."""
    from smartpower_modbus import Register
    for reg in Register:
        derived = frozenset(_BRANCH_TO_MODEL[b] for b in reg.branches)
        assert reg.models == derived


# ---------- Product-code mapping ----------

def test_product_codes_match_firmware_app_cnfg_h():
    """These are sourced from PRODUCT_CODE in App/app_cnfg.h on each
    firmware branch. Pinned here so a silent change to either side is
    caught immediately."""
    assert SmartPowerModel.SOLO.product_code    == "55400400"
    assert SmartPowerModel.GEN_1_0.product_code == "55370250"
    assert SmartPowerModel.GEN_1_5.product_code == "55370111"
    assert SmartPowerModel.GEN_2_0.product_code == "55370112"


def test_product_code_to_model_inverse_is_bijective():
    """Every model has exactly one product code and vice versa."""
    assert len(_MODEL_TO_PRODUCT_CODE) == len(SmartPowerModel)
    assert len(_PRODUCT_CODE_TO_MODEL) == len(_MODEL_TO_PRODUCT_CODE)
    for m in SmartPowerModel:
        assert _PRODUCT_CODE_TO_MODEL[_MODEL_TO_PRODUCT_CODE[m]] is m


def test_normalize_product_code_strips_0x_and_whitespace_and_casing():
    assert _normalize_product_code("55370112") == "55370112"
    assert _normalize_product_code("0x55370250") == "55370250"
    assert _normalize_product_code("0X55370250") == "55370250"
    assert _normalize_product_code("  55370111\n") == "55370111"
    # ASCII hex is case-insensitive — uppercase any letters.
    assert _normalize_product_code("0xabcd") == "ABCD"


def test_from_product_code_accepts_raw_firmware_strings():
    """The library must accept the exact strings the firmware emits,
    including the literal ``0x`` prefix used by the ProductionPhase1
    branch."""
    assert SmartPowerModel.from_product_code("55400400")    is SmartPowerModel.SOLO
    assert SmartPowerModel.from_product_code("0x55370250")  is SmartPowerModel.GEN_1_0
    assert SmartPowerModel.from_product_code("55370250")    is SmartPowerModel.GEN_1_0
    assert SmartPowerModel.from_product_code("55370111")    is SmartPowerModel.GEN_1_5
    assert SmartPowerModel.from_product_code("55370112")    is SmartPowerModel.GEN_2_0
    # Whitespace and casing tolerated.
    assert SmartPowerModel.from_product_code(" 55370112 ")  is SmartPowerModel.GEN_2_0


def test_from_product_code_rejects_unknown():
    with pytest.raises(UnsupportedFirmwareBranchError):
        SmartPowerModel.from_product_code("DEADBEEF")
    # The error message must include the unknown code so the user can
    # report it back.
    with pytest.raises(UnsupportedFirmwareBranchError, match="42424242"):
        SmartPowerModel.from_product_code("42424242")
