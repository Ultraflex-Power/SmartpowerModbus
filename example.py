"""Walkthrough: open a Modbus RTU connection to a SmartPower module,
read a handful of variables, write a setpoint safely, and close.

Run with::

    python example.py --port COM5 --slave 1 --model SmartPowerGen_2.0

``--model`` accepts the public SmartPower model name. Known values are
``SmartPowerSolo``, ``SmartPowerGen_1.0``, ``SmartPowerGen_1.5``,
``SmartPowerGen_2.0``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from smartpower_modbus import (
    InvalidValueError,
    Register,
    SmartPowerClient,
    SmartPowerError,
    SmartPowerModel,
    TemperatureUnit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Serial port (COM5 / /dev/ttyUSB0)")
    parser.add_argument("--slave", type=int, default=1, help="Modbus slave ID")
    parser.add_argument(
        "--model", default=None,
        help=(
            "Public SmartPower model name. If omitted, the client auto-detects "
            "the model on connect via Modbus FC 0x2B (PRODUCT_CODE)."
        ),
    )
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument(
        "--temperature-unit", choices=["C", "K", "F"], default="C",
        help="Display unit for temperature registers (default: C)",
    )
    parser.add_argument("--sp-p", type=float, default=None,
                        help="If given, write SP_P as a percentage (e.g. 50 = 50.00%%)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Pass None to let the client auto-identify the model on connect.
    model = SmartPowerModel.from_name(args.model) if args.model else None
    temp_unit = TemperatureUnit.from_name(args.temperature_unit)

    # 1) Open the connection. Context manager handles connect()/close().
    #    When model is None, connect() auto-identifies via FC 0x2B
    #    (PRODUCT_CODE) and sets client.model.
    try:
        with SmartPowerClient(
            port=args.port,
            slave_id=args.slave,
            model=model,
            baudrate=args.baud,
            timeout=1.0,
            retries=2,
            temperature_unit=temp_unit,
        ) as client:

            # 2) Print what the device reports about itself.
            info = client.read_device_info()
            print(
                f"Device: vendor={info['vendor']!r} "
                f"product_code={info['product_code']!r} "
                f"revision={info['revision']!r}"
            )
            print(f"Resolved model: {client.model.value}")

            # 3) Read telemetry. Two parallel passes — raw uint16 and the
            #    scaled / unit-converted interpretation — so the difference
            #    is visible side by side.
            telemetry = [
                Register.INPUT_REG_OUT_P,
                Register.INPUT_REG_OUT_I,
                Register.INPUT_REG_OUT_V,
                Register.INPUT_REG_FREQ,
                Register.INPUT_REG_IN_COOLANT_T,    # K x 10, shown in temp_unit
                Register.INPUT_REG_OUT_COOLANT_T,
                Register.INPUT_FAULT,               # discrete input
                Register.INPUT_READY,
            ]
            print("\nReading telemetry (raw uint16):")
            for reg in telemetry:
                try:
                    value = client.read(reg)
                except SmartPowerError as exc:
                    print(f"  {reg.name}: ERROR — {exc}")
                else:
                    print(f"  {reg.name:30s} = {value!r}")

            print(f"\nReading telemetry (interpreted; temperatures in {temp_unit.value}):")
            for reg in telemetry:
                try:
                    value = client.read_value(reg)
                except SmartPowerError as exc:
                    print(f"  {reg.name}: ERROR — {exc}")
                else:
                    suffix = reg.unit
                    if suffix == "K":
                        suffix = f"°{temp_unit.value}" if temp_unit is not TemperatureUnit.KELVIN else "K"
                    print(f"  {reg.name:30s} = {value} {suffix}".rstrip())

            # 4) Write a variable safely. write_value() applies the
            #    register's scale (0.01 %/lsb) so --sp-p is in percent.
            if args.sp_p is not None:
                fault = client.read(Register.INPUT_FAULT)
                if fault:
                    print("\nDevice reports FAULT — refusing to write SP_P.")
                else:
                    print(f"\nWriting HOLD_REG_SP_P = {args.sp_p} %")
                    try:
                        client.write_value(Register.HOLD_REG_SP_P, args.sp_p)
                    except InvalidValueError as exc:
                        print(f"  rejected by library: {exc}")
                    else:
                        readback = client.read_value(Register.HOLD_REG_SP_P)
                        # Tolerance: one raw lsb is 0.01 %; round-trip can lose
                        # at most half that to nearest-even rounding.
                        if abs(readback - args.sp_p) <= 0.005:
                            print(f"  readback OK: {readback} %")
                        else:
                            print(f"  readback mismatch: wrote {args.sp_p} %, read {readback} %")

        # 5) Connection automatically closed by context manager exit.
        print("\nConnection closed.")
        return 0

    except SmartPowerError as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
