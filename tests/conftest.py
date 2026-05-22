"""Shared test fakes + fixtures.

Going through a real Modbus RTU server would require a paired virtual serial
port (com0com / socat), which isn't portable in CI. Instead we replace
``_Transport._client`` with a stub that records the exact pymodbus call and
returns canned responses. The fakes live here (rather than inside one test
module) so peer test files don't have to cross-import from a test file.

This file also declares the opt-in plumbing for the live-hardware suite
under ``tests/hardware/``. The hardware tests are gated by markers
(``hardware``, ``hardware_write``, ``hardware_fault``) and require the
matching CLI flag (``--hardware``, ``--allow-writes``,
``--allow-fault-injection``) — without them the marked tests are auto-
skipped, so the default ``pytest -q`` run remains fast and fake-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from smartpower_modbus import SmartPowerClient, SmartPowerModel

# ---------- Hardware test gating ----------

def pytest_addoption(parser):
    """Register the live-hardware opt-in flags.

    These options live on the top-level ``tests/conftest.py`` rather than
    ``tests/hardware/conftest.py`` because pytest only honours
    ``pytest_addoption`` from conftests at or above the rootdir — a
    nested conftest cannot register new CLI options.
    """
    group = parser.getgroup(
        "smartpower-hardware",
        "Live-hardware test options (tests/hardware/). All off by default.",
    )
    group.addoption(
        "--hardware", action="store_true", default=False,
        help="Enable @pytest.mark.hardware tests against a real SmartPower module.",
    )
    group.addoption(
        "--allow-writes", action="store_true", default=False,
        help="Allow @pytest.mark.hardware_write tests to perform Modbus writes. "
             "Implies --hardware.",
    )
    group.addoption(
        "--allow-fault-injection", action="store_true", default=False,
        help="Allow @pytest.mark.hardware_fault tests to trigger Modbus exceptions / "
             "timeouts. Implies --hardware.",
    )
    group.addoption(
        "--port", action="store", default=None,
        help="Serial port for the live-hardware suite (e.g. /dev/ttyUSB0, COM5).",
    )
    group.addoption(
        "--baud", action="store", type=int, default=None,
        help="Baud rate for the live-hardware suite (default: SmartPower DEFAULT_BAUDRATE).",
    )
    group.addoption(
        "--slave-id", action="store", type=int, default=1,
        help="Modbus slave ID for the live-hardware suite (default: 1).",
    )
    group.addoption(
        "--model", action="store", default=None,
        help="SmartPower model name (e.g. SmartPowerGen_2.0). Omit to auto-identify via FC 0x2B.",
    )
    group.addoption(
        "--temperature-unit", action="store", choices=["C", "K", "F"], default="C",
        help="Display unit for temperature reads in the hardware suite (default: C).",
    )
    group.addoption(
        "--hw-timeout", action="store", type=float, default=1.0,
        help="Modbus response timeout in seconds for the hardware suite (default: 1.0).",
    )
    group.addoption(
        "--hw-retries", action="store", type=int, default=0,
        help="Transport-level read retry budget for the hardware suite (default: 0 — test raw behaviour).",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip hardware-marked tests unless the matching opt-in flag is set.

    Skip reasons are explicit so a developer running ``pytest -v`` can
    see at a glance why a test was skipped. ``hardware_write`` and
    ``hardware_fault`` imply ``hardware`` — if the implied flag is also
    missing, *both* are missing and we still surface a single clear reason.
    """
    hardware = config.getoption("--hardware")
    allow_writes = config.getoption("--allow-writes")
    allow_faults = config.getoption("--allow-fault-injection")
    port = config.getoption("--port")

    skip_no_hardware = pytest.mark.skip(
        reason="hardware test — pass --hardware --port=PORT to enable",
    )
    skip_no_port = pytest.mark.skip(
        reason="hardware test — --hardware set but --port=PORT is required",
    )
    skip_no_writes = pytest.mark.skip(
        reason="hardware-write test — pass --allow-writes to enable",
    )
    skip_no_faults = pytest.mark.skip(
        reason="hardware-fault test — pass --allow-fault-injection to enable",
    )

    for item in items:
        is_hw = "hardware" in item.keywords
        is_write = "hardware_write" in item.keywords
        is_fault = "hardware_fault" in item.keywords
        if not (is_hw or is_write or is_fault):
            continue
        if not hardware:
            item.add_marker(skip_no_hardware)
            continue
        if not port:
            item.add_marker(skip_no_port)
            continue
        if is_write and not allow_writes:
            item.add_marker(skip_no_writes)
            continue
        if is_fault and not allow_faults:
            item.add_marker(skip_no_faults)
            continue

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
