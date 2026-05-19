"""SmartPower Modbus RTU client library.

Quickstart::

    from smartpower_modbus import SmartPowerClient, FirmwareBranch, Register

    with SmartPowerClient("COM5", slave_id=1, branch=FirmwareBranch.MEGA_MAIN) as c:
        out_p = c.read(Register.INPUT_REG_OUT_P)
        c.write(Register.HOLD_REG_SP_P, 50)
"""

from __future__ import annotations

import logging

from .branches import FirmwareBranch
from .client import DEFAULT_BAUDRATE, SmartPowerClient
from .exceptions import (
    IllegalAddressError,
    IllegalValueError,
    InvalidValueError,
    ModbusCommError,
    ModbusCrcError,
    ModbusTimeoutError,
    ReadOnlyRegisterError,
    SerialPortError,
    SlaveDeviceFailureError,
    SmartPowerError,
    UnsupportedFirmwareBranchError,
    UnsupportedRegisterError,
)
from .registers import Register, RegisterKind, RegisterMeta, signed16, unsigned16

__all__ = [
    "DEFAULT_BAUDRATE",
    "FirmwareBranch",
    "IllegalAddressError",
    "IllegalValueError",
    "InvalidValueError",
    "ModbusCommError",
    "ModbusCrcError",
    "ModbusTimeoutError",
    "ReadOnlyRegisterError",
    "Register",
    "RegisterKind",
    "RegisterMeta",
    "SerialPortError",
    "SlaveDeviceFailureError",
    "SmartPowerClient",
    "SmartPowerError",
    "UnsupportedFirmwareBranchError",
    "UnsupportedRegisterError",
    "signed16",
    "unsigned16",
]

logging.getLogger(__name__).addHandler(logging.NullHandler())
