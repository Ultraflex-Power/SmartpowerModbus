# Hardware validation tests — implementation plan

Status: drafted 2026-05-22, branch `docs/claude-md-and-requirements`.
Owner: s.bonev@ultraflexpower.com.

## Goal

Validate the SmartPower Modbus client end-to-end against real hardware
(MOD-537-250 control board over RS-485) without disturbing the
existing FakeSerialClient-based unit tests that already run in CI.

Coverage targets:

1. Modbus request/response round-trip (FC 01/02/03/04/05/06/0F/10 and
   FC 0x2B/0x0E).
2. Register read/write, including the composite capacitance pair.
3. Error handling — Modbus exception responses 0x01/0x02/0x03/0x04
   surfaced as the right `SmartPowerError` subclass.
4. Timeout behaviour — `ModbusTimeoutError` on no-response, recovery on
   the next transaction.
5. CRC/framing — best-effort: cannot easily inject a CRC corruption from
   user-space pyserial, so the test will only *assert behaviour if* the
   driver reports one (e.g. flaky cabling); documented as "soft".
6. Communication recovery after faults — every fault-injection test
   verifies a normal read succeeds afterwards on the same client.
7. Real-serial behaviour across the four supported models
   (`SmartPowerSolo`, `Gen_1.0`, `Gen_1.5`, `Gen_2.0`) — the test suite
   takes the model as a CLI option, or auto-identifies via FC 0x2B.

Non-goals:

- Performance/throughput benchmarking.
- Galvanic / EMC / safety testing (handled by lab procedures, not pytest).
- Property-based fuzzing.

## Constraints

- Must not run by default. `pytest -q` keeps working unchanged; the
  hardware tests are skipped unless explicit opt-in flags are passed.
- Must not energise the load. Tests refuse to run if
  `INPUT_HEAT` or `INPUT_ENABLE` is observed high during smoke probe.
- Writes are off by default even with `--hardware`; a separate
  `--allow-writes` flag is required to opt in. Every write test
  round-trips through a `try/finally` that restores the original
  register value.
- Fault-injection writes are off by default with `--hardware`; a
  separate `--allow-fault-injection` flag is required. Fault injection
  is read-only (illegal-address / illegal-function probes), so the
  flag is mostly a "you understand this will log errors" gate.

## Gating

Pytest marker + opt-in flags, declared in `tests/conftest.py`:

| Flag                       | Purpose                                              | Default |
|---------------------------|------------------------------------------------------|---------|
| `--hardware`               | Enable any test marked `@pytest.mark.hardware`       | off     |
| `--port=PORT`              | Serial port (`/dev/ttyUSB0`, `COM5`)                 | required when `--hardware` |
| `--baud=N`                 | Baud rate                                            | 38400 (`DEFAULT_BAUDRATE`) |
| `--slave-id=N`             | Modbus slave id (1..247)                             | 1 |
| `--model=NAME`             | Public SmartPower model name; omit to auto-identify  | None |
| `--allow-writes`           | Enable tests marked `@pytest.mark.hardware_write`    | off |
| `--allow-fault-injection`  | Enable tests marked `@pytest.mark.hardware_fault`    | off |
| `--temperature-unit={C,K,F}` | Display unit for temperature reads                 | C |
| `--timeout=SEC`            | Modbus response timeout                              | 1.0 |
| `--retries=N`              | Transport-level read retry budget                    | 0 (test raw behaviour) |

`pytest_collection_modifyitems` adds `pytest.mark.skip` to any test that
carries `hardware*` markers when its required flag isn't set, with a
human-readable reason string. The marker `hardware_write` *implies*
`hardware`, and `hardware_fault` *implies* `hardware`.

The `hardware` marker is registered in `pyproject.toml` so pytest does
not warn about an unknown marker.

## Layout

```
tests/
├── conftest.py                      # add pytest_addoption + collection hook
└── hardware/
    ├── PLAN.md                      # this file
    ├── README.md                    # short how-to-run
    ├── __init__.py
    ├── conftest.py                  # fixtures (hw_client, hw_model, ...)
    ├── test_smoke.py                # connectivity + identify + probe
    ├── test_register_sweep.py       # read-only sweep of model registers
    ├── test_writes.py               # round-trip writes on allowlisted regs
    └── test_fault_recovery.py       # illegal-addr / illegal-fn / recovery
```

`tests/hardware/` is collected by pytest like any other tests dir
(`testpaths = ["tests"]` in `pyproject.toml`); the marker is the gate,
not the directory.

## Fixtures (in `tests/hardware/conftest.py`)

- `hw_port`, `hw_baud`, `hw_slave_id`, `hw_model`, `hw_temp_unit`,
  `hw_timeout`, `hw_retries`: thin wrappers over `pytestconfig.getoption`.
- `hw_client`: session-scoped `SmartPowerClient`, opened once and reused
  across tests. Closes on session teardown. Auto-identifies when
  `--model` is omitted.
- `hw_safety_check`: function-scoped autouse fixture (only inside
  `tests/hardware/`) that re-reads `INPUT_HEAT` and `INPUT_ENABLE`
  before each test and fails fast if either is on.

The fixtures are *only* defined under `tests/hardware/`, so the
non-hardware tests are unaffected.

## Test scope per file

### `test_smoke.py` (marker: `hardware`)

1. Port opens; `connect()` succeeds.
2. `read_device_info()` returns vendor / product_code / revision; all
   non-empty.
3. `read_product_code()` matches the configured model when one is set;
   otherwise becomes the auto-identified model.
4. `probe_model()` returns a candidate tuple that includes the
   identified model.
5. A single `read(Register.INPUT_REG_OUT_V)` (or any always-supported
   telemetry reg) succeeds and returns an int.
6. Safety guard: assert `INPUT_HEAT == False` and `INPUT_ENABLE == False`
   — fail loudly if not.

### `test_register_sweep.py` (marker: `hardware`)

1. `client.dump()` returns a result for every register exposed by the
   identified model (or skips with a warning for `IllegalAddressError`
   — which is what the existing `dump()` does).
2. For each `INPUT_REG`: the raw int falls inside `[-32768, 32767]`
   when `signed`, else `[0, 65535]`.
3. For each `DISCRETE_INPUT` / `COIL`: the value is a bool.
4. For temperature registers (`unit == "K"`): the interpreted value in
   Celsius is in a plausible -50..+200 °C window — warns rather than
   fails (a stopped/cold board can legitimately read out-of-range).
5. The sweep is repeated twice (back to back) to catch state-changing
   side-effects of a read. Both passes must report the same kind/shape
   for every register.

### `test_writes.py` (marker: `hardware_write`)

Allowlisted registers (chosen as non-actuating: setting them while
`COIL_HEAT=0` does not energise the load):

- `HOLD_REG_HS_RATIO` (uint16, scale 0.01) — heat-station transformer
  ratio. Pure configuration; effect is only visible while heating.
- `HOLD_REG_REQ_PROFILE` (uint16) — requested profile index; the
  firmware only acts on it after a profile-load command.
- `HOLD_REG_TIMER_SP` (int16, scale 0.1 s) — timer setpoint; only used
  while heat is active.

Write-pair (composite) round-trip:

- `write_capacitance` / `read_capacitance` and the `second_` variants
  — write a small perturbation around the read value, verify
  round-trip within 0.05 % (the encoder's documented precision floor),
  restore original.

Each test is structured:

```python
original = client.read(reg)
try:
    client.write(reg, original + delta)
    assert client.read(reg) == original + delta
finally:
    client.write(reg, original)
```

A test that fails mid-sequence still restores the original via the
`finally` block. If `--allow-writes` was passed against an
unsupported model (e.g. the register isn't on this firmware), the test
is auto-skipped via `pytest.skip` rather than failing.

### `test_fault_recovery.py` (marker: `hardware_fault`)

1. **IllegalAddress (0x02)**: pick an address known not to be in the
   firmware's `AppAddress_t` table (e.g. `0x3FFF`). Use the raw API
   (`read_holding`) so the client doesn't pre-reject. Assert
   `IllegalAddressError`. Then a normal `read(INPUT_REG_OUT_V)`
   succeeds — proving recovery.
2. **IllegalFunction (0x01)**: invoke `_transport.read_device_information`
   with `read_code=0x04`, which the SmartPower firmware does not
   honour (`MEI_DEV_ONE_OBJ_ENA` is disabled). Assert
   `IllegalFunctionError`. Verify recovery.
3. **Timeout**: temporarily lower `_transport._client.timeout` to
   ~0.001 s and read a known-supported register. Assert
   `ModbusTimeoutError`. Restore timeout. Verify recovery.
   - If the slave is fast enough to respond inside 1 ms (it usually
     isn't), the test is marked `xfail(strict=False)` so the suite
     doesn't fail on a too-quick happy path.
4. **CRC/framing**: documented as "soft" — only assertable if the
   underlying driver flags a CRC error during the run. We do not
   intentionally corrupt a frame (would require kernel/USB-level
   intervention). The test just records whether any
   `ModbusCrcError` was raised during sweep + writes and prints a
   note.
5. **Retry recovery on reads**: enable `retries=2` on a per-test
   client, set timeout very low, and confirm reads still surface
   `ModbusTimeoutError` after retries are exhausted (i.e. retries
   don't silently swallow a real fault).

Every step in this file re-runs a baseline read at the end (a function-
scoped fixture `verify_recovery`) so a failed recovery is captured as
a *separate* assertion rather than silently leaving the bus dirty.

## What we are deliberately not doing

- No mock-vs-real comparison harness. Existing `FakeSerialClient` tests
  already validate the parsing logic; the hardware tests target the
  *wire* and the *firmware*.
- No automatic restart of the device. If a write test leaves the unit
  in an unexpected state, it fails loudly — we don't try to power-cycle.
- No CI invocation. The hardware suite is a developer/QA tool. The
  README documents the exact command. CI continues to run only the
  fake-serial suite.

## Open questions / follow-ups

- Once we have a lab fixture with `socat` + a Modbus RTU emulator
  exposing two known PRODUCT_CODE strings, we can extend the suite to
  a parametrised matrix that runs the full thing against each emulated
  model. Until then, the operator picks the model at the CLI.
- If the team standardises on a single QA harness device per model,
  we can wire a marker (`@pytest.mark.requires_model("Gen_2.0")`) that
  auto-skips when the connected hardware doesn't match.

## How to run (cheat sheet)

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

# Full hardware run — everything except CI defaults.
python -m pytest -q --hardware --allow-writes --allow-fault-injection \
    --port=/dev/ttyUSB0 --slave-id=1 \
    tests/hardware/
```
