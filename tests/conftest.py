"""Shared test fakes + fixtures.

Going through a real Modbus RTU server would require a paired virtual serial
port (com0com / socat), which isn't portable in CI. Instead we replace
``_Transport._client`` with a stub that records the exact pymodbus call and
returns canned responses. The fakes live here (rather than inside one test
module) so peer test files don't have to cross-import from a test file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from smartpower_modbus import SmartPowerClient, SmartPowerModel

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


class _DeviceInfoResp:
    """Stand-in for ``pymodbus.pdu.mei_message.ReadDeviceInformationResponse``."""

    def __init__(self, information):
        self.information = information

    def isError(self):  # noqa: N802
        return False


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
        # Tracks how many times close() ran — useful for verifying that
        # the client cleans up on a failed connect() / auto-identify.
        self.close_count: int = 0

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
        self.close_count += 1

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

    # FC 0x2B/0x0E — Read Device Identification
    def read_device_information(self, *, read_code=0x01, object_id=0, slave=None, **kw):
        self.calls.append(_Call(
            "read_device_information", -1,
            {"read_code": read_code, "object_id": object_id, "slave": slave, **kw},
        ))
        return self._next(
            "read_device_information",
            _DeviceInfoResp({
                0: b"Ultraflex Power",
                1: b"55370112",
                2: b"1.0.0",
            }),
        )

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
    """A connected SmartPowerClient wired to the in-memory FakeSerialClient.

    Default model is GEN_2_0; override with ``smartpower_client_factory`` or
    construct directly if a test needs a different one.
    """
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=0,
    )
    # Swap in our fake; bypasses the real serial open in connect().
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    yield c
    c.close()
