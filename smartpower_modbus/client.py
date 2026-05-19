"""High-level Modbus RTU client for SmartPower control boards."""

from __future__ import annotations

import logging
import threading
from typing import Iterable

from .branches import FirmwareBranch
from .exceptions import (
    IllegalAddressError,
    InvalidValueError,
    ReadOnlyRegisterError,
    SmartPowerError,
)
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


class SmartPowerClient:
    """Modbus RTU client tailored to SmartPower firmware.

    Combine ``port`` + ``slave_id`` + ``branch`` to talk to a specific module.
    Every call is guarded by an internal lock — safe to share one client
    across threads, but a single port is half-duplex so only one transaction
    proceeds at a time.

    Use as a context manager::

        with SmartPowerClient("COM5", slave_id=1, branch=FirmwareBranch.SMARTPOWER_GEN_2_0) as c:
            print(c.read(Register.INPUT_REG_OUT_P))
    """

    def __init__(
        self,
        port: str,
        slave_id: int,
        branch: FirmwareBranch | str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        parity: str = "N",
        stopbits: int = 1,
        bytesize: int = 8,
        timeout: float = 1.0,
        retries: int = 2,
    ) -> None:
        if isinstance(branch, str):
            branch = FirmwareBranch.from_name(branch)
        if not 1 <= slave_id <= 247:
            raise InvalidValueError(
                f"slave_id must be 1..247 (Modbus RTU); got {slave_id}"
            )
        self.port = port
        self.slave_id = slave_id
        self.branch = branch
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

    # ----- lifecycle -----

    def connect(self) -> "SmartPowerClient":
        with self._lock:
            if not self._connected:
                self._transport.connect()
                self._connected = True
                logger.info(
                    "Connected to %s slave=%d platform=%s (firmware branch %s)",
                    self.port, self.slave_id, self.branch.name, self.branch.value,
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
        """Read a single register with branch + type validation.

        Returns ``bool`` for coil/discrete inputs and ``int`` for registers
        (interpreted as int16 if ``reg.signed`` is True).
        """
        assert_supported(reg, self.branch)
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
        """Write a single register with branch + type validation."""
        assert_supported(reg, self.branch)
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
        """Read several registers individually. Always-correct, never batches.

        Bulk-reading adjacent addresses with a single Modbus frame would be
        faster but risks crossing branch-specific gaps and is left to the
        caller to do explicitly via the low-level API.
        """
        out: dict[Register, int | bool] = {}
        for reg in regs:
            out[reg] = self.read(reg)
        return out

    def dump(self) -> dict[Register, int | bool]:
        """Read every register exposed by the configured firmware branch.

        Skips registers the slave rejects with an illegal-address response
        (rare, but possible when the firmware enum claims an address that
        the runtime hasn't wired up).
        """
        out: dict[Register, int | bool] = {}
        for reg in sorted(Register.for_branch(self.branch), key=lambda r: (r.kind.value, r.addr)):
            try:
                out[reg] = self.read(reg)
            except IllegalAddressError:
                logger.warning(
                    "Slave reports %s (0x%04X) as illegal — skipping",
                    reg.name, reg.addr,
                )
        return out

    # ----- branch probe ----------

    def probe_branch(self) -> tuple[FirmwareBranch, ...]:
        """Best-effort identification of the firmware branch.

        Reads the address that diverges across the four supported branches
        (``0x2021`` = ``INPUT_REG_THERMO_REG_LIMIT``) and returns the
        candidate platforms:

        - Read succeeds → platforms that expose extended thermo regulation:
          ``SMARTPOWER_SOLO`` and ``SMARTPOWER_GEN_1_5``.
        - Slave returns illegal-address → platforms that do not:
          ``SMARTPOWER_GEN_1_0`` and ``SMARTPOWER_GEN_2_0``.

        The branches inside each group are structurally identical at the
        Modbus layer (the only difference is a firmware-side spelling of
        ``ACIVE_PROFILE``/``ACTIVE_PROFILE``) so a single answer is not
        possible. A warning is logged if the result is inconsistent with
        the branch configured on the client.
        """
        probe_reg = Register.INPUT_REG_THERMO_REG_LIMIT
        try:
            with self._lock:
                self._transport.read_input(probe_reg.addr, 1)
            has_ext_thermo = True
        except IllegalAddressError:
            has_ext_thermo = False

        if has_ext_thermo:
            candidates = (
                FirmwareBranch.SMARTPOWER_SOLO,
                FirmwareBranch.SMARTPOWER_GEN_1_5,
            )
        else:
            candidates = (
                FirmwareBranch.SMARTPOWER_GEN_1_0,
                FirmwareBranch.SMARTPOWER_GEN_2_0,
            )
        if self.branch not in candidates:
            logger.warning(
                "Configured platform %s does not match probe result %s",
                self.branch.name, [b.name for b in candidates],
            )
        return candidates
