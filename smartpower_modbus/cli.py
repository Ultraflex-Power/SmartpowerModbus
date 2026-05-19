"""``smartpower-cli`` — read, write, dump, and probe a SmartPower module.

``--model`` accepts a public SmartPower model name (``SmartPowerGen_2.0``,
``SmartPowerSolo``, etc.). The deprecated ``--branch`` flag still works
and accepts the same value (a deprecation warning is printed).

Examples::

    smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 read OUT_P OUT_I OUT_V
    smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 write HOLD_REG_SP_P 50
    smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 dump
    smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 probe
    smartpower-cli --model SmartPowerGen_2.0 list-registers
    smartpower-cli list-models
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from typing import Sequence

from .client import DEFAULT_BAUDRATE, SmartPowerClient
from .exceptions import SmartPowerError
from .models import SmartPowerModel
from .registers import Register, RegisterKind


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smartpower-cli", description=__doc__.split("\n\n")[0])
    p.add_argument("--port", help="Serial port (e.g. COM5, /dev/ttyUSB0). Required for read/write/dump/probe.")
    p.add_argument("--slave", type=int, help="Modbus slave ID (1..247). Required for read/write/dump/probe.")
    p.add_argument(
        "--model",
        help=(
            "Public SmartPower model name (SmartPowerSolo, SmartPowerGen_1.0, "
            "SmartPowerGen_1.5, SmartPowerGen_2.0). Required for everything "
            "except list-models / list-branches."
        ),
    )
    # Deprecated alias retained for back-compat; emits a DeprecationWarning.
    p.add_argument(
        "--branch", dest="branch_deprecated",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE, help=f"Baud rate (default {DEFAULT_BAUDRATE})")
    p.add_argument("--timeout", type=float, default=1.0, help="Response timeout in seconds")
    p.add_argument("--retries", type=int, default=2, help="Retries on transient errors")
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v: INFO, -vv: DEBUG")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp_read = sub.add_parser("read", help="Read one or more registers by name")
    sp_read.add_argument("names", nargs="+", help="Register names (canonical or legacy)")

    sp_write = sub.add_parser("write", help="Write a single register")
    sp_write.add_argument("name", help="Register name")
    sp_write.add_argument("value", help="Integer for holding regs, 0/1/true/false for coils")

    sub.add_parser("dump", help="Read every register valid on the selected model")
    sub.add_parser("probe", help="Identify the model by probing diverging addresses")
    sub.add_parser("list-registers", help="List all registers valid on the selected model")
    sub.add_parser("list-models", help="List all known SmartPower models")
    # Deprecated alias.
    sub.add_parser("list-branches", help=argparse.SUPPRESS)

    return p


def _parse_value(reg: Register, raw: str) -> int | bool:
    if reg.kind is RegisterKind.COIL:
        lo = raw.strip().lower()
        if lo in ("1", "true", "on", "yes"):
            return True
        if lo in ("0", "false", "off", "no"):
            return False
        raise SystemExit(f"Cannot interpret {raw!r} as a coil value (use 0/1/true/false)")
    try:
        if raw.lower().startswith("0x"):
            return int(raw, 16)
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"Cannot interpret {raw!r} as an integer: {exc}")


def _format(value: int | bool, reg: Register) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if reg.signed:
        return f"{value} (0x{value & 0xFFFF:04X})"
    return f"{value} (0x{value:04X})"


def _resolve_model_arg(args) -> SmartPowerModel | None:
    """Return the SmartPowerModel from --model or the deprecated --branch."""
    if args.model and args.branch_deprecated:
        print("error: pass either --model or --branch, not both", file=sys.stderr)
        raise SystemExit(2)
    if args.branch_deprecated:
        warnings.warn(
            "--branch is deprecated; use --model SmartPowerGen_<N.N> instead.",
            DeprecationWarning, stacklevel=2,
        )
        return SmartPowerModel.from_name(args.branch_deprecated)
    if args.model:
        return SmartPowerModel.from_name(args.model)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    # Show deprecation warnings to the user by default.
    warnings.filterwarnings("default", category=DeprecationWarning, module=r"smartpower_modbus(\..*)?")

    args = _build_parser().parse_args(argv)

    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    elif args.verbose >= 1:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "list-models":
        for m in SmartPowerModel:
            print(f"{m.value:22s}  (firmware branch: {m.firmware_branch.value})")
        return 0
    if args.cmd == "list-branches":
        warnings.warn(
            "`list-branches` is deprecated; use `list-models`.",
            DeprecationWarning, stacklevel=2,
        )
        for m in SmartPowerModel:
            print(f"{m.value:22s}  (firmware branch: {m.firmware_branch.value})")
        return 0

    try:
        model = _resolve_model_arg(args)
    except SmartPowerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if model is None:
        print("error: --model is required for this command", file=sys.stderr)
        return 2

    if args.cmd == "list-registers":
        for reg in sorted(Register.for_model(model), key=lambda r: (r.kind.value, r.addr)):
            print(f"0x{reg.addr:04X}  {reg.kind.value:14s}  {reg.name}")
        return 0

    if args.port is None or args.slave is None:
        print("error: --port and --slave are required for this command", file=sys.stderr)
        return 2

    try:
        with SmartPowerClient(
            port=args.port,
            slave_id=args.slave,
            model=model,
            baudrate=args.baud,
            timeout=args.timeout,
            retries=args.retries,
        ) as client:
            if args.cmd == "read":
                for name in args.names:
                    reg = Register.from_name(name)
                    value = client.read(reg)
                    print(f"{reg.name:34s} 0x{reg.addr:04X}  =  {_format(value, reg)}")
            elif args.cmd == "write":
                reg = Register.from_name(args.name)
                value = _parse_value(reg, args.value)
                client.write(reg, value)
                readback = client.read(reg)
                print(f"wrote {reg.name} = {value!r}; read back {_format(readback, reg)}")
            elif args.cmd == "dump":
                for reg, value in client.dump().items():
                    print(f"{reg.name:34s} 0x{reg.addr:04X}  =  {_format(value, reg)}")
            elif args.cmd == "probe":
                candidates = client.probe_model()
                print("Detected model candidates:")
                for m in candidates:
                    marker = " <- configured" if m is model else ""
                    print(f"  {m.value:22s}  (firmware branch: {m.firmware_branch.value}){marker}")
    except SmartPowerError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
