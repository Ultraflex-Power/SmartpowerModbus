"""Happy-path reads, writes, low-level passthrough, dump, and read_many batching.

Going through a real Modbus RTU server would require a paired virtual serial
port (com0com / socat), which isn't portable in CI. The shared fakes
(:class:`FakeSerialClient`, ``_Resp``, ``_ExcResp``, …) and the wired-up
``fake_client`` / ``client`` fixtures live in ``conftest.py``; sibling
modules cover error translation (``test_transport_errors.py``), device
info / model identification (``test_device_info.py``) and connect/close
lifecycle (``test_client_lifecycle.py``).

This module asserts that reads + writes round-trip the right pymodbus
method, kwargs, address, and count, and that the high-level batching
path (``read_many``) collapses contiguous reads into a single
transaction.
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    InvalidValueError,
    ReadOnlyRegisterError,
    Register,
    SmartPowerClient,
    SmartPowerModel,
    UnsupportedRegisterError,
)

from .conftest import _ExcResp, _Resp

# ---------- Tests: reads ----------

def test_read_input_register_returns_signed_when_declared(client, fake_client):
    fake_client.script("read_input_registers", _Resp(registers=[0xFFFE]))
    value = client.read(Register.INPUT_REG_IN_COOLANT_T)  # signed
    assert value == -2
    call = fake_client.calls[-1]
    assert call.name == "read_input_registers"
    assert call.address == 0x200B
    assert call.kwargs["count"] == 1
    assert call.kwargs["slave"] == 1


def test_read_input_register_returns_unsigned_when_not_signed(client, fake_client):
    fake_client.script("read_input_registers", _Resp(registers=[0xFFFE]))
    assert client.read(Register.INPUT_REG_OUT_P) == 0xFFFE


def test_read_holding_register_routes_to_holding_method(client, fake_client):
    fake_client.script("read_holding_registers", _Resp(registers=[1234]))
    assert client.read(Register.HOLD_REG_SP_P) == 1234
    assert fake_client.calls[-1].name == "read_holding_registers"
    assert fake_client.calls[-1].address == 0x3007


def test_read_coil_returns_bool(client, fake_client):
    fake_client.script("read_coils", _Resp(bits=[True, False, False, False, False, False, False, False]))
    assert client.read(Register.COIL_ENABLE) is True


def test_read_discrete_returns_bool(client, fake_client):
    fake_client.script("read_discrete_inputs", _Resp(bits=[True]))
    assert client.read(Register.INPUT_FAULT) is True


# ---------- Tests: writes ----------

def test_write_rejects_negative_for_unsigned_register(client):
    """HOLD_REG_SP_P is unsigned (range 0..65535). Writing -1 must raise
    rather than silently wrap to 0xFFFF — see code-review Bug 4c."""
    with pytest.raises(InvalidValueError, match="uint16"):
        client.write(Register.HOLD_REG_SP_P, -1)


def test_write_accepts_negative_for_signed_register(client, fake_client):
    """HOLD_REG_THERMO_REG_EXT_SP is signed (range -32768..32767). A
    negative value is written through as a two's-complement uint16 on
    the wire."""
    # That register is only available on SOLO/GEN_1_0, so we need a
    # different client. Build one inline against the same fake.
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_1_0,
        timeout=0.01, retries=0,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    try:
        c.write(Register.HOLD_REG_THERMO_REG_EXT_SP, -1)
        call = fake_client.calls[-1]
        assert call.name == "write_register"
        assert call.kwargs["value"] == 0xFFFF
    finally:
        c.close()


def test_write_coil_routes_to_write_coil(client, fake_client):
    client.write(Register.COIL_ENABLE, True)
    call = fake_client.calls[-1]
    assert call.name == "write_coil"
    assert call.kwargs["value"] is True


def test_write_rejects_readonly_register(client):
    with pytest.raises(ReadOnlyRegisterError):
        client.write(Register.INPUT_FAULT, True)
    with pytest.raises(ReadOnlyRegisterError):
        client.write(Register.INPUT_REG_OUT_P, 10)


def test_write_rejects_out_of_range_value(client):
    with pytest.raises(InvalidValueError):
        client.write(Register.HOLD_REG_SP_P, 70000)


def test_write_coil_rejects_int_passing_as_value(client, fake_client):
    # bool is acceptable, plain int is coerced to bool by write(); this just
    # confirms it doesn't raise.
    client.write(Register.COIL_ENABLE, 0)
    assert fake_client.calls[-1].kwargs["value"] is False


def test_write_holding_rejects_bool(client):
    with pytest.raises(InvalidValueError):
        client.write(Register.HOLD_REG_SP_P, True)


# ---------- Tests: branch + register validation ----------

def test_read_unsupported_register_for_model_raises(client):
    # SmartPowerGen_2.0 does not expose THERMO_REG_LIMIT.
    with pytest.raises(UnsupportedRegisterError):
        client.read(Register.INPUT_REG_THERMO_REG_LIMIT)


def test_unsupported_register_does_not_touch_wire(client, fake_client):
    fake_client.calls.clear()
    with pytest.raises(UnsupportedRegisterError):
        client.read(Register.HOLD_REG_THERMO_REG_EXT_SP)
    assert fake_client.calls == []


# ---------- Tests: low-level methods ----------

def test_low_level_read_holding_passes_through(client, fake_client):
    fake_client.script("read_holding_registers", _Resp(registers=[1, 2, 3]))
    out = client.read_holding(0x3000, count=3)
    assert out == [1, 2, 3]
    assert fake_client.calls[-1].kwargs["count"] == 3


def test_low_level_write_coils_passes_through(client, fake_client):
    client.write_coils(0x1000, [True, False, True])
    call = fake_client.calls[-1]
    assert call.name == "write_coils"
    assert call.kwargs["values"] == [True, False, True]


# ---------- Tests: dump ----------

def test_dump_skips_illegal_address(client, fake_client):
    # Make every input-register read succeed but every holding-register
    # read fail with an illegal-address response. Coils/discretes also
    # succeed (FakeSerialClient default).
    fake_client.scripts.clear()
    n_hold = sum(
        1 for r in SmartPowerModel.GEN_2_0.registers if r.kind.name == "HOLDING_REG"
    )
    fake_client.script("read_holding_registers", *([_ExcResp(0x02)] * n_hold))
    result = client.dump()
    hold_in_result = [r for r in result if r.kind.name == "HOLDING_REG"]
    assert hold_in_result == []


# ---------- Tests: read_many batching ----------

def test_read_many_batches_contiguous_holding_registers(client, fake_client):
    """SP_I/SP_P sit at adjacent addresses 0x3006/0x3007 — a single FC03
    read for count=2 must serve both, not two separate FC03 calls."""
    fake_client.script(
        "read_holding_registers", _Resp(registers=[111, 222]),
    )
    out = client.read_many([Register.HOLD_REG_SP_I, Register.HOLD_REG_SP_P])
    assert out == {Register.HOLD_REG_SP_I: 111, Register.HOLD_REG_SP_P: 222}
    holding_calls = [c for c in fake_client.calls if c.name == "read_holding_registers"]
    assert len(holding_calls) == 1
    assert holding_calls[0].address == 0x3006
    assert holding_calls[0].kwargs["count"] == 2


def test_read_many_splits_runs_on_address_gap(client, fake_client):
    """OUT_P (0x2011) and FREQ (0x2015) are non-contiguous → two reads,
    one per island, not a wasteful read of every address in between."""
    fake_client.script(
        "read_input_registers",
        _Resp(registers=[42]),    # OUT_P @ 0x2011
        _Resp(registers=[3000]),  # FREQ  @ 0x2015
    )
    out = client.read_many([Register.INPUT_REG_OUT_P, Register.INPUT_REG_FREQ])
    assert out == {Register.INPUT_REG_OUT_P: 42, Register.INPUT_REG_FREQ: 3000}
    input_calls = [c for c in fake_client.calls if c.name == "read_input_registers"]
    assert [c.address for c in input_calls] == [0x2011, 0x2015]
    assert all(c.kwargs["count"] == 1 for c in input_calls)


def test_read_many_groups_by_kind(client, fake_client):
    """Discrete inputs, input regs and holding regs each go to their own
    pymodbus method, even when interleaved in the request list."""
    fake_client.script("read_discrete_inputs", _Resp(bits=[True]))
    fake_client.script("read_input_registers", _Resp(registers=[7]))
    fake_client.script("read_holding_registers", _Resp(registers=[99]))
    out = client.read_many([
        Register.INPUT_FAULT, Register.INPUT_REG_OUT_P, Register.HOLD_REG_SP_P,
    ])
    assert out[Register.INPUT_FAULT] is True
    assert out[Register.INPUT_REG_OUT_P] == 7
    assert out[Register.HOLD_REG_SP_P] == 99


def test_read_many_applies_signed_recovery(client, fake_client):
    """Signed input registers must come back as int16, not uint16."""
    fake_client.script("read_input_registers", _Resp(registers=[0xFFFE]))
    out = client.read_many([Register.INPUT_REG_IN_COOLANT_T])
    assert out[Register.INPUT_REG_IN_COOLANT_T] == -2


def test_read_many_dedupes_duplicate_register(client, fake_client):
    """Asking for the same register twice must still hit the wire only
    once and surface a single dict entry."""
    fake_client.script("read_input_registers", _Resp(registers=[123]))
    out = client.read_many([Register.INPUT_REG_OUT_P, Register.INPUT_REG_OUT_P])
    assert out == {Register.INPUT_REG_OUT_P: 123}
    input_calls = [c for c in fake_client.calls if c.name == "read_input_registers"]
    assert len(input_calls) == 1


def test_read_many_validates_model_before_wire(client, fake_client):
    """Asking for a register the model doesn't expose must raise without
    touching the wire — same contract as read()."""
    fake_client.calls.clear()
    # HOLD_REG_THERMO_REG_EXT_SP doesn't exist on GEN_2_0.
    with pytest.raises(UnsupportedRegisterError):
        client.read_many([
            Register.HOLD_REG_SP_P, Register.HOLD_REG_THERMO_REG_EXT_SP,
        ])
    assert fake_client.calls == []
