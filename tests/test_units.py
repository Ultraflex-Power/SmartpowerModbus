"""Tests for unit scaling, temperature conversion, and read_value/write_value."""

from __future__ import annotations

import pytest

from smartpower_modbus import Register, SmartPowerClient, SmartPowerModel, TemperatureUnit
from smartpower_modbus.units import kelvin_from, kelvin_to


# ---------- Pure unit math ----------

def test_kelvin_to_self_is_identity():
    assert kelvin_to(298.15, TemperatureUnit.KELVIN) == 298.15


def test_kelvin_to_celsius():
    assert kelvin_to(298.15, TemperatureUnit.CELSIUS) == pytest.approx(25.0)
    assert kelvin_to(273.15, TemperatureUnit.CELSIUS) == pytest.approx(0.0)


def test_kelvin_to_fahrenheit():
    # 25°C = 298.15 K = 77 °F
    assert kelvin_to(298.15, TemperatureUnit.FAHRENHEIT) == pytest.approx(77.0)


def test_kelvin_round_trip():
    """K → C → K and K → F → K must round-trip exactly enough for IEEE 754."""
    for k in (200.0, 273.15, 298.15, 350.0, 500.0):
        for u in TemperatureUnit:
            assert kelvin_from(kelvin_to(k, u), u) == pytest.approx(k, abs=1e-9)


def test_temperature_unit_from_name_accepts_short_and_long_forms():
    assert TemperatureUnit.from_name("C") is TemperatureUnit.CELSIUS
    assert TemperatureUnit.from_name("CELSIUS") is TemperatureUnit.CELSIUS
    assert TemperatureUnit.from_name(" k ") is TemperatureUnit.KELVIN
    assert TemperatureUnit.from_name("°F") is TemperatureUnit.FAHRENHEIT
    with pytest.raises(ValueError):
        TemperatureUnit.from_name("Rankine")


# ---------- Register metadata: scale / unit / signed sanity ----------

@pytest.mark.parametrize(
    "reg, scale, unit",
    [
        # Currents (Amps after /10).
        (Register.INPUT_REG_OUT_I,       0.1,    "A"),
        (Register.INPUT_REG_OUT_100_I,   0.1,    "A"),
        # Voltages (Volts after /10).
        (Register.INPUT_REG_OUT_V,       0.1,    "V"),
        (Register.HOLD_REG_MAINS_NOM_V,  0.1,    "V"),
        # Power (Watts, *100).
        (Register.INPUT_REG_OUT_P,       100.0,  "W"),
        (Register.INPUT_REG_OUT_100_P,   100.0,  "W"),
        # Capacitor max-power (VA, *10000).
        (Register.HOLD_REG_CAP_MAX_P,    10000.0, "VA"),
        # Frequency (Hz after /100).
        (Register.INPUT_REG_FREQ,        0.01,   "Hz"),
        # Setpoint percent (after /100).
        (Register.INPUT_REG_SP_I,        0.01,   "%"),
        (Register.HOLD_REG_SP_P,         0.01,   "%"),
        # Coolant flow.
        (Register.INPUT_REG_PA_COOLANT_FLOW, 0.1, "lps"),
        # Temperatures (Kelvin after /10).
        (Register.INPUT_REG_IN_COOLANT_T,    0.1, "K"),
        (Register.INPUT_REG_OUT_COOLANT_T,   0.1, "K"),
        (Register.INPUT_REG_CABINET_T,       0.1, "K"),
        (Register.INPUT_REG_DEW_POINT_T,     0.1, "K"),
        (Register.INPUT_REG_THERMO_REG_SP,   0.1, "K"),
        (Register.HOLD_REG_THERMO_REG_EXT_SP, 0.1, "K"),
        # Timer (seconds after /10, signed).
        (Register.INPUT_REG_TIMER_REMAIN, 0.1, "s"),
        (Register.HOLD_REG_TIMER_SP,      0.1, "s"),
    ],
)
def test_register_scale_and_unit_match_spec(reg, scale, unit):
    assert reg.scale == scale, f"{reg.name} scale"
    assert reg.unit == unit, f"{reg.name} unit"


def test_registers_with_negative_one_sentinel_are_signed():
    """The Modbus spec lists 'range: -1..32767' for these registers,
    so they must be marked signed=True so a -1 raw reads as -0.1
    (sentinel) rather than 6553.5."""
    for reg in (
        Register.INPUT_REG_TIMER_REMAIN,
        Register.HOLD_REG_TIMER_SP,
        Register.HOLD_REG_CAP_MAX_I,
        Register.HOLD_REG_CAP_MAX_P,
        Register.HOLD_REG_SECOND_CAP_MAX_I,
        Register.HOLD_REG_SECOND_CAP_MAX_P,
        Register.HOLD_REG_CAP_EXP,
        Register.HOLD_REG_SECOND_CAP_EXP,
    ):
        assert reg.signed, f"{reg.name} should be signed (sentinel value -1)"


# ---------- read_value / write_value via the fake transport ----------

from tests.test_transport import (  # noqa: E402  (intentional cross-import)
    FakeSerialClient,
    _DeviceInfoResp,
    _Resp,
)


@pytest.fixture()
def fake_client():
    return FakeSerialClient()


@pytest.fixture()
def client(fake_client):
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=0,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    yield c
    c.close()


@pytest.fixture()
def gen_1_0_client(fake_client):
    """A client configured for SmartPowerGen_1.0 — used for tests that
    touch the extended-thermo registers (THERMO_REG_EXT_*), which only
    exist on the SOLO and GEN_1_0 platforms."""
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_1_0,
        timeout=0.01, retries=0,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    yield c
    c.close()


def test_read_value_applies_scale_for_current(client, fake_client):
    """OUT_I raw=1234 → 123.4 A."""
    fake_client.script("read_input_registers", _Resp(registers=[1234]))
    value = client.read_value(Register.INPUT_REG_OUT_I)
    assert value == pytest.approx(123.4)
    assert isinstance(value, float)


def test_read_value_applies_scale_for_power(client, fake_client):
    """OUT_P raw=50 → 5000 W (scale=100.0)."""
    fake_client.script("read_input_registers", _Resp(registers=[50]))
    assert client.read_value(Register.INPUT_REG_OUT_P) == pytest.approx(5000.0)


def test_read_value_temperature_default_celsius(client, fake_client):
    """IN_COOLANT_T raw=2981 → 298.1 K → 24.95 °C."""
    fake_client.script("read_input_registers", _Resp(registers=[2981]))
    value = client.read_value(Register.INPUT_REG_IN_COOLANT_T)
    assert value == pytest.approx(24.95, abs=0.01)


def test_read_value_temperature_in_kelvin_via_override(client, fake_client):
    """Per-call override beats the client-wide default."""
    fake_client.script("read_input_registers", _Resp(registers=[2981]))
    value = client.read_value(
        Register.INPUT_REG_IN_COOLANT_T, temperature_unit=TemperatureUnit.KELVIN,
    )
    assert value == pytest.approx(298.1)


def test_read_value_temperature_in_fahrenheit_via_string_override(client, fake_client):
    fake_client.script("read_input_registers", _Resp(registers=[2981]))
    value = client.read_value(Register.INPUT_REG_IN_COOLANT_T, temperature_unit="F")
    # 24.95 °C → 76.91 °F
    assert value == pytest.approx(76.91, abs=0.01)


def test_client_default_temperature_unit_is_celsius():
    c = SmartPowerClient(port="dummy", slave_id=1, model=SmartPowerModel.GEN_2_0)
    assert c.temperature_unit is TemperatureUnit.CELSIUS


def test_client_accepts_string_temperature_unit_in_constructor():
    c = SmartPowerClient(
        port="dummy", slave_id=1, model=SmartPowerModel.GEN_2_0,
        temperature_unit="F",
    )
    assert c.temperature_unit is TemperatureUnit.FAHRENHEIT


def test_read_value_unscaled_register_returns_int(client, fake_client):
    """ERROR (0x2000) has no scale/unit — read_value should keep it int."""
    fake_client.script("read_input_registers", _Resp(registers=[0x4007]))
    value = client.read_value(Register.INPUT_REG_ERROR)
    assert value == 0x4007
    assert isinstance(value, int) and not isinstance(value, bool)


def test_read_value_for_coil_returns_bool(client, fake_client):
    fake_client.script("read_coils", _Resp(bits=[True]))
    value = client.read_value(Register.COIL_ENABLE)
    assert value is True


def test_read_value_signed_negative_sentinel_for_temperature(client, fake_client):
    """A raw value of -1 (0xFFFF as int16) is the invalid sentinel — the
    library still passes it through the scaling, so the caller sees an
    impossibly-low temperature and knows to treat it as invalid."""
    fake_client.script("read_input_registers", _Resp(registers=[0xFFFF]))
    value = client.read_value(Register.INPUT_REG_IN_COOLANT_T)
    # -1 raw * 0.1 = -0.1 K → ~-273.25 °C
    assert value < -270


# ---------- write_value (interpreted writes) ----------

def test_write_value_holding_register_amps(client, fake_client):
    """Write 50.0 A to a /10A register → raw 500."""
    client.write_value(Register.HOLD_REG_CAP_MAX_I, 50.0)
    call = fake_client.calls[-1]
    assert call.name == "write_register"
    assert call.kwargs["value"] == 50  # CAP_MAX_I has scale=1.0, unit=A — raw == amps


def test_write_value_setpoint_percent(client, fake_client):
    """SP_P scale=0.01 — write 50% → raw 5000."""
    client.write_value(Register.HOLD_REG_SP_P, 50.0)
    call = fake_client.calls[-1]
    assert call.kwargs["value"] == 5000


def test_write_value_temperature_celsius_default(gen_1_0_client, fake_client):
    """Write 26.85 °C → 300.00 K → raw 3000 (chosen to avoid the FP
    rounding ambiguity at K-values ending in .05).

    HOLD_REG_THERMO_REG_EXT_SP only exists on SOLO and GEN_1_0 — use a
    GEN_1_0 client.
    """
    gen_1_0_client.write_value(Register.HOLD_REG_THERMO_REG_EXT_SP, 26.85)
    call = fake_client.calls[-1]
    # 26.85 + 273.15 = 300.00 K → ×10 = 3000.0 → 3000
    assert call.kwargs["value"] == pytest.approx(3000, abs=1)


def test_write_value_temperature_kelvin_via_override(gen_1_0_client, fake_client):
    """Per-call override on the write path."""
    gen_1_0_client.write_value(
        Register.HOLD_REG_THERMO_REG_EXT_SP, 300.0,
        temperature_unit=TemperatureUnit.KELVIN,
    )
    call = fake_client.calls[-1]
    # 300.0 K * 10 = 3000 exactly
    assert call.kwargs["value"] == 3000


def test_write_value_temperature_fahrenheit(gen_1_0_client, fake_client):
    """80.33 °F ≈ 26.85 °C = 300.00 K → raw ≈ 3000."""
    gen_1_0_client.write_value(
        Register.HOLD_REG_THERMO_REG_EXT_SP, 80.33,
        temperature_unit="F",
    )
    call = fake_client.calls[-1]
    # Allow ±1 raw because FP can drift the K value by ≤0.05.
    assert call.kwargs["value"] == pytest.approx(3000, abs=1)


def test_write_value_rejects_out_of_range(gen_1_0_client):
    """4000 °C = 4273.15 K = raw 42731.5 → overflows int16."""
    from smartpower_modbus import InvalidValueError
    with pytest.raises(InvalidValueError):
        gen_1_0_client.write_value(Register.HOLD_REG_THERMO_REG_EXT_SP, 4000.0)


def test_write_value_round_trip_with_read_value(client, fake_client):
    """Write 50 A, transport echoes the raw it received, read_value
    decodes back to 50.0."""
    # Capture the raw value the client writes, then return it on read.
    client.write_value(Register.HOLD_REG_CAP_MAX_I, 50.0)
    written_raw = fake_client.calls[-1].kwargs["value"]
    fake_client.script("read_holding_registers", _Resp(registers=[written_raw]))
    assert client.read_value(Register.HOLD_REG_CAP_MAX_I) == pytest.approx(50.0)


def test_write_value_coil_passes_through(client, fake_client):
    """Coils have no scaling — write_value delegates to write()."""
    client.write_value(Register.COIL_ENABLE, True)
    call = fake_client.calls[-1]
    assert call.name == "write_coil"
    assert call.kwargs["value"] is True


def test_read_capacitance_combines_val_and_exp(client, fake_client):
    """CAP_VAL=1234, CAP_EXP=-9 → 12.34 nF = 1.234e-8 F.

    Formula: VAL/100 * 10^EXP.
    """
    # First read CAP_VAL (holding), then CAP_EXP. Both are holding registers
    # so they share the read_holding_registers script queue.
    fake_client.script(
        "read_holding_registers",
        _Resp(registers=[1234]),   # CAP_VAL
        _Resp(registers=[0xFFF7]), # CAP_EXP = -9 as int16
    )
    cap = client.read_capacitance()
    # 1234 / 100 * 10^-9 = 12.34e-9 = 1.234e-8 F
    assert cap == pytest.approx(1.234e-8, rel=1e-6)
