"""Smoke tests for the ``smartpower-cli`` argparse front-end.

Covers everything that does not need a real (or faked) serial port:

- The pure value-parsing helpers ``_parse_raw_value`` and
  ``_parse_interpreted_value`` — happy paths, hex form, coil truthy
  spellings, error paths (which raise ``SystemExit``).
- The pure formatters ``_format_raw`` and ``_format_interpreted`` —
  signed/unsigned hex, temperature unit suffixes, the B5 regression
  (no misleading hex twin on interpreted unitless ints).
- The ``list-models`` and ``list-registers`` subcommands end-to-end
  through ``main(argv)`` — they don't open a serial port.
- The deprecated ``--branch`` alias still resolves a model and emits
  a ``DeprecationWarning``.
"""

from __future__ import annotations

import warnings

import pytest

from smartpower_modbus import Register, SmartPowerModel, TemperatureUnit
from smartpower_modbus.cli import (
    _format_interpreted,
    _format_raw,
    _parse_interpreted_value,
    _parse_raw_value,
    _resolve_model_arg,
    main,
)

# ---------- _parse_raw_value ----------

def test_parse_raw_value_decimal_int():
    assert _parse_raw_value(Register.HOLD_REG_SP_P, "42") == 42


def test_parse_raw_value_hex_int():
    assert _parse_raw_value(Register.HOLD_REG_SP_P, "0xFF") == 0xFF
    assert _parse_raw_value(Register.HOLD_REG_SP_P, "0x1234") == 0x1234


def test_parse_raw_value_negative_int():
    assert _parse_raw_value(Register.HOLD_REG_CAP_MAX_I, "-50") == -50


def test_parse_raw_value_rejects_non_numeric():
    with pytest.raises(SystemExit, match="integer"):
        _parse_raw_value(Register.HOLD_REG_SP_P, "abc")


def test_parse_raw_value_coil_truthy_spellings():
    for s in ("1", "true", "TRUE", "True", "on", "yes"):
        assert _parse_raw_value(Register.COIL_ENABLE, s) is True
    for s in ("0", "false", "False", "off", "no"):
        assert _parse_raw_value(Register.COIL_ENABLE, s) is False


def test_parse_raw_value_coil_rejects_garbage():
    with pytest.raises(SystemExit, match="coil"):
        _parse_raw_value(Register.COIL_ENABLE, "maybe")


# ---------- _parse_interpreted_value ----------

def test_parse_interpreted_value_returns_float():
    val = _parse_interpreted_value(Register.HOLD_REG_SP_P, "50.5")
    assert val == 50.5
    assert isinstance(val, float)


def test_parse_interpreted_value_rejects_non_numeric():
    with pytest.raises(SystemExit, match="number"):
        _parse_interpreted_value(Register.HOLD_REG_SP_P, "fifty")


def test_parse_interpreted_value_coil_delegates_to_raw():
    """For coils, --interpret and raw mode parse identically."""
    assert _parse_interpreted_value(Register.COIL_ENABLE, "true") is True


# ---------- _format_raw ----------

def test_format_raw_unsigned_int_uses_hex_companion():
    assert _format_raw(0x1234, Register.HOLD_REG_SP_P) == "4660 (0x1234)"


def test_format_raw_signed_negative_masks_for_hex():
    # -1 as int16 is 0xFFFF on the wire.
    assert _format_raw(-1, Register.HOLD_REG_CAP_MAX_I) == "-1 (0xFFFF)"


def test_format_raw_bool_becomes_0_or_1():
    assert _format_raw(True, Register.COIL_ENABLE) == "1"
    assert _format_raw(False, Register.COIL_ENABLE) == "0"


# ---------- _format_interpreted (B5 regression area) ----------

def test_format_interpreted_temperature_celsius():
    out = _format_interpreted(24.5, Register.INPUT_REG_IN_COOLANT_T, TemperatureUnit.CELSIUS)
    assert out == "24.50 °C"


def test_format_interpreted_temperature_kelvin_has_no_degree_sign():
    out = _format_interpreted(297.65, Register.INPUT_REG_IN_COOLANT_T, TemperatureUnit.KELVIN)
    assert out == "297.65 K"


def test_format_interpreted_temperature_fahrenheit():
    out = _format_interpreted(76.91, Register.INPUT_REG_IN_COOLANT_T, TemperatureUnit.FAHRENHEIT)
    assert out == "76.91 °F"


def test_format_interpreted_with_unit_float():
    out = _format_interpreted(1234.5, Register.INPUT_REG_OUT_P, TemperatureUnit.CELSIUS)
    # OUT_P unit is "W"; float values use %g
    assert out.endswith(" W")
    assert "1234" in out  # 1234.5 with %g is "1234.5"


def test_format_interpreted_unitless_int_drops_hex_twin():
    """B5 regression: --interpret on a unitless register (e.g. ERROR
    bitmask) must NOT print the misleading ``(0xFFFF)`` companion that
    suggested the raw form was the source of truth."""
    out = _format_interpreted(-1, Register.HOLD_REG_CAP_EXP, TemperatureUnit.CELSIUS)
    assert out == "-1"
    assert "0x" not in out


def test_format_interpreted_unitless_positive_int():
    out = _format_interpreted(42, Register.INPUT_REG_ERROR, TemperatureUnit.CELSIUS)
    assert out == "42"


# ---------- _resolve_model_arg ----------

class _Args:
    """Stand-in for argparse.Namespace; only the two fields _resolve_model_arg reads."""
    def __init__(self, model=None, branch_deprecated=None):
        self.model = model
        self.branch_deprecated = branch_deprecated


def test_resolve_model_arg_returns_none_when_neither_given():
    assert _resolve_model_arg(_Args()) is None


def test_resolve_model_arg_accepts_model():
    assert _resolve_model_arg(_Args(model="SmartPowerGen_2.0")) is SmartPowerModel.GEN_2_0


def test_resolve_model_arg_branch_emits_deprecation():
    """The legacy --branch flag still resolves but must warn."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _resolve_model_arg(_Args(branch_deprecated="MegaMain"))
    assert result is SmartPowerModel.GEN_2_0
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_resolve_model_arg_rejects_both_model_and_branch():
    with pytest.raises(SystemExit):
        _resolve_model_arg(_Args(model="SmartPowerGen_2.0", branch_deprecated="MegaMain"))


# ---------- main() — subcommands that don't need a serial port ----------

def test_main_list_models_prints_every_model(capsys):
    rc = main(["list-models"])
    assert rc == 0
    out = capsys.readouterr().out
    for model in SmartPowerModel:
        assert model.value in out, f"{model.value} missing from list-models output"
        assert model.product_code in out


def test_main_list_registers_requires_model(capsys):
    rc = main(["list-registers"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--model is required" in err


def test_main_list_registers_for_gen_2_0(capsys):
    rc = main(["--model", "SmartPowerGen_2.0", "list-registers"])
    assert rc == 0
    out = capsys.readouterr().out
    # Spot-check a register that's in GEN_2_0 and one that isn't.
    assert "INPUT_REG_OUT_P" in out
    # THERMO_REG_LIMIT is SOLO + GEN_1_0 only.
    assert "INPUT_REG_THERMO_REG_LIMIT" not in out


def test_main_list_registers_for_solo_includes_ext_thermo(capsys):
    rc = main(["--model", "SmartPowerSolo", "list-registers"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "INPUT_REG_THERMO_REG_LIMIT" in out


def test_main_read_requires_port(capsys):
    """``read`` is a wire-touching command; --port + --slave are required."""
    rc = main(["--model", "SmartPowerGen_2.0", "read", "OUT_P"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--port" in err and "--slave" in err
