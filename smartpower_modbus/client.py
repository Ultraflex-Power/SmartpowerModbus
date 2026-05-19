"""High-level Modbus RTU client for SmartPower control boards."""

from __future__ import annotations

import logging
import math
import threading
import warnings
from collections.abc import Iterable

from ._transport import _Transport
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
from .units import (
    TemperatureUnit,
    is_temperature_unit,
    kelvin_from,
    kelvin_to,
)

logger = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 38400  # MODBUS::DEF_BAUD_RATE from ModBus.hpp


def interpret_raw(
    reg: Register,
    raw: int | bool,
    temperature_unit: TemperatureUnit,
) -> float | int | bool:
    """Apply ``reg``'s scaling and (for temperatures) the requested
    output unit to a raw value already read from the wire.

    Single source of truth shared by ``SmartPowerClient.read_value()``
    and the CLI ``dump --interpret`` path, which both need this
    transformation but on values that arrived through different paths
    (live read vs. a cached ``client.dump()`` result).
    """
    if isinstance(raw, bool):
        return raw
    # Apply the firmware-side scaling first.
    if reg.scale == 1.0 and not is_temperature_unit(reg.unit):
        value: float | int = raw  # keep int — no fractional component
    else:
        value = raw * reg.scale
    if is_temperature_unit(reg.unit):
        return kelvin_to(value, temperature_unit)
    return value


def _validate_int16(reg: Register, raw: int, *, physical_value=None) -> None:
    """Range-check ``raw`` against ``reg``'s declared signedness.

    When ``physical_value`` is provided (interpreted-write path) the error
    message also mentions the original physical input and the register's
    scale/unit — otherwise it stays terse (direct-write path).
    """
    if reg.signed:
        if not -0x8000 <= raw <= 0x7FFF:
            if physical_value is None:
                raise InvalidValueError(
                    f"{reg.name} is int16 — value {raw} out of range "
                    f"[-32768, 32767]"
                )
            raise InvalidValueError(
                f"Value {physical_value} (raw {raw}) out of range for {reg.name} "
                f"(int16, scale={reg.scale}, unit={reg.unit or 'raw'})"
            )
    else:
        if not 0 <= raw <= 0xFFFF:
            if physical_value is None:
                raise InvalidValueError(
                    f"{reg.name} is uint16 — value {raw} out of range "
                    f"[0, 65535]"
                )
            raise InvalidValueError(
                f"Value {physical_value} (raw {raw}) out of range for {reg.name} "
                f"(uint16, scale={reg.scale}, unit={reg.unit or 'raw'})"
            )


# Plausibility window for the tank-capacitor exponent. Real capacitors run
# from picofarad to ~Farad scale; clamping past that catches uninitialised
# memory / wrong-register reads while leaving plenty of headroom on either
# side. Used by both the read decoder (``_read_cap_pair``) and the write
# encoder (``_encode_capacitance``).
_CAP_EXP_MIN = -30
_CAP_EXP_MAX = 6


def _encode_capacitance(value_F: float) -> tuple[int, int]:
    """Encode a capacitance in Farads as a (val, exp) pair for the firmware's
    two-register storage: ``cap_F == val * 10**exp``, with ``val`` in
    ``[0, 65535]`` (uint16) and ``exp`` in ``[_CAP_EXP_MIN, _CAP_EXP_MAX]``.

    Chooses ``exp`` so that ``val`` lands in ``[6554, 65535]`` whenever
    possible — that maximises uint16 precision so a round-trip through
    :func:`_read_cap_pair` preserves the input to ~4 decimal digits.

    Raises :class:`InvalidValueError` for negative, ``nan``, ``inf``, or
    values that cannot be represented within the exponent window.
    """
    if not isinstance(value_F, (int, float)) or isinstance(value_F, bool):
        raise InvalidValueError(
            f"Capacitance must be a real number, got {type(value_F).__name__} {value_F!r}"
        )
    if not math.isfinite(value_F) or value_F < 0:
        raise InvalidValueError(
            f"Capacitance must be a non-negative finite number, got {value_F!r}"
        )
    if value_F == 0.0:
        return (0, 0)
    # exp = ceil(log10(value / uint16_max)) puts ``val`` in [6554, 65535]
    # except when rounding pushes val to 65536 — caught below.
    exp = math.ceil(math.log10(value_F / 0xFFFF))
    val = round(value_F / (10.0 ** exp))
    if val > 0xFFFF:
        exp += 1
        val = round(value_F / (10.0 ** exp))
    if not _CAP_EXP_MIN <= exp <= _CAP_EXP_MAX:
        raise InvalidValueError(
            f"Capacitance {value_F} F cannot be encoded with exponent in "
            f"[{_CAP_EXP_MIN}, {_CAP_EXP_MAX}] (got exp={exp})"
        )
    return (val, exp)


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
        retry_writes: bool = False,
        temperature_unit: TemperatureUnit | str = TemperatureUnit.CELSIUS,
        branch=None,
    ) -> None:
        # Accept a string like "C"/"K"/"F" for ergonomics; coerce to enum.
        if isinstance(temperature_unit, str):
            temperature_unit = TemperatureUnit.from_name(temperature_unit)
        self.temperature_unit: TemperatureUnit = temperature_unit
        # Backward-compat: accept the deprecated ``branch=`` kwarg.
        if model is not None and branch is not None:
            raise TypeError("Pass either `model=` or `branch=`, not both")
        if branch is not None:
            warnings.warn(
                "SmartPowerClient(branch=...) is deprecated; "
                "pass model=SmartPowerModel.<MODEL> instead.",
                DeprecationWarning, stacklevel=2,
            )
            model = branch
        # ``model=None`` means auto-identify the device on connect() via
        # Modbus FC 0x2B/0x0E (PRODUCT_CODE). The attribute is typed
        # ``SmartPowerModel | None`` until connect() resolves it.
        self.model: SmartPowerModel | None = (
            _coerce_model(model) if model is not None else None
        )

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
            retry_writes=retry_writes,
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
        return self._require_model().firmware_branch

    def _require_model(self) -> SmartPowerModel:
        """Return the resolved model or raise if it hasn't been set yet."""
        if self.model is None:
            raise SmartPowerError(
                "SmartPowerClient model is not set. Either pass model= to "
                "the constructor or call connect()/identify_model() so the "
                "device can be auto-identified via FC 0x2B (PRODUCT_CODE)."
            )
        return self.model

    # ----- lifecycle -----

    def connect(self) -> SmartPowerClient:
        with self._lock:
            if not self._connected:
                self._transport.connect()
                self._connected = True
        # Auto-identify the model if the user didn't pass one. Done outside
        # the lock because identify_model() acquires it itself. If
        # auto-identification raises (unknown PRODUCT_CODE, slave does
        # not support FC 0x2B, comm error, ...), the serial transport
        # must be closed before propagating — otherwise the port leaks,
        # and Python's ``with`` will not call __exit__ since __enter__
        # never returned.
        if self.model is None:
            logger.info(
                "No model configured — auto-identifying via FC 0x2B PRODUCT_CODE"
            )
            try:
                self.identify_model()
            except BaseException:
                with self._lock:
                    if self._connected:
                        self._transport.close()
                        self._connected = False
                raise
        logger.info(
            "Connected to %s slave=%d model=%s",
            self.port, self.slave_id, self._require_model().value,
        )
        return self

    def close(self) -> None:
        with self._lock:
            if self._connected:
                self._transport.close()
                self._connected = False
                logger.info("Closed connection to %s", self.port)

    def __enter__(self) -> SmartPowerClient:
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
        assert_supported(reg, self._require_model())
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
        assert_supported(reg, self._require_model())
        if not reg.is_writable:
            raise ReadOnlyRegisterError(
                f"{reg.name} is a {reg.kind.value}; not writable."
            )
        if reg.kind is RegisterKind.COIL:
            # Accept bool, or int 0/1 (legacy ergonomics). Reject everything
            # else — silently coercing e.g. ``42`` to ``True`` would let a
            # caller addressing the wrong register get a wrong-but-plausible
            # write rather than a clear error.
            if isinstance(value, bool):
                bit = value
            elif isinstance(value, int) and value in (0, 1):
                bit = bool(value)
            else:
                raise InvalidValueError(
                    f"Coil {reg.name} accepts bool or int 0/1, got "
                    f"{type(value).__name__} {value!r}"
                )
            with self._lock:
                self._transport.write_coil(reg.addr, bit)
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
        # Range-check according to the register's declared signedness.
        # unsigned16() accepts the whole [-32768, 65535] range — which
        # silently turns a stray -1 on an unsigned register into 0xFFFF.
        # Validate first so the caller sees a meaningful error.
        _validate_int16(reg, value)
        with self._lock:
            self._transport.write_holding(reg.addr, unsigned16(value))

    def read_many(self, regs: Iterable[Register]) -> dict[Register, int | bool]:
        """Read several registers individually."""
        out: dict[Register, int | bool] = {}
        for reg in regs:
            out[reg] = self.read(reg)
        return out

    # ----- interpreted read/write (scaling + temperature units) -----

    def read_value(
        self,
        reg: Register,
        *,
        temperature_unit: TemperatureUnit | str | None = None,
    ) -> float | int | bool:
        """Read a register and return its **interpreted** physical value.

        Applies the register's ``scale`` factor and, for temperatures
        (``unit == "K"``), converts to ``temperature_unit`` (defaults to
        the client's configured unit — Celsius unless overridden in the
        constructor).

        Returns:
            - ``bool`` for coils / discrete inputs (unchanged from ``read()``).
            - ``float`` for registers whose ``scale != 1.0`` or whose
              ``unit`` is a temperature.
            - ``int`` for registers with ``scale == 1.0`` and no unit
              (i.e. enum / bitmask / counter registers that don't scale).
        """
        raw = self.read(reg)
        if isinstance(temperature_unit, str):
            temperature_unit = TemperatureUnit.from_name(temperature_unit)
        unit_out = temperature_unit or self.temperature_unit
        return interpret_raw(reg, raw, unit_out)

    def write_value(
        self,
        reg: Register,
        value: float | int | bool,
        *,
        temperature_unit: TemperatureUnit | str | None = None,
    ) -> None:
        """Inverse of :meth:`read_value` — convert ``value`` to a raw
        uint16 (rounding to nearest) and write it.

        ``value`` is in the natural physical unit for the register:
            - Amps for currents, Volts for voltages, Watts for powers,
              Hz for frequency, % for setpoints, lps for flows, sec for
              timers, V·A for max-power limits.
            - For temperatures (``reg.unit == "K"``), ``value`` is in
              ``temperature_unit`` (or the client's configured unit if
              omitted) — Celsius by default.
            - For coils, ``value`` is bool.
            - For enum / bitmask / counter registers (``scale == 1.0``,
              no unit), ``value`` is the raw int.
        """
        if reg.kind is RegisterKind.COIL:
            # Narrow off float here so the call to write() type-checks; the
            # strict bool/int validation happens inside write() itself.
            if not isinstance(value, (bool, int)):
                raise InvalidValueError(
                    f"Coil {reg.name} accepts bool or int 0/1, got "
                    f"{type(value).__name__} {value!r}"
                )
            self.write(reg, value)
            return

        # Reject bool for non-coil registers up front. Without this check
        # ``int(True)`` would silently coerce to 1 and a caller intending
        # to set a coil but addressing a holding register would get a
        # wrong-but-plausible write.
        if isinstance(value, bool):
            raise InvalidValueError(
                f"{reg.name} is a {reg.kind.value}, not a coil — "
                f"refusing to silently coerce bool to int"
            )

        if isinstance(temperature_unit, str):
            temperature_unit = TemperatureUnit.from_name(temperature_unit)
        unit_in = temperature_unit or self.temperature_unit

        # Temperatures: caller's unit → Kelvin first.
        if is_temperature_unit(reg.unit):
            value = kelvin_from(float(value), unit_in)

        # Invert the firmware-side scaling. Always round half-to-even —
        # truncation would surprise the caller for setpoints near
        # integer boundaries (e.g. write_value(reg, 1.7) would store 1).
        raw = int(round(float(value) / reg.scale))

        # Range-check before delegating to write() so the user gets a
        # meaningful error message that mentions the physical value.
        _validate_int16(reg, raw, physical_value=value)

        self.write(reg, raw)

    # ----- composite: tank-capacitor value+exponent -----

    def _read_cap_pair(self, val_reg: Register, exp_reg: Register) -> float:
        """Atomic value+exponent read shared by ``read_capacitance`` and
        ``read_second_capacitance``. Reads ``val_reg`` and ``exp_reg`` in
        one Modbus transaction (they live at adjacent addresses), decodes
        the exponent as int16, and bounds-checks it.
        """
        model = self._require_model()
        assert_supported(val_reg, model)
        assert_supported(exp_reg, model)
        # val_reg and exp_reg are firmware-side adjacent (val first); enforce
        # so a refactor can't quietly desynchronise the pair.
        assert exp_reg.addr == val_reg.addr + 1, (
            f"{val_reg.name} and {exp_reg.name} must be at adjacent addresses"
        )
        with self._lock:
            raw = self._transport.read_holding(val_reg.addr, 2)
        val_raw = raw[0]
        exp_raw = signed16(raw[1])
        if not _CAP_EXP_MIN <= exp_raw <= _CAP_EXP_MAX:
            raise InvalidValueError(
                f"Capacitance exponent {exp_raw} from {exp_reg.name} "
                f"is out of plausible range [{_CAP_EXP_MIN}, {_CAP_EXP_MAX}]"
            )
        return val_raw * (10.0 ** exp_raw)

    def _write_cap_pair(
        self, val_reg: Register, exp_reg: Register, value: float
    ) -> None:
        """Encode ``value`` (Farads) and write the val+exp pair atomically
        via FC 0x10 (Write Multiple Registers). Shared by
        ``write_capacitance`` and ``write_second_capacitance``.
        """
        model = self._require_model()
        assert_supported(val_reg, model)
        assert_supported(exp_reg, model)
        assert exp_reg.addr == val_reg.addr + 1, (
            f"{val_reg.name} and {exp_reg.name} must be at adjacent addresses"
        )
        val, exp = _encode_capacitance(float(value))
        # ``exp`` is a signed int16 on the wire — convert to the uint16
        # wire form. unsigned16() also validates the range as a sanity guard.
        exp_wire = unsigned16(exp)
        with self._lock:
            self._transport.write_holdings(val_reg.addr, [val, exp_wire])

    def read_capacitance(self) -> float:
        """Read the equal-tank-capacitor value as a single float in Farads.

        The firmware stores capacitance as a value/exponent pair across
        ``HOLD_REG_CAP_VAL`` (``0x3008``) and ``HOLD_REG_CAP_EXP``
        (``0x3009``); the physical value is ``VAL * 10^EXP`` F (spec
        rev A7 — earlier revisions of the spec wrongly listed
        ``VAL/100 * 10^EXP``).

        Reads both registers in one Modbus transaction so a concurrent
        writer cannot tear the value/exponent pair, and validates the
        exponent against a sane range — a garbage firmware value of e.g.
        ``exp=400`` would silently overflow ``10.0 ** exp`` to ``inf``.
        """
        return self._read_cap_pair(
            Register.HOLD_REG_CAP_VAL, Register.HOLD_REG_CAP_EXP,
        )

    def read_second_capacitance(self) -> float:
        """Read the second equal-tank-capacitor value as a single float in Farads.

        Mirror of :meth:`read_capacitance` against the second value/exponent
        pair at ``HOLD_REG_SECOND_CAP_VAL`` (``0x3012``) and
        ``HOLD_REG_SECOND_CAP_EXP`` (``0x3013``). Same atomicity and
        bounds-check semantics.
        """
        return self._read_cap_pair(
            Register.HOLD_REG_SECOND_CAP_VAL, Register.HOLD_REG_SECOND_CAP_EXP,
        )

    def write_capacitance(self, value: float) -> None:
        """Write the equal-tank-capacitor value as a single float in Farads.

        Inverse of :meth:`read_capacitance`. Encodes ``value`` as a (val,
        exp) pair and writes both registers atomically via FC 0x10. The
        encoder maximises uint16 precision (mantissa lands in
        ``[6554, 65535]`` whenever possible) so a round-trip through
        :meth:`read_capacitance` preserves ``value`` to ~4 decimal digits.

        Raises :class:`InvalidValueError` for negative, ``nan``/``inf``,
        or out-of-range inputs (exponent outside ``[-30, 6]``).
        """
        self._write_cap_pair(
            Register.HOLD_REG_CAP_VAL, Register.HOLD_REG_CAP_EXP, value,
        )

    def write_second_capacitance(self, value: float) -> None:
        """Write the second equal-tank-capacitor value as a single float in Farads.

        Mirror of :meth:`write_capacitance` against the second value/exponent
        pair (``HOLD_REG_SECOND_CAP_VAL`` / ``HOLD_REG_SECOND_CAP_EXP``).
        """
        self._write_cap_pair(
            Register.HOLD_REG_SECOND_CAP_VAL,
            Register.HOLD_REG_SECOND_CAP_EXP,
            value,
        )

    def dump(self) -> dict[Register, int | bool]:
        """Read every register exposed by the configured model.

        Iteration order follows the protocol address layout (discrete
        inputs → coils → input regs → holding regs), which is how the
        spec groups them.
        """
        model = self._require_model()
        out: dict[Register, int | bool] = {}
        for reg in sorted(Register.for_model(model), key=lambda r: r.addr):
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
        if self.model is not None and self.model not in candidates:
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

    # ----- auto-recognition via FC 0x2B PRODUCT_CODE -----

    def read_device_info(self) -> dict[str, str]:
        """Read the standard Modbus device identification objects.

        Issues FC 0x2B / 0x0E (Read Device Identification) at ``read_code=0x01``
        (basic conformity level) which returns the three mandatory MEI
        objects:

        - ``"vendor"``   (object ID 0) — e.g. ``"Ultraflex Power"``
        - ``"product_code"`` (object ID 1) — e.g. ``"55370112"``
        - ``"revision"`` (object ID 2) — firmware revision string

        Returns a ``dict[str, str]`` keyed by friendly name.
        """
        with self._lock:
            raw = self._transport.read_device_information(read_code=0x01, object_id=0)
        return {
            "vendor":       raw.get(0, ""),
            "product_code": raw.get(1, ""),
            "revision":     raw.get(2, ""),
        }

    def read_product_code(self) -> str:
        """Read just the PRODUCT_CODE string the device reports.

        Issues FC 0x2B / 0x0E with ``read_code=0x01`` (basic conformity)
        and ``object_id=1`` (start at PRODUCT_CODE).

        Why basic and not specific (0x04)? The SmartPower firmware ships
        with the ``MEI_DEV_ONE_OBJ_ENA`` macro **disabled** (commented out
        in ``ModBus_Slave.cpp``), which means the slave only honours
        ``read_code == 0x01`` and replies to anything else with Modbus
        exception 0x02. The basic-conformity handler is fall-through:
        passing ``object_id=1`` makes it emit PRODUCT_CODE and REVISION
        (skipping vendor), which is the cheapest combination that gets
        us the product code while remaining compatible with every
        SmartPower firmware build.
        """
        with self._lock:
            raw = self._transport.read_device_information(read_code=0x01, object_id=1)
        code = raw.get(1)
        if code is None or code == "":
            raise SmartPowerError(
                "Device did not return a product code in response to FC 0x2B"
            )
        return code

    def identify_model(self) -> SmartPowerModel:
        """Auto-identify the SmartPower model by reading its PRODUCT_CODE.

        Issues FC 0x2B / 0x0E, looks the result up in the centralized
        product-code-to-model table, and **sets** ``self.model`` to the
        resolved value (so subsequent calls to ``read()`` / ``write()``
        work without further configuration).

        Raises:
            IllegalFunctionError: if the device does not support FC 0x2B.
            UnsupportedFirmwareBranchError: if the product code is not
                recognised. The raw code is included in the error message
                so it can be added to the mapping if it's a new model.
            ModbusCommError: on transport-level failures.
        """
        code = self.read_product_code()
        try:
            model = SmartPowerModel.from_product_code(code)
        except UnsupportedFirmwareBranchError as exc:
            logger.error(
                "Unrecognised PRODUCT_CODE from %s slave=%d: %r",
                self.port, self.slave_id, code,
            )
            raise UnsupportedFirmwareBranchError(
                f"Device reported PRODUCT_CODE {code!r}, which does not "
                f"match any known SmartPower model. {exc}"
            ) from exc

        # Compare-and-set under the lock so a concurrent caller cannot
        # observe a half-resolved state. CPython's GIL makes each access
        # atomic, but the read-decide-write triplet is not.
        with self._lock:
            if self.model is not None and self.model is not model:
                logger.warning(
                    "Configured model %s disagrees with device-reported model %s "
                    "(PRODUCT_CODE %r) — keeping configured value",
                    self.model.value, model.value, code,
                )
            else:
                self.model = model
                logger.info("Identified model: %s (PRODUCT_CODE %r)", model.value, code)
        return model
