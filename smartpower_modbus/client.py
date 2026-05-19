"""High-level Modbus RTU client for SmartPower control boards."""

from __future__ import annotations

import logging
import threading
import warnings
from typing import Iterable

from .branches import FirmwareBranch
from .exceptions import (
    IllegalAddressError,
    InvalidValueError,
    ReadOnlyRegisterError,
    SmartPowerError,
    UnsupportedFirmwareBranchError,
)
from .models import SmartPowerModel
from .registers import (
    Register,
    RegisterKind,
    assert_supported,
    signed16,
    unsigned16,
)
from ._transport import _Transport

logger = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 38400  # MODBUS::DEF_BAUD_RATE from ModBus.hpp


def _coerce_model(value) -> SmartPowerModel:
    """Accept a SmartPowerModel, a public model name string, a FirmwareBranch
    (deprecated), or a firmware-branch string (deprecated) and return the
    canonical SmartPowerModel."""
    if isinstance(value, SmartPowerModel):
        return value
    if isinstance(value, FirmwareBranch):
        warnings.warn(
            "Passing a FirmwareBranch as the SmartPower model is deprecated; "
            "pass a SmartPowerModel value instead.",
            DeprecationWarning, stacklevel=3,
        )
        return value.model
    if isinstance(value, str):
        # SmartPowerModel.from_name itself emits a deprecation warning when
        # given a firmware-branch string.
        return SmartPowerModel.from_name(value)
    raise TypeError(
        f"model must be a SmartPowerModel, str, or FirmwareBranch — got "
        f"{type(value).__name__}"
    )


class SmartPowerClient:
    """Modbus RTU client tailored to SmartPower power-supply modules.

    Combine ``port`` + ``slave_id`` + ``model`` to talk to a specific
    module. Every call is guarded by an internal lock — safe to share one
    client across threads, but a single port is half-duplex so only one
    transaction proceeds at a time.

    Use as a context manager::

        from smartpower_modbus import SmartPowerClient, SmartPowerModel, Register

        with SmartPowerClient(
            "COM5", slave_id=1, model=SmartPowerModel.GEN_2_0,
        ) as c:
            print(c.read(Register.INPUT_REG_OUT_P))
    """

    def __init__(
        self,
        port: str,
        slave_id: int,
        model=None,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        parity: str = "N",
        stopbits: int = 1,
        bytesize: int = 8,
        timeout: float = 1.0,
        retries: int = 2,
        branch=None,
    ) -> None:
        # Backward-compat: accept the deprecated ``branch=`` kwarg.
        if model is None and branch is None:
            raise TypeError("SmartPowerClient requires a `model=` argument")
        if model is not None and branch is not None:
            raise TypeError("Pass either `model=` or `branch=`, not both")
        if branch is not None:
            warnings.warn(
                "SmartPowerClient(branch=...) is deprecated; "
                "pass model=SmartPowerModel.<MODEL> instead.",
                DeprecationWarning, stacklevel=2,
            )
            model = branch
        self.model: SmartPowerModel = _coerce_model(model)

        if not 1 <= slave_id <= 247:
            raise InvalidValueError(
                f"slave_id must be 1..247 (Modbus RTU); got {slave_id}"
            )
        self.port = port
        self.slave_id = slave_id
        self._lock = threading.Lock()
        self._transport = _Transport(
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=bytesize,
            timeout=timeout,
            slave_id=slave_id,
            retries=retries,
        )
        self._connected = False

    @property
    def branch(self) -> FirmwareBranch:
        """Internal firmware branch backing the configured model.

        .. deprecated::
            Use ``client.model`` instead. The firmware branch is an
            implementation detail of the model and may change.
        """
        warnings.warn(
            "SmartPowerClient.branch is deprecated; use SmartPowerClient.model.",
            DeprecationWarning, stacklevel=2,
        )
        return self.model.firmware_branch

    # ----- lifecycle -----

    def connect(self) -> "SmartPowerClient":
        with self._lock:
            if not self._connected:
                self._transport.connect()
                self._connected = True
                logger.info(
                    "Connected to %s slave=%d model=%s",
                    self.port, self.slave_id, self.model.value,
                )
        return self

    def close(self) -> None:
        with self._lock:
            if self._connected:
                self._transport.close()
                self._connected = False
                logger.info("Closed connection to %s", self.port)

    def __enter__(self) -> "SmartPowerClient":
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----- low-level: take raw addresses ----------

    def read_holding(self, addr: int, count: int = 1) -> list[int]:
        with self._lock:
            return self._transport.read_holding(addr, count)

    def read_input(self, addr: int, count: int = 1) -> list[int]:
        with self._lock:
            return self._transport.read_input(addr, count)

    def read_coils(self, addr: int, count: int = 1) -> list[bool]:
        with self._lock:
            return self._transport.read_coils(addr, count)

    def read_discretes(self, addr: int, count: int = 1) -> list[bool]:
        with self._lock:
            return self._transport.read_discretes(addr, count)

    def write_holding(self, addr: int, value: int) -> None:
        with self._lock:
            self._transport.write_holding(addr, unsigned16(value))

    def write_holdings(self, addr: int, values: list[int]) -> None:
        with self._lock:
            self._transport.write_holdings(addr, [unsigned16(v) for v in values])

    def write_coil(self, addr: int, value: bool) -> None:
        with self._lock:
            self._transport.write_coil(addr, bool(value))

    def write_coils(self, addr: int, values: list[bool]) -> None:
        with self._lock:
            self._transport.write_coils(addr, [bool(v) for v in values])

    # ----- high-level: take Register members ----------

    def read(self, reg: Register) -> int | bool:
        """Read a single register with model + type validation."""
        assert_supported(reg, self.model)
        with self._lock:
            if reg.kind is RegisterKind.COIL:
                return self._transport.read_coils(reg.addr, 1)[0]
            if reg.kind is RegisterKind.DISCRETE_INPUT:
                return self._transport.read_discretes(reg.addr, 1)[0]
            if reg.kind is RegisterKind.INPUT_REG:
                raw = self._transport.read_input(reg.addr, 1)[0]
            else:  # HOLDING_REG
                raw = self._transport.read_holding(reg.addr, 1)[0]
            return signed16(raw) if reg.signed else raw

    def write(self, reg: Register, value: int | bool) -> None:
        """Write a single register with model + type validation."""
        assert_supported(reg, self.model)
        if not reg.is_writable:
            raise ReadOnlyRegisterError(
                f"{reg.name} is a {reg.kind.value}; not writable."
            )
        if reg.kind is RegisterKind.COIL:
            if not isinstance(value, (bool, int)):
                raise InvalidValueError(
                    f"Coil {reg.name} accepts bool, got {type(value).__name__}"
                )
            with self._lock:
                self._transport.write_coil(reg.addr, bool(value))
            return
        # HOLDING_REG
        if isinstance(value, bool):
            raise InvalidValueError(
                f"Holding register {reg.name} expects int, got bool"
            )
        if not isinstance(value, int):
            raise InvalidValueError(
                f"Holding register {reg.name} expects int, got {type(value).__name__}"
            )
        with self._lock:
            self._transport.write_holding(reg.addr, unsigned16(value))

    def read_many(self, regs: Iterable[Register]) -> dict[Register, int | bool]:
        """Read several registers individually."""
        out: dict[Register, int | bool] = {}
        for reg in regs:
            out[reg] = self.read(reg)
        return out

    def dump(self) -> dict[Register, int | bool]:
        """Read every register exposed by the configured model."""
        out: dict[Register, int | bool] = {}
        for reg in sorted(Register.for_model(self.model), key=lambda r: (r.kind.value, r.addr)):
            try:
                out[reg] = self.read(reg)
            except IllegalAddressError:
                logger.warning(
                    "Slave reports %s (0x%04X) as illegal — skipping",
                    reg.name, reg.addr,
                )
        return out

    # ----- model probe ----------

    def probe_model(self) -> tuple[SmartPowerModel, ...]:
        """Best-effort identification of the SmartPower model.

        Reads address ``0x2021`` (``INPUT_REG_THERMO_REG_LIMIT``), which
        diverges across the four supported firmwares, and returns the
        candidate models:

        - Read succeeds → models with extended thermo regulation:
          ``SmartPowerModel.SOLO`` and ``SmartPowerModel.GEN_1_0``.
        - Slave returns illegal-address → models without it:
          ``SmartPowerModel.GEN_1_5`` and ``SmartPowerModel.GEN_2_0``.

        Models within each group are structurally identical at the Modbus
        layer (the only firmware-side difference is the spelling
        ``ACIVE_PROFILE``/``ACTIVE_PROFILE``), so a unique answer is not
        possible. A warning is logged if the result is inconsistent with
        the model configured on the client.
        """
        probe_reg = Register.INPUT_REG_THERMO_REG_LIMIT
        try:
            with self._lock:
                self._transport.read_input(probe_reg.addr, 1)
            has_ext_thermo = True
        except IllegalAddressError:
            has_ext_thermo = False

        if has_ext_thermo:
            candidates = (SmartPowerModel.SOLO, SmartPowerModel.GEN_1_0)
        else:
            candidates = (SmartPowerModel.GEN_1_5, SmartPowerModel.GEN_2_0)
        if self.model not in candidates:
            logger.warning(
                "Configured model %s does not match probe result %s",
                self.model.value, [m.value for m in candidates],
            )
        return candidates

    def probe_branch(self) -> tuple[FirmwareBranch, ...]:
        """Deprecated alias for ``probe_model()`` returning firmware branches.

        .. deprecated::
            Use ``probe_model()`` instead.
        """
        warnings.warn(
            "SmartPowerClient.probe_branch() is deprecated; use probe_model().",
            DeprecationWarning, stacklevel=2,
        )
        return tuple(m.firmware_branch for m in self.probe_model())
