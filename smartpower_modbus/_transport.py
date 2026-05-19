"""Thin pymodbus wrapper. The only module in this package that imports pymodbus.

Public methods accept and return plain Python types (``int``, ``bool``,
``list[int]``, ``list[bool]``) and raise this library's own exception types
so the rest of the package never touches pymodbus directly.

Why this exists: pymodbus 3.x has shifted kwarg names (``unit`` → ``slave``
→ ``device_id``), response shapes, and exception classes across minor
releases. Pin the version range in pyproject.toml and isolate the churn here.
"""

from __future__ import annotations

import inspect
import logging
import time
from typing import Any

from .exceptions import (
    IllegalAddressError,
    IllegalFunctionError,
    IllegalValueError,
    ModbusCommError,
    ModbusCrcError,
    ModbusTimeoutError,
    SerialPortError,
    SlaveDeviceFailureError,
)

logger = logging.getLogger(__name__)


def _slave_kwarg(method: Any) -> str:
    """Return whichever of ``slave`` / ``device_id`` / ``unit`` this pymodbus
    method actually accepts. Pymodbus 3.7+ uses ``slave``; 3.8+ added
    ``device_id`` as the new canonical name. Older builds used ``unit``.
    """
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return "slave"
    for name in ("device_id", "slave", "unit"):
        if name in params:
            return name
    return "slave"


class _Transport:
    """Owns a ``ModbusSerialClient``, normalises calls, and translates errors."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        parity: str,
        stopbits: int,
        bytesize: int,
        timeout: float,
        slave_id: int,
        retries: int,
    ) -> None:
        # Imported lazily so a missing pymodbus surfaces only when a transport
        # is actually constructed (not at package import time).
        from pymodbus.client import ModbusSerialClient

        self._client = ModbusSerialClient(
            port=port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=bytesize,
            timeout=timeout,
        )
        self._slave_id = slave_id
        self._retries = max(0, int(retries))

    # ----- lifecycle -----

    def connect(self) -> None:
        try:
            ok = self._client.connect()
        except Exception as exc:
            raise SerialPortError(f"Failed to open serial port: {exc}") from exc
        if not ok:
            raise SerialPortError(
                f"Could not open serial port {self._client.comm_params.host!r}"
                if hasattr(self._client, "comm_params")
                else "Could not open serial port"
            )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover — close errors are non-fatal
            logger.debug("Error while closing serial port", exc_info=True)

    # ----- reads -----

    def read_holding(self, addr: int, count: int = 1) -> list[int]:
        return self._call(
            self._client.read_holding_registers,
            address=addr, count=count, result_attr="registers", retryable=True,
        )

    def read_input(self, addr: int, count: int = 1) -> list[int]:
        return self._call(
            self._client.read_input_registers,
            address=addr, count=count, result_attr="registers", retryable=True,
        )

    def read_coils(self, addr: int, count: int = 1) -> list[bool]:
        bits = self._call(
            self._client.read_coils,
            address=addr, count=count, result_attr="bits", retryable=True,
        )
        return [bool(b) for b in bits[:count]]

    def read_discretes(self, addr: int, count: int = 1) -> list[bool]:
        bits = self._call(
            self._client.read_discrete_inputs,
            address=addr, count=count, result_attr="bits", retryable=True,
        )
        return [bool(b) for b in bits[:count]]

    # ----- writes -----

    def write_holding(self, addr: int, value: int) -> None:
        self._call(
            self._client.write_register,
            address=addr, value=value & 0xFFFF, result_attr=None, retryable=True,
        )

    def write_holdings(self, addr: int, values: list[int]) -> None:
        self._call(
            self._client.write_registers,
            address=addr, values=[v & 0xFFFF for v in values],
            result_attr=None, retryable=True,
        )

    def write_coil(self, addr: int, value: bool) -> None:
        self._call(
            self._client.write_coil,
            address=addr, value=bool(value), result_attr=None, retryable=True,
        )

    def write_coils(self, addr: int, values: list[bool]) -> None:
        self._call(
            self._client.write_coils,
            address=addr, values=[bool(v) for v in values],
            result_attr=None, retryable=True,
        )

    # ----- Modbus FC 0x2B/0x0E: Read Device Identification -----

    def read_device_information(
        self,
        read_code: int = 0x04,
        object_id: int = 0,
    ) -> dict[int, str]:
        """Issue a Modbus FC 0x2B/0x0E Read Device Identification request.

        ``read_code``: 0x01 basic, 0x02 regular, 0x03 extended, 0x04
        specific object (default — returns just ``object_id``).

        Returns a ``dict[int, str]`` keyed by MEI object ID. The
        SmartPower firmware exposes:

        - 0: vendor name (``"Ultraflex Power"``)
        - 1: product code (``"55370112"`` etc.)
        - 2: revision (firmware version string)
        """
        from pymodbus.exceptions import ConnectionException, ModbusIOException
        try:
            response = self._client.read_device_information(
                read_code=read_code,
                object_id=object_id,
                slave=self._slave_id,
            )
        except ConnectionException as exc:
            raise SerialPortError(f"Serial connection lost: {exc}") from exc
        except ModbusIOException as exc:
            raise self._translate_io_error(exc) from exc

        if response is None:
            raise ModbusCommError("No response to Read Device Identification")
        exc_code = getattr(response, "exception_code", None)
        if exc_code is not None:
            self._raise_exception_response(response)
        if response.isError():
            raise self._translate_io_error(response)

        raw_info = getattr(response, "information", None)
        if raw_info is None:
            raise ModbusCommError(
                "Read Device Identification response missing 'information' field"
            )
        out: dict[int, str] = {}
        for oid, value in raw_info.items():
            if isinstance(value, (bytes, bytearray)):
                try:
                    out[int(oid)] = value.decode("ascii", errors="replace").rstrip("\x00").strip()
                except Exception:
                    out[int(oid)] = value.hex()
            else:
                out[int(oid)] = str(value)
        return out

    # ----- internals -----

    def _call(
        self,
        method: Any,
        *,
        address: int,
        result_attr: str | None,
        retryable: bool,
        count: int | None = None,
        value: Any = None,
        values: Any = None,
    ) -> Any:
        from pymodbus.exceptions import ConnectionException, ModbusIOException

        slave_kw = _slave_kwarg(method)
        kwargs: dict[str, Any] = {slave_kw: self._slave_id}
        if count is not None:
            kwargs["count"] = count
        if value is not None:
            kwargs["value"] = value
        if values is not None:
            kwargs["values"] = values

        attempts = self._retries + 1 if retryable else 1
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                logger.debug(
                    "TX %s addr=0x%04X kwargs=%s (attempt %d/%d)",
                    method.__name__, address, kwargs, attempt, attempts,
                )
                response = method(address, **kwargs)
            except ConnectionException as exc:
                raise SerialPortError(f"Serial connection lost: {exc}") from exc
            except ModbusIOException as exc:
                last_exc = self._translate_io_error(exc)
            except Exception as exc:
                last_exc = ModbusCommError(
                    f"Unexpected pymodbus error on {method.__name__}: {exc}"
                )
            else:
                logger.debug("RX %s response=%r", method.__name__, response)
                # Modbus exception responses carry an ``exception_code``
                # attribute. Duck-type on it rather than isinstance so the
                # shim survives the class moving around inside pymodbus.
                exc_code = getattr(response, "exception_code", None)
                if exc_code is not None:
                    # Deterministic Modbus error — never retry.
                    self._raise_exception_response(response)
                if response is None or response.isError():
                    last_exc = self._translate_io_error(response)
                else:
                    if result_attr is None:
                        return None
                    return getattr(response, result_attr)

            # If we got here, we have a retryable error in last_exc.
            if attempt < attempts:
                logger.warning(
                    "%s on attempt %d/%d, retrying: %s",
                    method.__name__, attempt, attempts, last_exc,
                )
                time.sleep(0.05)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _translate_io_error(exc: Any) -> ModbusCommError:
        text = str(exc).lower()
        if "timeout" in text or "no response" in text:
            return ModbusTimeoutError(f"Modbus timeout: {exc}")
        if "crc" in text or "checksum" in text:
            return ModbusCrcError(f"Modbus CRC/framing error: {exc}")
        return ModbusCommError(f"Modbus IO error: {exc}")

    @staticmethod
    def _raise_exception_response(response: Any) -> None:
        # pymodbus exposes the Modbus exception code as ``exception_code``.
        code = getattr(response, "exception_code", None)
        msg = f"Modbus exception response 0x{code:02X}" if code is not None else "Modbus exception response"
        if code == 0x01:
            raise IllegalFunctionError(msg)
        if code == 0x02:
            raise IllegalAddressError(msg)
        if code == 0x03:
            raise IllegalValueError(msg)
        if code == 0x04:
            raise SlaveDeviceFailureError(msg)
        raise ModbusCommError(msg)
