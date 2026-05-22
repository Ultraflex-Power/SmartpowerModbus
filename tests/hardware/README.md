# Live-hardware validation suite

Pytest tests that exercise the SmartPower Modbus client against a **real**
MOD-537-250 control board over RS-485. The default ``pytest -q`` run does
not touch these files; opting in requires explicit CLI flags.

See [`PLAN.md`](PLAN.md) for the design rationale and the per-file scope.

## Safety first

These tests connect to a live power supply. Before running them:

1. **De-energise the load.** The smoke test refuses to run if it sees
   ``INPUT_HEAT`` or ``INPUT_ENABLE`` high, but you must verify the
   physical interlocks yourself — the firmware bit only reflects the
   commanded state.
2. **Confirm the slave ID and baud rate** match the configured unit.
   A mismatched slave ID will only produce timeouts (handled), but a
   wrong baud rate can produce CRC errors that mask real bugs.
3. **Round-trip writes** restore the original register value in a
   ``try/finally``. If a test fails mid-sequence, **inspect the unit
   before re-running** — a crashed Python process won't run the
   ``finally`` block.
4. **Fault-injection** tests intentionally trigger Modbus exception
   responses. These are read-only on our supported firmwares; the
   ``--allow-fault-injection`` flag is mostly a "you understand the
   logs will be noisy" gate.

## Quick reference

```bash
# Default — fake-serial unit tests only. Hardware tests skip.
python -m pytest -q

# Smoke + read-only sweep on the connected board.
python -m pytest -q --hardware --port=/dev/ttyUSB0 --slave-id=1 \
    tests/hardware/test_smoke.py tests/hardware/test_register_sweep.py

# Round-trip safe writes (opt-in).
python -m pytest -q --hardware --allow-writes \
    --port=/dev/ttyUSB0 --slave-id=1 --model=SmartPowerGen_2.0 \
    tests/hardware/test_writes.py

# Fault injection + recovery.
python -m pytest -q --hardware --allow-fault-injection \
    --port=/dev/ttyUSB0 --slave-id=1 \
    tests/hardware/test_fault_recovery.py

# Full hardware run.
python -m pytest -q --hardware --allow-writes --allow-fault-injection \
    --port=/dev/ttyUSB0 --slave-id=1 \
    tests/hardware/
```

## CLI flags (declared in `tests/conftest.py`)

| Flag                       | Default | Notes |
|---------------------------|---------|-------|
| `--hardware`               | off     | Required for any test in this directory. |
| `--allow-writes`           | off     | Enables `@pytest.mark.hardware_write`. Implies `--hardware`. |
| `--allow-fault-injection`  | off     | Enables `@pytest.mark.hardware_fault`. Implies `--hardware`. |
| `--port=PORT`              | none    | Required when `--hardware` is set. |
| `--baud=N`                 | 38400   | Matches `smartpower_modbus.DEFAULT_BAUDRATE`. |
| `--slave-id=N`             | 1       | 1..247. |
| `--model=NAME`             | auto    | `SmartPowerSolo`, `SmartPowerGen_1.0`, `SmartPowerGen_1.5`, `SmartPowerGen_2.0`. Omit to auto-identify via FC 0x2B. |
| `--temperature-unit={C,K,F}` | C     | For interpreted reads of temperature registers. |
| `--hw-timeout=SEC`         | 1.0     | Modbus response timeout. |
| `--hw-retries=N`           | 0       | Transport read retry budget. Default 0 to test raw behaviour. |

## What gets tested

| File                          | Markers              | Scope |
|------------------------------|----------------------|-------|
| `test_smoke.py`              | `hardware`           | Connect, FC 0x2B identify, model probe, single read, safety guard. |
| `test_register_sweep.py`     | `hardware`           | Full `client.dump()`; type and range sanity on every reg. Run twice. |
| `test_writes.py`             | `hardware_write`     | Round-trip writes on allowlisted registers + capacitance pair. |
| `test_fault_recovery.py`     | `hardware_fault`     | Illegal-address, illegal-function, timeout, recovery. |

## Troubleshooting

- *Every test errors with "Could not open serial port"*: check that the
  user running pytest is in the `dialout` group (Linux) or has access
  to the COM port (Windows).
- *Smoke test fails on the safety guard*: the unit reports `HEAT` or
  `ENABLE` as on. Stop heating before running the suite.
- *Sweep reports `IllegalAddress` for many registers*: the wrong
  `--model` was selected (or the device is running a firmware variant
  this library doesn't model). Try omitting `--model` to auto-identify.
- *Write test "left in unexpected state"*: re-read the register
  manually via `smartpower-cli read <REG_NAME>` and restore. The
  `try/finally` ran but the value didn't match — either the firmware
  rounded our write or another process is writing concurrently.
