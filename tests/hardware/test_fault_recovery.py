"""Fault-injection and recovery against a real SmartPower module.

Every test deliberately triggers a Modbus exception or transport
failure, asserts the matching ``SmartPowerError`` subclass, and then
verifies a normal read still works on the same client (i.e. a fault
on one transaction does not leave the bus in a wedged state).

Gated by ``@pytest.mark.hardware_fault`` — requires both ``--hardware``
and ``--allow-fault-injection``.
"""

from __future__ import annotations

import logging

import pytest

from smartpower_modbus import (
    IllegalAddressError,
    IllegalFunctionError,
    ModbusCommError,
    ModbusTimeoutError,
    Register,
    SmartPowerClient,
)

pytestmark = pytest.mark.hardware_fault

logger = logging.getLogger(__name__)


# Address far outside any firmware's AppAddress_t table. 0x3FFF sits past
# the last holding register (0x3019) but inside the 16-bit space, so the
# slave will respond with Modbus exception 0x02 rather than ignoring the
# request entirely.
_KNOWN_BAD_ADDR = 0x3FFF


def _baseline_read(client: SmartPowerClient) -> None:
    """A cheap "the bus still works" probe used after each fault."""
    in_v = client.read(Register.INPUT_REG_IN_V)
    assert isinstance(in_v, int), (
        f"Recovery read returned {type(in_v).__name__}, not int — "
        f"the bus is wedged."
    )


def test_illegal_address_then_recovery(hw_client: SmartPowerClient) -> None:
    """Reading an unmapped holding-register address must raise
    ``IllegalAddressError`` (Modbus exception 0x02), and the very next
    read must succeed.
    """
    with pytest.raises(IllegalAddressError):
        # Use the raw API so the client-side ``assert_supported`` check
        # doesn't short-circuit before we hit the wire.
        hw_client.read_holding(_KNOWN_BAD_ADDR, 1)
    _baseline_read(hw_client)


def test_illegal_function_then_recovery(hw_client: SmartPowerClient) -> None:
    """FC 0x2B/0x0E with ``read_code != 0x01`` is not honoured by the
    SmartPower firmware (``MEI_DEV_ONE_OBJ_ENA`` is disabled). The
    slave returns Modbus exception 0x01 (or 0x02 on some firmware
    builds that route unsupported codes through the address handler).
    Either is a non-transient error — the test accepts both.
    """
    with pytest.raises((IllegalFunctionError, IllegalAddressError)):
        hw_client._transport.read_device_information(read_code=0x04, object_id=0)
    _baseline_read(hw_client)


def test_timeout_then_recovery(
    hw_client: SmartPowerClient, hw_port: str, hw_baud: int, hw_slave_id: int,
) -> None:
    """A sub-millisecond timeout against a real slave must surface as
    ``ModbusTimeoutError``, and a subsequent normal read on the *same*
    client must succeed once the timeout is restored.

    Implementation detail: we mutate ``hw_client._transport._client.timeout``
    via the pymodbus client object. The fact that we have to reach
    through two underscores is intentional friction — this is a test
    hook, not a public API.
    """
    pymb_client = hw_client._transport._client
    original_timeout = getattr(pymb_client, "timeout", None)
    try:
        try:
            pymb_client.timeout = 0.001
        except AttributeError:
            pytest.skip(
                "Underlying pymodbus client has no settable 'timeout' attribute "
                "in this version — cannot inject a timeout."
            )
        try:
            hw_client.read(Register.INPUT_REG_IN_V)
        except ModbusTimeoutError:
            pass
        except ModbusCommError as exc:
            # Some pymodbus / OS combinations report a 1 ms timeout as
            # a generic IO error rather than the specific timeout
            # string. Accept any ModbusCommError but log the type so a
            # downstream investigator knows what the driver said.
            logger.warning(
                "Timeout injection produced %s rather than ModbusTimeoutError: %s",
                type(exc).__name__, exc,
            )
        else:
            pytest.xfail(
                "Read succeeded inside a 1 ms timeout — slave is too fast "
                "or the OS clock granularity hid the deadline."
            )
    finally:
        if original_timeout is not None:
            pymb_client.timeout = original_timeout
    _baseline_read(hw_client)


def test_retries_do_not_swallow_persistent_failure(
    hw_client: SmartPowerClient,
) -> None:
    """Crank the session client's retry budget up and its timeout down,
    then confirm a sub-ms read still surfaces a ``ModbusCommError``
    after retries are exhausted — i.e. the retry loop does not silently
    swallow a persistent fault.

    Mutates ``hw_client._transport`` rather than opening a second
    ``SmartPowerClient`` on the same port: pyserial holds an exclusive
    lock, so a parallel client would fail with ``SerialPortError``
    rather than exercising the retry path.
    """
    pymb_client = hw_client._transport._client
    original_timeout = getattr(pymb_client, "timeout", None)
    original_retries = hw_client._transport._retries
    try:
        try:
            pymb_client.timeout = 0.001
        except AttributeError:
            pytest.skip(
                "Underlying pymodbus client has no settable 'timeout' attribute "
                "in this version — cannot inject a timeout."
            )
        hw_client._transport._retries = 2

        try:
            hw_client.read(Register.INPUT_REG_IN_V)
        except ModbusCommError:
            pass  # expected — retries exhausted, fault surfaced
        else:
            pytest.xfail(
                "Read succeeded inside a 1 ms timeout even with retries=2 — "
                "slave is too fast for this kind of injection."
            )
    finally:
        hw_client._transport._retries = original_retries
        if original_timeout is not None:
            pymb_client.timeout = original_timeout


def test_recovery_after_sweep_of_faults(hw_client: SmartPowerClient) -> None:
    """Run several faults back-to-back and verify the bus survives.

    A working recovery on *each* fault (above) is not the same as a
    working recovery when faults are interleaved — this catches any
    state that persists across calls (stale request id, half-parsed
    frame buffer, …).
    """
    sequence = [
        ("illegal_address", lambda: hw_client.read_holding(_KNOWN_BAD_ADDR, 1)),
        ("illegal_function", lambda: hw_client._transport.read_device_information(read_code=0x04, object_id=0)),
        ("illegal_address", lambda: hw_client.read_holding(_KNOWN_BAD_ADDR, 1)),
    ]
    for label, action in sequence:
        with pytest.raises(ModbusCommError):
            action()
        # Recovery between each fault.
        _baseline_read(hw_client)
        logger.info("%s: bus recovered, baseline read OK", label)
