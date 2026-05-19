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
    """Transport-level Modbus failure. Retryable unless a subclass says otherwise."""


class ModbusTimeoutError(ModbusCommError):
    """No response received within the configured timeout."""


class ModbusCrcError(ModbusCommError):
    """Response received but its CRC or framing was invalid."""


class IllegalAddressError(ModbusCommError):
    """Modbus exception response 0x02 — slave reports the address is illegal."""


class IllegalValueError(ModbusCommError):
    """Modbus exception response 0x03 — slave reports the value is illegal."""


class SlaveDeviceFailureError(ModbusCommError):
    """Modbus exception response 0x04 — slave is reporting an internal failure."""
