"""Simple FC 0x02 -> FC 0x04 corruption probe.

Run with::

    python e_2_4_cmd.py --port COM5 --slave 1 --model SmartPowerGen_2.0

The script:

1. Connects to the device.
2. Reads FC 0x04 at 0x2000 for 10 registers and stores that baseline.
3. Reads FC 0x02 at 0x0000 for 16 discrete inputs and stores that snapshot.
4. Repeats FC 0x02 -> FC 0x04 until Ctrl+C.
5. Compares every FC 0x04 register against the baseline and prints any drift.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from smartpower_modbus import SmartPowerClient, SmartPowerError, SmartPowerModel, TemperatureUnit

FC02_ADDR = 0x0000
FC02_COUNT = 16
FC04_ADDR = 0x2000
FC04_COUNT = 10
LOOP_COUNT = 10


def _format_regs(values: list[int], start_addr: int) -> str:
    lines = []
    for index, value in enumerate(values):
        addr = start_addr + index
        lines.append(f"  0x{addr:04X} = 0x{value:04X} ({value})")
    return "\n".join(lines)


def _format_bits(values: list[bool], start_addr: int) -> str:
    lines = []
    for index, value in enumerate(values):
        addr = start_addr + index
        lines.append(f"  0x{addr:04X} = {int(value)}")
    return "\n".join(lines)


def _changed_registers(baseline: list[int], current: list[int]) -> list[tuple[int, int, int]]:
    changes: list[tuple[int, int, int]] = []
    for index, (before, now) in enumerate(zip(baseline, current, strict=True)):
        if before != now:
            changes.append((FC04_ADDR + index, before, now))
    return changes


def _changed_bits(baseline: list[bool], current: list[bool]) -> list[tuple[int, bool, bool]]:
    changes: list[tuple[int, bool, bool]] = []
    for index, (before, now) in enumerate(zip(baseline, current, strict=True)):
        if before != now:
            changes.append((FC02_ADDR + index, before, now))
    return changes


def _hex_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def _serial_endpoint(modbus_client: Any) -> Any:
    for attr in ("socket", "_serial", "serial", "transport"):
        endpoint = getattr(modbus_client, attr, None)
        if endpoint is not None and hasattr(endpoint, "read") and hasattr(endpoint, "write"):
            return endpoint
    raise RuntimeError("Could not locate the underlying serial endpoint for raw TX/RX logging")


def _install_serial_logger(modbus_client: Any, log_file: BinaryIO) -> None:
    endpoint = _serial_endpoint(modbus_client)
    if getattr(endpoint, "_sp_raw_logger_installed", False):
        return

    original_write = endpoint.write
    original_read = endpoint.read

    def logged_write(data: bytes) -> Any:
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        log_file.write(f"{timestamp} TX {len(data):3d} {_hex_bytes(bytes(data))}\n")
        log_file.flush()
        return original_write(data)

    def logged_read(*args: Any, **kwargs: Any) -> Any:
        data = original_read(*args, **kwargs)
        raw = bytes(data)
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        log_file.write(f"{timestamp} RX {len(raw):3d} {_hex_bytes(raw)}\n")
        log_file.flush()
        return data

    endpoint.write = logged_write
    endpoint.read = logged_read
    endpoint._sp_raw_logger_installed = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Serial port (COM5 / /dev/ttyUSB0)")
    parser.add_argument("--slave", type=int, default=1, help="Modbus slave ID")
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Public SmartPower model name. If omitted, the client auto-detects "
            "the model on connect via Modbus FC 0x2B (PRODUCT_CODE)."
        ),
    )
    parser.add_argument("--baud", type=int, default=38400, help="Serial baud rate")
    parser.add_argument("--timeout", type=float, default=1.0, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="Retry count")
    parser.add_argument(
        "--log-file",
        default="e_2_4_cmd_serial.log",
        help="ASCII text file that records raw serial TX/RX bytes in hex",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Optional delay in seconds between FC 0x02 and FC 0x04 inside the loop",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    model = SmartPowerModel.from_name(args.model) if args.model else None

    try:
        log_path = Path(args.log_file).resolve()
        with log_path.open("w", encoding="ascii") as log_file:
            log_file.write("# Raw serial TX/RX log for e_2_4_cmd.py\n")
            log_file.write(
                f"# started={datetime.now().isoformat(timespec='seconds')} "
                f"port={args.port} slave={args.slave} fc02={FC02_ADDR:#06x}/{FC02_COUNT} "
                f"fc04={FC04_ADDR:#06x}/{FC04_COUNT} loops={LOOP_COUNT}\n"
            )
            log_file.flush()

            with SmartPowerClient(
                port=args.port,
                slave_id=args.slave,
                model=model,
                baudrate=args.baud,
                timeout=args.timeout,
                retries=args.retries,
                temperature_unit=TemperatureUnit.CELSIUS,
            ) as client:
                _install_serial_logger(client._transport._client, log_file)

                info = client.read_device_info()
                print(
                    f"Device: vendor={info['vendor']!r} "
                    f"product_code={info['product_code']!r} "
                    f"revision={info['revision']!r}"
                )
                print(f"Resolved model: {client.model.value}")
                print(f"Raw serial log: {log_path}")

                baseline_regs = client._transport.read_input(FC04_ADDR, count=FC04_COUNT)
                print(f"\nBaseline FC 0x04: 0x{FC04_ADDR:04X} count={FC04_COUNT}")
                print(_format_regs(baseline_regs, FC04_ADDR))

                baseline_bits = client._transport.read_discretes(FC02_ADDR, count=FC02_COUNT)
                print(f"\nInitial FC 0x02: 0x{FC02_ADDR:04X} count={FC02_COUNT}")
                print(_format_bits(baseline_bits, FC02_ADDR))

                print(f"\nRunning {LOOP_COUNT} cycles of FC 0x02 -> FC 0x04.")
                for cycle in range(1, LOOP_COUNT + 1):
                    discretes = client._transport.read_discretes(FC02_ADDR, count=FC02_COUNT)
                    if args.sleep > 0:
                        time.sleep(args.sleep)
                    regs = client._transport.read_input(FC04_ADDR, count=FC04_COUNT)
                    bit_changes = _changed_bits(baseline_bits, discretes)
                    changes = _changed_registers(baseline_regs, regs)

                    if not bit_changes and not changes:
                        print(cycle, flush=True)
                        continue

                    print(f"\nCycle {cycle}: mismatch detected")
                    if bit_changes:
                        print("FC 0x02 bits changed from baseline:")
                        for addr, before, now in bit_changes:
                            print(f"  0x{addr:04X}: {int(before)} -> {int(now)}")
                    else:
                        print("FC 0x02 bits: no change from baseline")

                    print(f"FC 0x02 bits current: {[int(bit) for bit in discretes]}")
                    if not changes:
                        print("FC 0x04 registers: no change from baseline")
                        continue

                    print("FC 0x04 registers changed from baseline:")
                    for addr, before, now in changes:
                        print(f"  0x{addr:04X}: 0x{before:04X} ({before}) -> 0x{now:04X} ({now})")

    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    except SmartPowerError as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("\nConnection closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
