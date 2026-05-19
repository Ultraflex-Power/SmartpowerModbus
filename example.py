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
    parser.add_argument("--sp-p", type=int, default=None,
                        help="If given, attempt to write this SP_P value")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Pass None to let the client auto-identify the model on connect.
    model = SmartPowerModel.from_name(args.model) if args.model else None

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
        ) as client:

            # 2) Print what the device reports about itself.
            info = client.read_device_info()
            print(
                f"Device: vendor={info['vendor']!r} "
                f"product_code={info['product_code']!r} "
                f"revision={info['revision']!r}"
            )
            print(f"Resolved model: {client.model.value}")

            # 3) Read several variables. Coils/discretes return bool;
            #    input/holding registers return int (signed if declared so).
            print("\nReading telemetry:")
            telemetry = [
                Register.INPUT_REG_OUT_P,
                Register.INPUT_REG_OUT_I,
                Register.INPUT_REG_OUT_V,
                Register.INPUT_REG_IN_COOLANT_T,   # signed int16 (degrees C)
                Register.INPUT_REG_OUT_COOLANT_T,  # signed int16
                Register.INPUT_FAULT,              # discrete input → bool
                Register.INPUT_READY,
            ]
            for reg in telemetry:
                try:
                    value = client.read(reg)
                except SmartPowerError as exc:
                    print(f"  {reg.name}: ERROR — {exc}")
                else:
                    print(f"  {reg.name:30s} = {value!r}")

            # 4) Write a variable safely.
            if args.sp_p is not None:
                fault = client.read(Register.INPUT_FAULT)
                if fault:
                    print("\nDevice reports FAULT — refusing to write SP_P.")
                else:
                    print(f"\nWriting HOLD_REG_SP_P = {args.sp_p}")
                    try:
                        client.write(Register.HOLD_REG_SP_P, args.sp_p)
                    except InvalidValueError as exc:
                        print(f"  rejected by library: {exc}")
                    else:
                        readback = client.read(Register.HOLD_REG_SP_P)
                        if readback == args.sp_p:
                            print(f"  readback OK: {readback}")
                        else:
                            print(f"  readback mismatch: wrote {args.sp_p}, read {readback}")

        # 5) Connection automatically closed by context manager exit.
        print("\nConnection closed.")
        return 0

    except SmartPowerError as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
