"""Exception hierarchy for the SmartPower Modbus library."""

from __future__ import annotations


class SmartPowerError(Exception):
    """Base for everything this library raises."""


class UnsupportedFirmwareBranchError(SmartPowerError):
    """The requested firmware branch is not known to this library."""


class UnsupportedRegisterError(SmartPowerError):
    """The register is not exposed by the firmware branch in use."""


class ReadOnlyRegisterError(SmartPowerError):
    """Attempted to write a discrete input or input register."""


class InvalidValueError(SmartPowerError):
    """Value is outside the range or type allowed for this register."""


class SerialPortError(SmartPowerError):
    """The underlying serial port could not be opened or held open."""


class ModbusCommError(SmartPowerError):
    """Transport-level Modbus failure.

    Retry behaviour is decided by the *caller* of the transport: reads
    are retried when ``retries > 0``; writes are NOT retried by default
    (set ``retry_writes=True`` to opt in). The subclasses below split
    transport errors (retried for reads) from deterministic Modbus
    exception responses (never retried).
    """


class ModbusTimeoutError(ModbusCommError):
    """No response received within the configured timeout.

    Treated as transient — retried for reads when ``retries > 0`` and for
    writes when ``retry_writes=True``.
    """


class ModbusCrcError(ModbusCommError):
    """Response received but its CRC or framing was invalid.

    Treated as transient on the wire (single corrupted frame); follows the
    same retry rule as :class:`ModbusTimeoutError`. Persistent CRC errors
    typically indicate a wiring or termination problem.
    """


class IllegalFunctionError(ModbusCommError):
    """Modbus exception response 0x01 — slave does not support this function code.

    Deterministic: never retried, since the slave will reject every
    attempt the same way.
    """


class IllegalAddressError(ModbusCommError):
    """Modbus exception response 0x02 — slave reports the address is illegal.

    Deterministic: never retried.
    """


class IllegalValueError(ModbusCommError):
    """Modbus exception response 0x03 — slave reports the value is illegal.

    Deterministic: never retried.
    """


class SlaveDeviceFailureError(ModbusCommError):
    """Modbus exception response 0x04 — slave is reporting an internal failure.

    Deterministic from the protocol layer's perspective: never retried.
    Recovery typically requires inspecting or resetting the slave.
    """
