"""Regression tests for client-level bugs surfaced in the May-2026 review.

Covers:

- B1 — ``read_capacitance`` rejects an out-of-range exponent rather than
  silently overflowing ``10.0 ** exp`` to ``inf``.
- B2 — ``read_capacitance`` reads both registers in a single Modbus
  transaction (no torn value/exponent pair under concurrent writes).
- B3 — ``write(coil, n)`` rejects ints outside ``{0, 1}`` instead of
  silently coercing them to ``True``.
- B4 — ``identify_model`` does not clobber an explicitly-configured
  ``self.model`` when the device reports a different one.
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    InvalidValueError,
    Register,
    SmartPowerClient,
    SmartPowerModel,
)

# Re-use the existing fake transport from test_transport.py. With pytest's
# default rootdir + tests/__init__.py present, the package form works; the
# alternative ``from test_transport import ...`` is the fallback if pytest is
# run with ``--import-mode=importlib``.
try:
    from tests.test_transport import FakeSerialClient, _DeviceInfoResp, _Resp
except ImportError:  # pragma: no cover — fallback for non-package import modes
    from test_transport import FakeSerialClient, _DeviceInfoResp, _Resp


def _signed16_to_raw(value: int) -> int:
    """Inverse of ``registers.signed16``, used to build fake register payloads
    for negative exponents."""
    return value & 0xFFFF


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


# ---------- B1 + B2: read_capacitance ----------

def test_read_capacitance_normal_pair(client, fake_client):
    """100 × 10^-6 = 1e-4 F (a typical 100 µF tank cap)."""
    # exp_raw=-6 round-trips through int16 as 0xFFFA.
    fake_client.script("read_holding_registers", _Resp(registers=[100, 0xFFFA]))
    cap = client.read_capacitance()
    assert cap == pytest.approx(1e-4)


def test_read_capacitance_issues_single_transport_call(client, fake_client):
    """B2: read_capacitance must batch VAL+EXP into one Modbus transaction
    so a concurrent writer cannot tear the pair."""
    fake_client.calls.clear()
    fake_client.script("read_holding_registers", _Resp(registers=[200, 0xFFFB]))
    client.read_capacitance()
    holding_calls = [c for c in fake_client.calls if c.name == "read_holding_registers"]
    assert len(holding_calls) == 1, (
        f"expected 1 read_holding_registers call, got {len(holding_calls)}: "
        f"{holding_calls}"
    )
    call = holding_calls[0]
    assert call.address == Register.HOLD_REG_CAP_VAL.addr  # 0x3008
    assert call.kwargs["count"] == 2


def test_read_capacitance_rejects_overflow_exponent(client, fake_client):
    """B1: a firmware value of e.g. exp=400 silently overflows
    ``10.0 ** exp`` to ``inf``. Must raise instead."""
    # raw uint16 = 400 → signed16(400) = 400, well outside [-30, 6].
    fake_client.script("read_holding_registers", _Resp(registers=[1, 400]))
    with pytest.raises(InvalidValueError, match="exponent"):
        client.read_capacitance()


def test_read_capacitance_rejects_underflow_exponent(client, fake_client):
    """Symmetric: a hugely negative exponent silently underflows to 0.0."""
    # 0x8001 as signed16 = -32767 → out of range.
    fake_client.script("read_holding_registers", _Resp(registers=[1, 0x8001]))
    with pytest.raises(InvalidValueError, match="exponent"):
        client.read_capacitance()


def test_read_capacitance_accepts_boundary_exponents(client, fake_client):
    """The boundaries of the plausibility range must remain valid — pico-
    farad (-12) and a small positive exponent both pass."""
    fake_client.script(
        "read_holding_registers",
        _Resp(registers=[5, _signed16_to_raw(-30)]),
        _Resp(registers=[5, _signed16_to_raw(6)]),
    )
    # exp=-30 (way below pico) and exp=6 (way above farad-class) — both
    # land on the boundary and must not raise.
    client.read_capacitance()
    client.read_capacitance()


# ---------- B3: coil-write strictness ----------

def test_coil_write_rejects_non_binary_int(client, fake_client):
    """B3: ``write(coil, 42)`` used to silently coerce to True. It must
    now raise, like the holding-register path already does for bools."""
    fake_client.calls.clear()
    with pytest.raises(InvalidValueError, match="Coil"):
        client.write(Register.COIL_ENABLE, 42)
    # And nothing should have hit the wire.
    write_calls = [c for c in fake_client.calls if c.name == "write_coil"]
    assert write_calls == []


def test_coil_write_rejects_negative_int(client):
    """Same path for negatives — -1 isn't ``0`` or ``1``."""
    with pytest.raises(InvalidValueError, match="Coil"):
        client.write(Register.COIL_ENABLE, -1)


def test_coil_write_rejects_non_int_non_bool(client):
    """Strings, floats, None — none of these should silently coerce."""
    for bad in ("on", 1.0, None):
        with pytest.raises(InvalidValueError, match="Coil"):
            client.write(Register.COIL_ENABLE, bad)


def test_coil_write_still_accepts_bool(client, fake_client):
    """Bool is the canonical input — must keep working."""
    client.write(Register.COIL_ENABLE, True)
    assert fake_client.calls[-1].kwargs["value"] is True
    client.write(Register.COIL_ENABLE, False)
    assert fake_client.calls[-1].kwargs["value"] is False


def test_coil_write_still_accepts_int_0_and_1(client, fake_client):
    """Legacy ergonomics: int 0/1 keeps working (test_transport.py:288-292
    already pinned this behaviour)."""
    client.write(Register.COIL_ENABLE, 0)
    assert fake_client.calls[-1].kwargs["value"] is False
    client.write(Register.COIL_ENABLE, 1)
    assert fake_client.calls[-1].kwargs["value"] is True


# ---------- B4: identify_model doesn't overwrite explicit model ----------

def test_identify_model_keeps_explicit_model_on_disagreement(client, fake_client):
    """B4 regression: the client fixture is configured for GEN_2_0. If
    the device disagrees, identify_model returns the device-reported
    value but must NOT overwrite the configured one."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"55370250"}))
    result = client.identify_model()
    assert result is SmartPowerModel.GEN_1_0
    assert client.model is SmartPowerModel.GEN_2_0  # unchanged


def test_identify_model_sets_when_unconfigured(fake_client):
    """B4: when no model is configured, identify_model still resolves and
    stores it (the lock change must not break this happy path)."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"55370112"}))
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01, retries=0)
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    try:
        result = c.identify_model()
        assert result is SmartPowerModel.GEN_2_0
        assert c.model is SmartPowerModel.GEN_2_0
    finally:
        c.close()
