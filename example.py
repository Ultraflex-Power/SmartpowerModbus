"""Walkthrough: open a Modbus RTU connection to a SmartPower module,
read a handful of variables, write a setpoint safely, and close.

Run with::

    python example.py --port COM5 --slave 1 --branch SMARTPOWER_GEN_2_0

The ``--branch`` argument accepts either the platform identifier
(``SMARTPOWER_GEN_2_0``) or the firmware-repo branch string (``MegaMain``).
"""

from __future__ import annotations

import argparse
import logging
import sys

from smartpower_modbus import (
    FirmwareBranch,
    InvalidValueError,
    Register,
    SmartPowerClient,
    SmartPowerError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Serial port (COM5 / /dev/ttyUSB0)")
    parser.add_argument("--slave", type=int, default=1, help="Modbus slave ID")
    parser.add_argument(
        "--branch", default="SMARTPOWER_GEN_2_0",
        help=(
            "Platform identifier (e.g. SMARTPOWER_GEN_2_0) or firmware "
            "branch string (e.g. MegaMain). Default: SMARTPOWER_GEN_2_0."
        ),
    )
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument("--sp-p", type=int, default=None,
                        help="If given, attempt to write this SP_P value")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    branch = FirmwareBranch.from_name(args.branch)

    # 1) Open the connection. Context manager handles connect()/close().
    try:
        with SmartPowerClient(
            port=args.port,
            slave_id=args.slave,
            branch=branch,
            baudrate=args.baud,
            timeout=1.0,
            retries=2,
        ) as client:

            # 2) Platform is already configured on the client. Quick probe
            #    to confirm the device matches what we declared.
            print(f"Probing platform (configured = {branch.name} / {branch.value}) ...")
            candidates = client.probe_branch()
            print(f"  device looks like: {[b.name for b in candidates]}")

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
