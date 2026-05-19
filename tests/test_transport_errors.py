"""Error translation, retry policy, and malformed-response handling.

These tests live one layer below the happy-path transport checks: they
assert how the wrapper reacts when pymodbus returns something other than
a normal :class:`_Resp`. Shared fakes and the ``client`` / ``fake_client``
fixtures come from ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    IllegalAddressError,
    IllegalValueError,
    ModbusCommError,
    ModbusTimeoutError,
    Register,
    SmartPowerClient,
    SmartPowerModel,
)

from .conftest import _ErrResp, _ExcResp, _Resp

# ---------- Error translation ----------

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


# ---------- Retry policy ----------

def test_retries_on_timeout_but_not_on_illegal_address(fake_client):
    from pymodbus.exceptions import ModbusIOException

    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
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


def test_writes_do_not_retry_by_default(fake_client):
    """Bug 3: writes must not auto-retry on timeout — a write after a
    timeout may have already been applied on the slave, and a retry
    would double-write."""
    from pymodbus.exceptions import ModbusIOException
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=2,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    fake_client.script(
        "write_register",
        ModbusIOException("response timeout"),
        ModbusIOException("response timeout"),
        ModbusIOException("response timeout"),
    )
    with pytest.raises(ModbusTimeoutError):
        c.write(Register.HOLD_REG_SP_P, 50)
    n_writes = sum(1 for call in fake_client.calls if call.name == "write_register")
    assert n_writes == 1, f"expected 1 write attempt, got {n_writes}"
    c.close()


def test_writes_retry_when_retry_writes_enabled(fake_client):
    """Opt-in: with ``retry_writes=True`` the old behaviour is back."""
    from pymodbus.exceptions import ModbusIOException
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=2,
        retry_writes=True,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    fake_client.script(
        "write_register",
        ModbusIOException("response timeout"),
        ModbusIOException("response timeout"),
        _Resp(),  # succeeds on third try
    )
    c.write(Register.HOLD_REG_SP_P, 50)
    n_writes = sum(1 for call in fake_client.calls if call.name == "write_register")
    assert n_writes == 3
    c.close()


# ---------- Malformed-response handling ----------

def test_transport_short_response_raises_modbus_comm_error(client, fake_client):
    """Bug 5: a response that succeeds but returns fewer registers than
    requested must surface as ModbusCommError, not IndexError."""
    fake_client.script("read_holding_registers", _Resp(registers=[1]))
    with pytest.raises(ModbusCommError, match="only 1"):
        client.read_holding(0x3000, count=3)


def test_transport_missing_registers_attr_raises_modbus_comm_error(client, fake_client):
    """Bug 5: a response object without the expected attribute must
    surface as ModbusCommError, not AttributeError."""
    class _BadResp:
        def isError(self):  # noqa: N802
            return False
    fake_client.script("read_input_registers", _BadResp())
    with pytest.raises(ModbusCommError, match="missing 'registers'"):
        client.read_input(0x2000, count=1)


def test_transport_null_registers_attr_raises_modbus_comm_error(client, fake_client):
    """Bug 5: payload=None on a successful response → ModbusCommError."""
    class _NoneResp:
        registers = None
        def isError(self):  # noqa: N802
            return False
    fake_client.script("read_input_registers", _NoneResp())
    with pytest.raises(ModbusCommError):
        client.read_input(0x2000, count=1)
