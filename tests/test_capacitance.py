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

Also covers the second-pair / write-side additions:

- ``read_second_capacitance`` mirrors ``read_capacitance`` against the
  ``HOLD_REG_SECOND_CAP_*`` pair (0x3012 / 0x3013).
- ``write_capacitance`` and ``write_second_capacitance`` encode a Farads
  value and emit one atomic FC 0x10 (Write Multiple Registers) write.
- ``_encode_capacitance`` round-trips, maximises uint16 precision, and
  rejects bad inputs.
"""

from __future__ import annotations

import math

import pytest

from smartpower_modbus import (
    InvalidValueError,
    Register,
    SmartPowerClient,
    SmartPowerModel,
)
from smartpower_modbus.client import _encode_capacitance

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


# ---------- _encode_capacitance: pure unit tests ----------

def test_encode_capacitance_microfarad():
    """100 µF must encode to a mantissa in [6554, 65535] so a round-trip
    keeps full uint16 precision."""
    val, exp = _encode_capacitance(100e-6)
    assert val == 10000
    assert exp == -8
    assert val * (10.0 ** exp) == pytest.approx(100e-6)


def test_encode_capacitance_zero():
    """Zero is the only value where the (val, exp) pair is ambiguous; we
    canonicalise it to (0, 0)."""
    assert _encode_capacitance(0.0) == (0, 0)


def test_encode_capacitance_picofarad_round_trip():
    val, exp = _encode_capacitance(1e-12)
    assert val * (10.0 ** exp) == pytest.approx(1e-12)


@pytest.mark.parametrize("value_F", [1e-12, 1e-9, 1e-6, 100e-6, 1e-3, 1.0, 1.234e-6])
def test_encode_capacitance_round_trip(value_F):
    """Across the plausible capacitor range, encode→decode must preserve
    the input value to within float-rounding tolerance."""
    val, exp = _encode_capacitance(value_F)
    assert 0 <= val <= 0xFFFF
    assert -30 <= exp <= 6
    assert val * (10.0 ** exp) == pytest.approx(value_F, rel=1e-4)


def test_encode_capacitance_rejects_negative():
    with pytest.raises(InvalidValueError, match="non-negative"):
        _encode_capacitance(-1e-6)


def test_encode_capacitance_rejects_nan():
    with pytest.raises(InvalidValueError, match="non-negative"):
        _encode_capacitance(math.nan)


def test_encode_capacitance_rejects_inf():
    with pytest.raises(InvalidValueError, match="non-negative"):
        _encode_capacitance(math.inf)


def test_encode_capacitance_rejects_bool():
    """bool is an int subclass — reject it explicitly so write_capacitance(True)
    can't sneak through as 1.0 F."""
    with pytest.raises(InvalidValueError, match="real number"):
        _encode_capacitance(True)


def test_encode_capacitance_rejects_too_small():
    with pytest.raises(InvalidValueError, match=r"exponent in \[-30, 6\]"):
        _encode_capacitance(1e-35)


def test_encode_capacitance_rejects_too_large():
    with pytest.raises(InvalidValueError, match=r"exponent in \[-30, 6\]"):
        _encode_capacitance(1e12)


# ---------- read_second_capacitance ----------

def test_read_second_capacitance_normal_pair(client, fake_client):
    """200 × 10^-5 = 2 mF — typical secondary tank cap."""
    fake_client.script(
        "read_holding_registers",
        _Resp(registers=[200, _signed16_to_raw(-5)]),
    )
    cap = client.read_second_capacitance()
    assert cap == pytest.approx(2e-3)


def test_read_second_capacitance_uses_single_call_at_0x3012(client, fake_client):
    """B2-style atomicity guard: one transaction, address 0x3012, count=2."""
    fake_client.calls.clear()
    fake_client.script("read_holding_registers", _Resp(registers=[100, _signed16_to_raw(-6)]))
    client.read_second_capacitance()
    holding_calls = [c for c in fake_client.calls if c.name == "read_holding_registers"]
    assert len(holding_calls) == 1
    call = holding_calls[0]
    assert call.address == Register.HOLD_REG_SECOND_CAP_VAL.addr  # 0x3012
    assert call.kwargs["count"] == 2


def test_read_second_capacitance_rejects_overflow_exponent(client, fake_client):
    fake_client.script("read_holding_registers", _Resp(registers=[1, 400]))
    with pytest.raises(InvalidValueError, match="exponent"):
        client.read_second_capacitance()


# ---------- write_capacitance ----------

def test_write_capacitance_emits_single_write_multiple_registers_call(client, fake_client):
    """B2-style atomicity: write_capacitance must use one FC 0x10 PDU at
    0x3008 carrying [val, exp_wire] — not two single-register writes."""
    fake_client.calls.clear()
    client.write_capacitance(100e-6)
    write_calls = [c for c in fake_client.calls if c.name == "write_registers"]
    assert len(write_calls) == 1, (
        f"expected 1 write_registers call, got {len(write_calls)}: {write_calls}"
    )
    call = write_calls[0]
    assert call.address == Register.HOLD_REG_CAP_VAL.addr  # 0x3008
    # 100 µF → (10000, -8); exp -8 on the wire is 0xFFF8.
    assert call.kwargs["values"] == [10000, 0xFFF8]


def test_write_capacitance_uses_no_single_register_writes(client, fake_client):
    """Belt-and-braces: no FC 0x06 (write_register) calls should leak out
    on the write_capacitance path."""
    fake_client.calls.clear()
    client.write_capacitance(1e-3)
    single_writes = [c for c in fake_client.calls if c.name == "write_register"]
    assert single_writes == []


@pytest.mark.parametrize("value_F", [1e-12, 1e-9, 1e-6, 100e-6, 1e-3, 1.0])
def test_write_then_read_capacitance_round_trip(value_F, client, fake_client):
    """End-to-end: write_capacitance(v) followed by reading the same val+exp
    pair back through the fake transport returns approximately v."""
    fake_client.calls.clear()
    client.write_capacitance(value_F)
    written = fake_client.calls[-1].kwargs["values"]
    # Mirror the encoder: exp_wire is unsigned16(exp); decoding goes back
    # through signed16. Have the fake reply with the same payload on read.
    fake_client.script(
        "read_holding_registers", _Resp(registers=list(written)),
    )
    assert client.read_capacitance() == pytest.approx(value_F, rel=1e-4)


def test_write_capacitance_zero_round_trip(client, fake_client):
    fake_client.calls.clear()
    client.write_capacitance(0.0)
    written = fake_client.calls[-1].kwargs["values"]
    assert written == [0, 0]
    fake_client.script("read_holding_registers", _Resp(registers=[0, 0]))
    assert client.read_capacitance() == 0.0


def test_write_capacitance_rejects_negative(client, fake_client):
    fake_client.calls.clear()
    with pytest.raises(InvalidValueError, match="non-negative"):
        client.write_capacitance(-1e-6)
    # And nothing should hit the wire.
    assert [c for c in fake_client.calls if c.name == "write_registers"] == []


def test_write_capacitance_rejects_nonfinite(client):
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(InvalidValueError, match="non-negative"):
            client.write_capacitance(bad)


def test_write_capacitance_rejects_too_large(client):
    with pytest.raises(InvalidValueError, match=r"exponent in \[-30, 6\]"):
        client.write_capacitance(1e12)


def test_write_capacitance_rejects_too_small(client):
    with pytest.raises(InvalidValueError, match=r"exponent in \[-30, 6\]"):
        client.write_capacitance(1e-35)


def test_write_capacitance_accepts_int_input(client, fake_client):
    """Common ergonomic case: caller passes an int (e.g. 1 for 1 F)."""
    fake_client.calls.clear()
    client.write_capacitance(1)
    call = fake_client.calls[-1]
    assert call.name == "write_registers"
    # 1 F → (10000, -4); -4 wire = 0xFFFC.
    assert call.kwargs["values"] == [10000, 0xFFFC]


# ---------- write_second_capacitance ----------

def test_write_second_capacitance_writes_to_0x3012(client, fake_client):
    fake_client.calls.clear()
    client.write_second_capacitance(100e-6)
    call = fake_client.calls[-1]
    assert call.name == "write_registers"
    assert call.address == Register.HOLD_REG_SECOND_CAP_VAL.addr  # 0x3012
    assert call.kwargs["values"] == [10000, 0xFFF8]


def test_write_second_capacitance_round_trip(client, fake_client):
    """Symmetry check: writing then reading the second pair returns the
    same value (parallel to the first-pair round-trip)."""
    fake_client.calls.clear()
    client.write_second_capacitance(1e-6)
    written = fake_client.calls[-1].kwargs["values"]
    fake_client.script("read_holding_registers", _Resp(registers=list(written)))
    assert client.read_second_capacitance() == pytest.approx(1e-6, rel=1e-4)
