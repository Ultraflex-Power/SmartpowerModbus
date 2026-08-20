# FC 0x02 → FC 0x04 corruption reproducer + multi-model hardware workflow

Status: drafted 2026-05-22.
Owner: s.bonev@ultraflexpower.com.
Branch: `test/hardware-validation-suite`.
Companion to: [`PLAN.md`](PLAN.md).

## Background

A customer-reported firmware defect: a single FC 0x02 (Read Discrete
Inputs) request at `0x0000`/16 bits corrupts the data returned by the
*next* FC 0x04 (Read Input Registers) request covering `0x2000`–`0x2007`.
After the FC 0x02, the four targets

| Symbolic name              | Library register (`registers.py`)        | Addr   | Corrupted value |
|----------------------------|------------------------------------------|--------|-----------------|
| `resetId`                  | `INPUT_REG_RESET`                        | 0x2001 | `1`             |
| `paEnableMask`             | `INPUT_REG_PA_ENABLE_MASK`               | 0x2004 | `0`             |
| `paMaxWorkingSet`          | `INPUT_REG_PA_MAX_WORK_SET`              | 0x2005 | `0`             |
| `fullScaleOutputPowerRaw`  | `INPUT_REG_OUT_100_P`                    | 0x2006 | `0`             |

read back at fixed wrong values. The immediate neighbour
`fullScaleOutputCurrentRaw` → `INPUT_REG_OUT_100_I` (`0x2007`) reads
correctly. Polling FC 0x04 alone is clean. Polling FC 0x02 alone is
benign. Inter-frame idle up to 1.5 s does not clear the state — only the
*next* FC 0x04 does. Zero CRC errors, zero exception responses, FC 0x08
diagnostic counters all zero, single master on the line.

All five touched registers (`0x2000`–`0x2008`) and the discrete input
range starting at `0x0000` use the default `_ALL` branch set in
`registers.py`, so all four supported models expose them — every device
is in scope.

## Goal

Two scopes in one document:

1. A surgical, opt-in pytest reproducer that proves the bug exists (and
   therefore fails CI / a firmware regression run when present), and
   logs an actionable signature when it does.
2. An operator workflow that runs the reproducer — plus the existing
   smoke + sweep tests — once per device for each of the four supported
   models, aggregating results into per-model artefacts the firmware
   team can collect from four separate runs.

Non-goals:

- Root-causing the firmware bug. We only reproduce, characterise the
  persistence window, and prove scope across branches.
- Multi-drop / bus-arbitration testing. The bench fixture stays
  single-drop; the plan documents how to rotate four devices through it.
- Fuzzing the FC 0x02 → FC 0x04 transition with random address/count
  combinations. The reproducer pins exactly the customer's reported
  request shape; a follow-up fuzz suite is out of scope.
- Property-based / hypothesis-style tests on the rest of the FC 0x02
  surface. Other discrete-input ranges may or may not trigger the same
  defect; that goes in a follow-up once firmware confirms the mechanism.

## Scope 1 — the reproducer

### New file

`tests/hardware/test_fc02_fc04_corruption.py`.

Sibling to `test_fault_recovery.py` so a reader scanning the directory
sees "fault" tests grouped together. The file name encodes the FC pair
under test so a future "FC 0x05 → FC 0x03 corruption" defect lands at
`test_fc05_fc03_corruption.py` and the pattern is obvious.

### Marker

Introduce a new marker:

```python
@pytest.mark.hardware_firmware_bug
```

Reasoning: this test is structurally different from the existing three
markers.

- `hardware` is the read-only / always-safe tier. The reproducer *is*
  read-only on the wire (no writes), but its expected outcome on
  affected firmware is a hard fail. Bundling it under plain `hardware`
  means a green-field operator running the smoke + sweep suite gets a
  surprise failure they did not opt into.
- `hardware_fault` is for tests that *deliberately* provoke an exception
  response and then assert recovery. The corruption bug emits *no*
  exception (zero CRC errors, zero NAKs); it silently mutates a
  subsequent response. Semantically different.

A dedicated marker also lets the operator opt-in explicitly when running
a firmware regression sweep ("are we still affected on branch X?")
without entangling that decision with `--allow-fault-injection`.

Plumbing (mirrors the existing markers):

| Where                          | Change |
|--------------------------------|--------|
| `pyproject.toml` markers list  | register `hardware_firmware_bug` so pytest doesn't warn. |
| `tests/conftest.py`            | add `--allow-firmware-bug-tests` flag (default off). Implies `--hardware`. |
| `tests/conftest.py`            | extend `pytest_collection_modifyitems` to auto-skip the marker when the flag is missing, with a clear reason string. |
| `tests/hardware/README.md`     | document the new flag + marker in the existing table. |

### Transport tier

Use the **raw transport API** directly:
`hw_client._transport.read_input(0x2000, count=9)` and
`hw_client._transport.read_discretes(0x0000, count=16)`.

Justification:

- We need byte-for-byte control of the FC code and the count field.
  Going through `client.read_many` would batch / split based on register
  metadata and could re-order or coalesce reads in ways that mask the
  defect.
- The customer's reproducer reads exactly `0x2000`/9 over FC 0x04 and
  `0x0000`/16 over FC 0x02. The transport's `read_input` /
  `read_discretes` map 1:1 to those PDUs.
- `assert_supported` would otherwise reject some addresses on some
  models depending on register metadata; for the four affected addresses
  this is not currently a concern (they are `_ALL`), but using raw keeps
  the test resilient to future branch-restriction edits.
- Retries must be **off** for this test (`hw_retries=0`), otherwise a
  transient comm error could mask or duplicate the corruption symptom.
  The default `--hw-retries=0` is already correct.

### Test structure

```python
TARGETS = {
    0x2001: "INPUT_REG_RESET",
    0x2004: "INPUT_REG_PA_ENABLE_MASK",
    0x2005: "INPUT_REG_PA_MAX_WORK_SET",
    0x2006: "INPUT_REG_OUT_100_P",
}
UNAFFECTED_NEIGHBOUR = (0x2007, "INPUT_REG_OUT_100_I")
CORRUPTION_SIGNATURE = {0x2001: 1, 0x2004: 0, 0x2005: 0, 0x2006: 0}
```

Procedure for each parametrised case:

1. `baseline = hw_client._transport.read_input(0x2000, count=9)`
2. `hw_client._transport.read_discretes(0x0000, count=16)`
3. `time.sleep(delay_ms / 1000.0)` (parametrised; see below)
4. `after = hw_client._transport.read_input(0x2000, count=9)`
5. Compare `baseline` vs `after` on the four target offsets and on the
   `0x2007` neighbour.

Pass/fail policy: **assert the bug is gone** (option (a) from the
brief). The test fails loudly on affected firmware. Rationale:

- The whole purpose is to be the regression gate after the firmware
  team ships a fix. A "skip with warning" outcome would silently
  re-pass after the fix, defeating the gate.
- The marker `hardware_firmware_bug` + opt-in flag already make this an
  explicit, deliberate run. CI doesn't see it. The cost of a loud
  failure is exactly zero for any non-affected unit.
- Operators running firmware regression need a single exit code to
  diff against. "Skip" doesn't give that.

The failure message embeds the corruption signature so the firmware
team gets actionable output without re-running with `-v`:

```
AssertionError: FC 0x02 → FC 0x04 corruption reproduced on
SmartPowerGen_2.0 (PRODUCT_CODE=55370112, firmware=1.0.0).
After one FC 0x02 read of 0x0000/16:
  0x2001 INPUT_REG_RESET           : 0x0042 -> 0x0001  (expected unchanged)
  0x2004 INPUT_REG_PA_ENABLE_MASK  : 0x0007 -> 0x0000  (expected unchanged)
  0x2005 INPUT_REG_PA_MAX_WORK_SET : 0x0003 -> 0x0000  (expected unchanged)
  0x2006 INPUT_REG_OUT_100_P       : 0x07D0 -> 0x0000  (expected unchanged)
Unaffected neighbour:
  0x2007 INPUT_REG_OUT_100_I       : 0x01F4 -> 0x01F4  (ok)
Inter-frame idle: 250 ms. Repetition: 3/5.
```

### Parametrisation

`@pytest.mark.parametrize("delay_ms", [0, 50, 250, 1500])` — exercises
the persistence claim that idle time alone does not clear the corrupt
state. A separate test issues five back-to-back repetitions
(`@pytest.mark.parametrize("rep", range(5))`) at `delay_ms=0` so we have
evidence the symptom is deterministic and not a one-shot.

A third test reads `0x2000`–`0x2008` *twice* with no FC 0x02 in between
(control case) and asserts no drift on the four targets. Failing this
test on a unit that *also* fails the FC 0x02 sequence would mean the
corruption is not actually caused by FC 0x02 — useful diagnostic.

### Per-test fixture

`fc02_corruption_baseline`: session-scoped, captures one clean FC 0x04
sweep before any FC 0x02 has been issued. The reproducer compares
against this so we have a known-good reference even if a previous test
in the session put the slave into the corrupted state. (Single-master,
single-client, but defensive.)

### Safety

The existing `_hw_safety_guard` autouse fixture already covers this
file because the marker name starts with `hardware`-anchor. Add
`"hardware_firmware_bug"` to the marker tuple inside the guard so it
fires. No coil writes, no holding-register writes — purely read-only on
the wire.

### Files added / changed for Scope 1

| Path                                          | Change |
|-----------------------------------------------|--------|
| `tests/hardware/test_fc02_fc04_corruption.py` | new — the reproducer. |
| `tests/conftest.py`                           | add `--allow-firmware-bug-tests`, extend skip plumbing. |
| `tests/hardware/conftest.py`                  | add `"hardware_firmware_bug"` to `_hw_safety_guard`'s marker tuple; add `fc02_corruption_baseline` fixture. |
| `pyproject.toml`                              | register `hardware_firmware_bug` marker. |
| `tests/hardware/README.md`                    | new flag + new file row in the table. |

## Scope 2 — running the suite against all four models

### Problem

The bench fixture is single-drop. The operator runs the suite once per
device. We need to (a) make the operator workflow obvious and (b)
aggregate four runs into one report so the firmware team can scope the
defect (which branches are affected, which are not).

### Approach: per-run JSON artefact, no test-side parametrisation

The existing `--model` flag already lets the operator pick the model
per invocation. We add a `--results-dir=PATH` flag that, when set,
causes the hardware suite to write a single JSON file per run with the
resolved model in the filename:

```
tests/hardware/results/<model>-<yyyymmddTHHMMSS>.json
```

Default: `--results-dir` unset → no files written, current behaviour
preserved. A `tests/hardware/results/` directory is added to
`.gitignore` and is created on demand by the writer fixture.

A separate top-level marker like `@pytest.mark.requires_model("...")` is
**rejected for now**. Reasoning:

- The hardware suite is run one device at a time; the operator already
  picks the model via `--model`. A `requires_model` marker only earns
  its keep if a single run can target multiple devices, which our
  fixture does not.
- The open question in `PLAN.md` envisaged a parametrised-matrix run
  against a `socat` + emulator setup. That's still the right home for
  `requires_model`; not this PR.
- For the FC 0x02/0x04 reproducer specifically, all four models expose
  every register involved, so there is nothing to skip per-model.

If a future test needs per-model gating, add `requires_model` then.

### What the results file contains

```json
{
  "run": {
    "started_at": "2026-05-22T14:03:11Z",
    "ended_at":   "2026-05-22T14:04:48Z",
    "port": "/dev/ttyUSB0",
    "slave_id": 1,
    "baud": 38400,
    "timeout_s": 1.0,
    "retries": 0,
    "git_sha": "<HEAD sha at run time>"
  },
  "device": {
    "model": "SmartPowerGen_2.0",
    "product_code": "55370112",
    "vendor": "Ultraflex Power",
    "revision": "1.0.0"
  },
  "tests": [
    {
      "nodeid": "tests/hardware/test_fc02_fc04_corruption.py::test_fc02_then_fc04_corrupts_targets[delay_ms=0]",
      "outcome": "failed",
      "duration_s": 0.41,
      "signature": {
        "delay_ms": 0,
        "baseline":  {"0x2001": 66, "0x2004": 7, "0x2005": 3, "0x2006": 2000, "0x2007": 500},
        "after":     {"0x2001": 1,  "0x2004": 0, "0x2005": 0, "0x2006": 0,    "0x2007": 500},
        "targets_changed": ["0x2001", "0x2004", "0x2005", "0x2006"],
        "neighbour_unchanged": "0x2007"
      }
    }
  ]
}
```

The `signature` payload is populated only by the corruption test, via a
helper that stashes a `dict` on the pytest `request.node.user_properties`
list. A pytest hook (`pytest_runtest_makereport`) reads
`user_properties` and merges them into the per-node JSON entry. Other
tests record just `nodeid` / `outcome` / `duration_s`.

### Implementation

| Path                                | Change |
|-------------------------------------|--------|
| `tests/conftest.py`                 | add `--results-dir` flag; add a `pytest_runtest_makereport` hook that appends to an in-memory list when the flag is set; add a `pytest_sessionfinish` hook that flushes the list + run metadata to JSON. Hook is a no-op when `--results-dir` is unset, so the fake-serial unit tests in CI are not slowed. |
| `tests/hardware/conftest.py`        | new `record_corruption_signature(...)` helper bound to the active node's `user_properties`. Imported by the new reproducer file. |
| `tests/hardware/results/.gitignore` | new — ignore everything in this directory except `.gitignore` itself. |
| `tests/hardware/README.md`          | new "Running across all four models" section: the loop below. |
| `RUNBOOK.md` (new, repo root)       | top-level operator runbook: prerequisites, four-device rotation, where to send the four JSON files when done. Cross-link from `README.md` and from `tests/hardware/README.md`. |

### Operator workflow

The operator wires up each device in turn and runs:

```bash
for MODEL in SmartPowerSolo SmartPowerGen_1.0 SmartPowerGen_1.5 SmartPowerGen_2.0; do
  echo "=== Connect ${MODEL} now, then press Enter ==="
  read
  python -m pytest -q \
      --hardware --allow-firmware-bug-tests \
      --port=/dev/ttyUSB0 --slave-id=1 --model=${MODEL} \
      --results-dir=tests/hardware/results \
      tests/hardware/test_smoke.py \
      tests/hardware/test_register_sweep.py \
      tests/hardware/test_fc02_fc04_corruption.py \
      || true   # keep going so we get artefacts for every model
done
```

Exit code is allowed to be non-zero per iteration: that's the *point*
of the regression gate. The `RUNBOOK.md` documents this loop and the
"collect the four JSON files and attach to the firmware ticket" handoff.

### Why not a single multi-model pytest run

`hw_client` is session-scoped and opens the port at session start. A
parametrised run that re-opens the port per model would (a) need to
tear down and rebuild the client mid-session, (b) require the operator
to physically swap devices mid-pytest with no obvious prompt, and (c)
defeat the autouse safety guard's "fail fast at session start"
property. Four invocations of pytest, one per device, is the cleaner
shape.

## Open questions / follow-ups

- A pytest plugin entry point (`smartpower_modbus_pytest`) could expose
  the `--results-dir` plumbing for downstream consumers (UltraFlex
  integration test rigs). Out of scope for this PR.
- Once firmware ships a candidate fix, add a smoke-only run on the
  *fixed* branch to confirm the assertion now passes — and at that
  point, consider promoting the test to plain `@pytest.mark.hardware`
  so any future regression is caught automatically by the read-only
  suite.
- `requires_model` marker is worth reviving when we have a `socat` +
  emulator harness that can target all four PRODUCT_CODE strings in a
  single pytest run.
- Investigate whether the same FC 0x02 → FC 0x04 corruption signature
  appears for *other* FC 0x02 ranges (e.g. count=8 instead of 16, or
  starting at `0x0008`). Out of scope for this PR; the firmware team
  may surface a wider pattern.

## How to run (cheat sheet)

```bash
# Default — unchanged. Hardware + firmware-bug tests skip.
python -m pytest -q

# Just the corruption reproducer against the connected unit.
python -m pytest -q \
    --hardware --allow-firmware-bug-tests \
    --port=/dev/ttyUSB0 --slave-id=1 --model=SmartPowerGen_2.0 \
    tests/hardware/test_fc02_fc04_corruption.py

# Same, capturing the JSON artefact under tests/hardware/results/.
python -m pytest -q \
    --hardware --allow-firmware-bug-tests \
    --port=/dev/ttyUSB0 --slave-id=1 --model=SmartPowerGen_2.0 \
    --results-dir=tests/hardware/results \
    tests/hardware/test_fc02_fc04_corruption.py

# Full diagnostic bundle (smoke + sweep + corruption) for one device.
python -m pytest -q \
    --hardware --allow-firmware-bug-tests \
    --port=/dev/ttyUSB0 --slave-id=1 --model=SmartPowerGen_2.0 \
    --results-dir=tests/hardware/results \
    tests/hardware/test_smoke.py \
    tests/hardware/test_register_sweep.py \
    tests/hardware/test_fc02_fc04_corruption.py

# Run the four-model rotation (see RUNBOOK.md for the device swap prompts).
bash scripts/run_hw_all_models.sh   # to be added alongside RUNBOOK.md
```
