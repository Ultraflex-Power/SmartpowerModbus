"""SmartPower Modbus RTU client library.

Quickstart::

    from smartpower_modbus import SmartPowerClient, SmartPowerModel, Register

    with SmartPowerClient(
        "COM5", slave_id=1, model=SmartPowerModel.GEN_2_0,
    ) as c:
        out_p = c.read(Register.INPUT_REG_OUT_P)
        c.write(Register.HOLD_REG_SP_P, 50)

Public model names:

- ``SmartPowerModel.SOLO``    (``"SmartPowerSolo"``)
- ``SmartPowerModel.GEN_1_0`` (``"SmartPowerGen_1.0"``)
- ``SmartPowerModel.GEN_1_5`` (``"SmartPowerGen_1.5"``)
- ``SmartPowerModel.GEN_2_0`` (``"SmartPowerGen_2.0"``)

The mapping from these public model names to the underlying firmware-repo
branch is centralised in ``smartpower_modbus.models``.
"""

from __future__ import annotations

import logging

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
from .models import SmartPowerModel
from .registers import Register, RegisterKind, RegisterMeta, signed16, unsigned16

# FirmwareBranch is an internal implementation detail; it is importable for
# the rare case of inspecting which firmware branch underlies a model, but
# new code should use SmartPowerModel.
from .branches import FirmwareBranch  # noqa: F401  (kept exportable but not in __all__)

__all__ = [
    "DEFAULT_BAUDRATE",
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
    "SmartPowerModel",
    "UnsupportedFirmwareBranchError",
    "UnsupportedRegisterError",
    "signed16",
    "unsigned16",
]

logging.getLogger(__name__).addHandler(logging.NullHandler())
