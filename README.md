# smartpower-modbus

A Python library for Modbus RTU communication with SmartPower power-supply
modules (`MOD-537-250` control board). Wraps
[`pymodbus`](https://pypi.org/project/pymodbus/) with **model-aware** typed
register accessors, structured exceptions, and a small CLI.

## Install

```powershell
pip install -e .
```

Requires Python 3.10+ and `pymodbus>=3.7,<4`.

## Supported SmartPower models

The public API talks in **product model names** only:

| `SmartPowerModel` member  | Public name (`.value`)  | Customer-facing platform |
|---------------------------|-------------------------|--------------------------|
| `SmartPowerModel.SOLO`    | `SmartPowerSolo`        | SmartPower Solo          |
| `SmartPowerModel.GEN_1_0` | `SmartPowerGen_1.0`     | SmartPower Gen 1.0       |
| `SmartPowerModel.GEN_1_5` | `SmartPowerGen_1.5`     | SmartPower Gen 1.5       |
| `SmartPowerModel.GEN_2_0` | `SmartPowerGen_2.0`     | SmartPower Gen 2.0       |

The mapping from these public model names to the firmware-repo branch that
ships on each model is internal — see
[`smartpower_modbus/models.py`](smartpower_modbus/models.py). The firmware
branch names are implementation detail and may change without notice; the
public model names will not.

### Per-model register differences

- The Gen 2.0 firmware renames `PA_COOLANT_FLOW` → `MCB_COOLANT_FLOW`
  (same address `0x200E`). Both spellings resolve via
  `Register.from_name(...)`.
- Gen 2.0 and Gen 1.5 fix the firmware-side typo `ACIVE_PROFILE` →
  `ACTIVE_PROFILE` (same address `0x201E`). Both spellings resolve.
- `INPUT_REG_THERMO_REG_LIMIT` (`0x2021`), `HOLD_REG_THERMO_REG_EXT_SP`
  (`0x3018`), and `HOLD_REG_THERMO_REG_EXT_LIMIT` (`0x3019`) exist only
  on **SmartPowerSolo** and **SmartPowerGen_1.0**. Reading or writing
  them against the other models raises `UnsupportedRegisterError` before
  any wire activity.

## Library usage

```python
from smartpower_modbus import SmartPowerModel, Register, SmartPowerClient

with SmartPowerClient(
    port="COM5", slave_id=1, model=SmartPowerModel.GEN_2_0,
) as client:
    out_p = client.read(Register.INPUT_REG_OUT_P)           # int (uint16)
    in_t  = client.read(Register.INPUT_REG_IN_COOLANT_T)    # int (int16, signed)
    fault = client.read(Register.INPUT_FAULT)               # bool (discrete input)

    client.write(Register.HOLD_REG_SP_P, 50)
    assert client.read(Register.HOLD_REG_SP_P) == 50
```

The `model=` argument accepts:

- a `SmartPowerModel` member: `SmartPowerModel.GEN_2_0`
- the canonical public string: `"SmartPowerGen_2.0"`
- the Python member name: `"GEN_2_0"`

Low-level access (raw addresses, no validation):

```python
values = client.read_holding(0x3007, count=2)
client.write_holding(0x3007, 50)
```

Walkthrough script:

```powershell
python example.py --port COM5 --slave 1 --model SmartPowerGen_2.0 --sp-p 50
```

## CLI

`--model` accepts a public SmartPower model name.

```powershell
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 read OUT_P OUT_I OUT_V
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 write HOLD_REG_SP_P 50
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 dump
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 probe
smartpower-cli --model SmartPowerGen_2.0 list-registers
smartpower-cli list-models
```

If the `smartpower-cli` entry point isn't on PATH, use
`python -m smartpower_modbus.cli ...`.

## Backward compatibility

The following deprecated forms still work — each emits a
`DeprecationWarning` and resolves to the equivalent public model:

- **`SmartPowerClient(branch=...)`** — pass `model=` instead.
- **`client.probe_branch()`** — use `client.probe_model()`.
- **`client.branch`** attribute — use `client.model`.
- **CLI `--branch`** — use `--model`.
- **CLI `list-branches`** — use `list-models`.
- **`SmartPowerModel.from_name("MegaMain")`** and other firmware-branch
  strings — pass a public model name (e.g. `"SmartPowerGen_2.0"`).

Internally, the `FirmwareBranch` enum is still available for advanced
inspection (`SmartPowerModel.GEN_2_0.firmware_branch` returns it) but it is
not part of the public surface — `from smartpower_modbus import …`
deliberately exports `SmartPowerModel`, not `FirmwareBranch`.

## Errors

All raised exceptions inherit from `SmartPowerError`:

- `UnsupportedFirmwareBranchError` — model / branch name not recognised
- `UnsupportedRegisterError` — register not exposed by the selected model
- `ReadOnlyRegisterError` — attempted to write a discrete input / input register
- `InvalidValueError` — value out of range or wrong type
- `SerialPortError` — could not open or hold the serial port
- `ModbusCommError` — base for transport-level failures
  - `ModbusTimeoutError`, `ModbusCrcError` — retried automatically (configurable)
  - `IllegalAddressError`, `IllegalValueError`, `SlaveDeviceFailureError` — Modbus
    exception responses 0x02 / 0x03 / 0x04, **not** retried

## Adding a new SmartPower model

1. Add a new member to `SmartPowerModel` in
   `smartpower_modbus/models.py` with the canonical public name as the
   `.value` string.
2. Add a new member to `FirmwareBranch` in
   `smartpower_modbus/branches.py` with the exact firmware-repo branch
   name as its `.value`.
3. Add the new model → branch pair to `_MODEL_TO_BRANCH` in `models.py`.
   The integrity asserts at the bottom of `models.py` will fail at import
   time if you forget.
4. In `smartpower_modbus/registers.py`, append the new firmware branch to
   the `branches=` set of any existing `Register` it exposes, and add
   new `Register` members for any genuinely new addresses.
5. Run `pytest tests/`.

Public API never changes shape — only the contents of these enums and the
mapping table do.

## Tests

```powershell
pip install -e .[test]
pytest
```
