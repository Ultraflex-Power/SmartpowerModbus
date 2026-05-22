# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Python library for Modbus RTU communication with SmartPower power-supply
modules (`MOD-537-250` control board). Wraps `pymodbus` with model-aware
typed register accessors, structured exceptions, and a small CLI
(`smartpower-cli` / `python -m smartpower_modbus.cli`).

Requires Python 3.10+; supports `pymodbus>=3.7,<4`.

## Common commands

```bash
pip install -e .[dev]                                      # dev install
python -m pytest -q                                        # run all tests
python -m pytest tests/test_capacitance.py -q              # one file
python -m pytest tests/test_capacitance.py::test_name -q   # one test
python -m pytest -q --cov=smartpower_modbus --cov-report=term-missing
python -m ruff check .
python -m mypy smartpower_modbus
python -m py_compile example.py        # syntax/import smoketest for the example
python -m build && python -m twine check --strict dist/*   # packaging dry-run
```

CI (`.github/workflows/ci.yml`) runs all of the above on Python 3.10–3.13;
reproduce locally before pushing.

## Architecture

Five layers, each with one job — they form a strict dependency chain
(bottom → top):

1. **`_transport.py`** — the only module that imports `pymodbus`. Wraps
   `ModbusSerialClient`, normalises pymodbus's drifting kwarg names
   (`unit`/`slave`/`device_id` across 3.x), translates pymodbus exceptions
   to this library's hierarchy, and runs the retry loop. Reads retry on
   transient errors; writes do **not** retry by default (would risk
   double-write after a lost response — opt in with `retry_writes=True`).
   Modbus exception responses (0x01–0x04) are deterministic and never
   retried.

2. **`exceptions.py`** — `SmartPowerError` base + transport (`ModbusCommError`
   and subclasses) and domain (`UnsupportedRegisterError`, `InvalidValueError`,
   …) errors. Imported by every other module.

3. **`branches.py` + `models.py`** — three-way mapping between public
   `SmartPowerModel` (`SmartPowerGen_2.0` etc.), internal
   `FirmwareBranch` (the firmware-repo branch name), and device-reported
   `PRODUCT_CODE` (e.g. `"55370112"`). `models.py` is the single source
   of truth; integrity asserts at the bottom of the file fail at import
   time if a new model isn't added to all three tables. `FirmwareBranch`
   is implementation detail — keep it out of new public APIs; accept and
   return `SmartPowerModel`.

4. **`registers.py` + `units.py`** — `Register` enum mirrors
   `MODBUS::AppAddress_t` from the firmware's `ModBus.hpp`. Each
   `RegisterMeta` carries the address, kind (discrete input / coil /
   input reg / holding reg), the set of `FirmwareBranch` values that
   expose it, signedness, scale factor, SI unit, and legacy-name aliases
   (firmware typos like `ACIVE_PROFILE`, and the MegaMain rename
   `PA_COOLANT_FLOW` → `MCB_COOLANT_FLOW`). The name lookup index is
   built once at import (`_build_name_index`); ambiguous suffixes (e.g.
   `SP_P` matches two registers) raise a listing error rather than
   silently picking one. `units.py` handles K↔C↔F for temperature
   registers; firmware always stores Kelvin × 10 on the wire.

5. **`client.py`** — `SmartPowerClient` is the user-facing entrypoint.
   Three API tiers, increasingly strict:
   - **Raw** (`read_holding`, `write_holding`, …): take raw addresses,
     no kind/model/signedness validation. Useful for poking unknown
     addresses.
   - **Register-typed** (`read`, `write`, `read_many`): take a
     `Register`, validate that the configured model exposes it, apply
     signed16 decoding, batch contiguous reads in `read_many`.
   - **Interpreted** (`read_value`, `write_value`): also apply
     `scale`/unit and temperature conversion. The `interpret_raw` helper
     is reused by the CLI's `dump --interpret` path so there's one
     source of truth for scaling.

   Composite operations live here too: `read_capacitance` /
   `write_capacitance` (and the `_second_` variants) read/write the
   value+exponent pair in a single Modbus transaction so a concurrent
   peer can't tear them. `_encode_capacitance` picks the exponent that
   maximises uint16 mantissa precision (lands in `[6554, 65535]` when
   possible) so round-trips preserve ~4 decimal digits.

   Auto-identification: passing `model=None` makes `connect()` issue FC
   0x2B/0x0E (Read Device Identification, `read_code=0x01`) and resolve
   the `PRODUCT_CODE` via the `models.py` table. The firmware ships
   with `MEI_DEV_ONE_OBJ_ENA` disabled — only `read_code=0x01` works;
   the other codes return Modbus exception 0x02. If auto-identification
   raises, the serial port is closed before propagating (`with` would
   otherwise not call `__exit__`).

   All transport methods are guarded by an internal `threading.Lock`;
   one client is safe to share across threads, but the serial port is
   half-duplex so transactions serialise.

6. **`cli.py`** — `smartpower-cli` argparse wrapper exposing `read`,
   `write`, `dump`, `probe`, `identify`, `list-registers`, `list-models`.
   `identify` is the only wire-touching subcommand that doesn't need
   `--model`.

## Per-model register differences (important non-obvious quirks)

- Gen 2.0 (MegaMain) renames `PA_COOLANT_FLOW` → `MCB_COOLANT_FLOW` at
  the same address `0x200E`. Both spellings resolve.
- Gen 2.0 and ProductionPhase1 fix the firmware-side typo
  `ACIVE_PROFILE` → `ACTIVE_PROFILE` at `0x201E`. Both spellings resolve.
- `INPUT_REG_THERMO_REG_LIMIT` (`0x2021`), `HOLD_REG_THERMO_REG_EXT_SP`
  (`0x3018`), and `HOLD_REG_THERMO_REG_EXT_LIMIT` (`0x3019`) exist only
  on **Solo** and **Gen 1.0**. `assert_supported` rejects reads/writes
  against other models before any wire activity.
- `probe_model()` can disambiguate {Solo, Gen 1.0} vs {Gen 1.5, Gen 2.0}
  by reading `0x2021`, but cannot resolve within each pair (the only
  difference there is the `ACIVE_PROFILE`/`ACTIVE_PROFILE` spelling).
  Use `identify_model()` for a unique answer.

## Adding a new SmartPower model

1. Add a member to `SmartPowerModel` in `models.py` (`.value` = canonical
   public name).
2. Add a member to `FirmwareBranch` in `branches.py` (`.value` = exact
   firmware-repo branch string — this is the serialised contract, never
   rename).
3. Add entries to `_MODEL_TO_BRANCH` **and** `_MODEL_TO_PRODUCT_CODE` in
   `models.py`. Import-time asserts at the bottom will fire if either is
   missed.
4. In `registers.py`, append the new firmware branch to the `branches=`
   frozenset of any existing `Register` it exposes, and add new
   `Register` members for genuinely new addresses.
5. Run `pytest tests/`.

Public API never changes shape — only the enum contents and mapping
tables do.

## Tests

Tests use an in-memory `FakeSerialClient` (`tests/conftest.py`) that
records every pymodbus call and returns scripted responses, so they
don't need a paired virtual serial port. The `client` fixture builds a
`SmartPowerClient` wired to the fake and pre-connected as
`SmartPowerModel.GEN_2_0`.

## Style / lint

- Ruff config in `pyproject.toml`: select `E,F,W,I,UP,B,SIM`; ignore
  `E501` (long lines are intentional in docstrings/log messages). Tests
  also ignore `B011`.
- mypy is "gradual strict": `check_untyped_defs`, `warn_unused_ignores`,
  `warn_redundant_casts`, `warn_unreachable`, `no_implicit_optional`.
  `pymodbus.*` is ignored (untyped upstream).
- Line target is 100; not autoformatted.
