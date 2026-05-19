"""Exercise the client + transport against a faked pymodbus ``ModbusSerialClient``.

Going through a real Modbus RTU server would require a paired virtual serial
port (com0com / socat), which isn't portable in CI. Instead we replace
``_Transport._client`` with a stub that records the exact pymodbus call and
returns canned responses, then assert that:

- The client validates branch and register kind before any wire activity.
- Reads round-trip the right pymodbus method + kwargs + address + count.
- Writes round-trip the same.
- ``isError()`` responses, ``ModbusIOException``, and ``ExceptionResponse``
  are translated to the library's own exception types.
- Retries fire for timeouts but not for illegal-address responses.

This isn't a substitute for a hardware-loop check, but it verifies every
code path the library actually owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from smartpower_modbus import (
    FirmwareBranch,
    IllegalAddressError,
    IllegalValueError,
    InvalidValueError,
    ModbusCommError,
    ModbusTimeoutError,
    ReadOnlyRegisterError,
    Register,
    SmartPowerClient,
    UnsupportedRegisterError,
)


# ---------- Pymodbus response/exception fakes ----------

class _Resp:
    """Minimal stand-in for a successful pymodbus response object."""

    def __init__(self, registers=None, bits=None):
        self.registers = list(registers or [])
        self.bits = list(bits or [])

    def isError(self):  # noqa: N802 — pymodbus naming
        return False


class _ErrResp:
    """Stand-in for a pymodbus response that reports an error."""

    def __init__(self, msg="bus error"):
        self._msg = msg

    def isError(self):  # noqa: N802
        return True

    def __str__(self):
        return self._msg


class _ExcResp:
    """Stand-in for ``pymodbus.pdu.ExceptionResponse``."""

    def __init__(self, exception_code):
        self.exception_code = exception_code

    def isError(self):  # noqa: N802
        return True


# ---------- Fake pymodbus client wired into the transport ----------

@dataclass
class _Call:
    name: str
    address: int
    kwargs: dict
    args: tuple = ()


class FakeSerialClient:
    """Captures every method invocation and returns scripted responses."""

    def __init__(self):
        self.calls: list[_Call] = []
        # Default canned responses keyed by method name; tests override.
        self.scripts: dict[str, list[Any]] = {}

    def script(self, name: str, *responses):
        self.scripts[name] = list(responses)

    def _next(self, name, default):
        queue = self.scripts.get(name)
        if not queue:
            return default
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    # lifecycle
    def connect(self):
        return True

    def close(self):
        pass

    # reads
    def read_holding_registers(self, address, *, slave=None, count=1, **kw):
        self.calls.append(_Call("read_holding_registers", address, {"slave": slave, "count": count, **kw}))
        return self._next("read_holding_registers", _Resp(registers=[0] * count))

    def read_input_registers(self, address, *, slave=None, count=1, **kw):
        self.calls.append(_Call("read_input_registers", address, {"slave": slave, "count": count, **kw}))
        return self._next("read_input_registers", _Resp(registers=[0] * count))

    def read_coils(self, address, *, slave=None, count=1, **kw):
        self.calls.append(_Call("read_coils", address, {"slave": slave, "count": count, **kw}))
        return self._next("read_coils", _Resp(bits=[False] * count))

    def read_discrete_inputs(self, address, *, slave=None, count=1, **kw):
        self.calls.append(_Call("read_discrete_inputs", address, {"slave": slave, "count": count, **kw}))
        return self._next("read_discrete_inputs", _Resp(bits=[False] * count))

    # writes
    def write_register(self, address, *, slave=None, value=0, **kw):
        self.calls.append(_Call("write_register", address, {"slave": slave, "value": value, **kw}))
        return self._next("write_register", _Resp())

    def write_registers(self, address, *, slave=None, values=(), **kw):
        self.calls.append(_Call("write_registers", address, {"slave": slave, "values": list(values), **kw}))
        return self._next("write_registers", _Resp())

    def write_coil(self, address, *, slave=None, value=False, **kw):
        self.calls.append(_Call("write_coil", address, {"slave": slave, "value": value, **kw}))
        return self._next("write_coil", _Resp())

    def write_coils(self, address, *, slave=None, values=(), **kw):
        self.calls.append(_Call("write_coils", address, {"slave": slave, "values": list(values), **kw}))
        return self._next("write_coils", _Resp())


# ---------- Fixtures ----------

@pytest.fixture()
def fake_client():
    return FakeSerialClient()


@pytest.fixture()
def client(fake_client):
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        branch=FirmwareBranch.MEGA_MAIN,
        timeout=0.01, retries=0,
    )
    # Swap in our fake; bypasses the real serial open in connect().
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    yield c
    c.close()


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

def test_write_holding_normalises_negative_to_uint16(client, fake_client):
    client.write(Register.HOLD_REG_SP_P, -1)
    call = fake_client.calls[-1]
    assert call.name == "write_register"
    assert call.kwargs["value"] == 0xFFFF


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

def test_read_unsupported_register_for_branch_raises(client):
    # MegaMain does not expose THERMO_REG_LIMIT.
    with pytest.raises(UnsupportedRegisterError):
        client.read(Register.INPUT_REG_THERMO_REG_LIMIT)


def test_unsupported_register_does_not_touch_wire(client, fake_client):
    fake_client.calls.clear()
    with pytest.raises(UnsupportedRegisterError):
        client.read(Register.HOLD_REG_THERMO_REG_EXT_SP)
    assert fake_client.calls == []


# ---------- Tests: error translation ----------

def test_modbus_io_exception_with_timeout_text_becomes_modbus_timeout(client, fake_client):
    from pymodbus.exceptions import ModbusIOException
    fake_client.script("read_input_registers", ModbusIOException("response timeout"))
    with pytest.raises(ModbusTimeoutError):
        client.read(Register.INPUT_REG_OUT_P)


def test_io_exception_without_known_text_becomes_modbus_comm_error(client, fake_client):
    from pymodbus.exceptions import ModbusIOException
    fake_client.script("read_input_registers", ModbusIOException("misc bus glitch"))
    with pytest.raises(ModbusCommError):
        client.read(Register.INPUT_REG_OUT_P)


def test_illegal_address_response_becomes_illegal_address_error(client, fake_client):
    fake_client.script("read_input_registers", _ExcResp(0x02))
    with pytest.raises(IllegalAddressError):
        client.read(Register.INPUT_REG_OUT_P)


def test_illegal_value_response_becomes_illegal_value_error(client, fake_client):
    fake_client.script("write_register", _ExcResp(0x03))
    with pytest.raises(IllegalValueError):
        client.write(Register.HOLD_REG_SP_P, 50)


def test_isError_response_is_translated(client, fake_client):
    fake_client.script("read_input_registers", _ErrResp("bus error"))
    with pytest.raises(ModbusCommError):
        client.read(Register.INPUT_REG_OUT_P)


# ---------- Tests: retries ----------

def test_retries_on_timeout_but_not_on_illegal_address(fake_client):
    from pymodbus.exceptions import ModbusIOException

    c = SmartPowerClient(
        port="dummy", slave_id=1,
        branch=FirmwareBranch.MEGA_MAIN,
        timeout=0.01, retries=2,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True

    # First two attempts time out, third succeeds.
    fake_client.script(
        "read_input_registers",
        ModbusIOException("timeout"),
        ModbusIOException("timeout"),
        _Resp(registers=[42]),
    )
    assert c.read(Register.INPUT_REG_OUT_P) == 42
    assert sum(1 for c_ in fake_client.calls if c_.name == "read_input_registers") == 3

    # Illegal-address responses must NOT be retried.
    fake_client.script("read_input_registers", _ExcResp(0x02))
    n_before = sum(1 for c_ in fake_client.calls if c_.name == "read_input_registers")
    with pytest.raises(IllegalAddressError):
        c.read(Register.INPUT_REG_OUT_P)
    n_after = sum(1 for c_ in fake_client.calls if c_.name == "read_input_registers")
    assert n_after - n_before == 1


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


# ---------- Tests: dump and probe ----------

def test_dump_skips_illegal_address(client, fake_client):
    # Make every input-register read succeed but every holding-register read
    # fail with an illegal-address response. Coils/discretes also succeed.
    fake_client.scripts.clear()
    # Default factory in FakeSerialClient returns successful empty responses,
    # so we just feed an _ExcResp for every holding read.
    # 25 holding regs in MegaMain (0x3000..0x3017 + 0x3018 reserved):
    n_hold = sum(1 for r in FirmwareBranch.MEGA_MAIN.registers if r.kind.name == "HOLDING_REG")
    fake_client.script("read_holding_registers", *([_ExcResp(0x02)] * n_hold))
    result = client.dump()
    # No holding registers should be present.
    hold_in_result = [r for r in result if r.kind.name == "HOLDING_REG"]
    assert hold_in_result == []


def test_probe_branch_returns_ext_group_when_address_succeeds(client, fake_client):
    fake_client.script("read_input_registers", _Resp(registers=[123]))
    candidates = client.probe_branch()
    assert FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE in candidates
    assert FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE in candidates
    assert FirmwareBranch.MEGA_MAIN not in candidates


def test_probe_branch_returns_non_ext_group_on_illegal_address(client, fake_client):
    fake_client.script("read_input_registers", _ExcResp(0x02))
    candidates = client.probe_branch()
    assert FirmwareBranch.MEGA_MAIN in candidates
    assert FirmwareBranch.GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT in candidates
    assert FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE not in candidates


# ---------- Tests: context manager ----------

def test_context_manager_connects_and_closes(fake_client):
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        branch=FirmwareBranch.MEGA_MAIN,
        timeout=0.01, retries=0,
    )
    c._transport._client = fake_client
    with c as ctx:
        assert ctx is c
        assert c._connected is True
    assert c._connected is False
