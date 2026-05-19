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

| `SmartPowerModel` member  | Public name (`.value`) | `PRODUCT_CODE` | Customer-facing platform |
| ------------------------- | ---------------------- | -------------- | ------------------------ |
| `SmartPowerModel.SOLO`    | `SmartPowerSolo`       | `55400400`     | SmartPower Solo          |
| `SmartPowerModel.GEN_1_0` | `SmartPowerGen_1.0`    | `55370250`     | SmartPower Gen 1.0       |
| `SmartPowerModel.GEN_1_5` | `SmartPowerGen_1.5`    | `55370111`     | SmartPower Gen 1.5       |
| `SmartPowerModel.GEN_2_0` | `SmartPowerGen_2.0`    | `55370112`     | SmartPower Gen 2.0       |

The `PRODUCT_CODE` column shows the value the firmware returns over
Modbus FC 0x2B/0x0E (Read Device Identification). The library uses this
for auto-recognition — see [Auto-detection](#auto-detection).

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

### Interpreted (scaled) reads and writes

`read()` and `write()` operate on raw 16-bit register values. For
physical-unit access — Amps, Volts, Watts, Hz, °C, … — use
`read_value()` / `write_value()`. The scaling factors and SI units come
straight from the Modbus spec (rev A7):

```python
out_p_W  = client.read_value(Register.INPUT_REG_OUT_P)        # Watts (float)
out_i_A  = client.read_value(Register.INPUT_REG_OUT_I)        # Amps (float)
in_t_C   = client.read_value(Register.INPUT_REG_IN_COOLANT_T) # °C (float, default)
freq_Hz  = client.read_value(Register.INPUT_REG_FREQ)         # Hz (float)

client.write_value(Register.HOLD_REG_SP_P, 50.0)              # 50.00 %
client.write_value(Register.HOLD_REG_THERMO_REG_EXT_SP, 25.0) # 25 °C
```

Temperature unit can be set on the client (default Celsius) or
overridden per call. Conversions are applied on top of the firmware's
Kelvin encoding:

```python
from smartpower_modbus import SmartPowerClient, TemperatureUnit

with SmartPowerClient(
    "COM5", slave_id=1, model=SmartPowerModel.GEN_2_0,
    temperature_unit=TemperatureUnit.FAHRENHEIT,
) as client:
    print(client.read_value(Register.INPUT_REG_IN_COOLANT_T))  # °F

    # One-off override:
    in_k = client.read_value(
        Register.INPUT_REG_IN_COOLANT_T, temperature_unit="K",
    )
```

#### Tank-capacitor read / write

The firmware exposes two tank-capacitor pairs as adjacent (value + exponent)
holding registers:

- `HOLD_REG_CAP_VAL` (`0x3008`) / `HOLD_REG_CAP_EXP` (`0x3009`)
- `HOLD_REG_SECOND_CAP_VAL` (`0x3012`) / `HOLD_REG_SECOND_CAP_EXP` (`0x3013`)

For convenience, the client exposes each pair as a single Farads-valued
float:

```python
c1 = client.read_capacitance()         # primary,   single float in F
c2 = client.read_second_capacitance()  # secondary, single float in F
client.write_capacitance(100e-6)       # 100 µF, atomic 2-register write
client.write_second_capacitance(1e-3)  # 1 mF
```

`write_capacitance` chooses the (`val`, `exp`) pair that maximises uint16
precision (mantissa in `[6554, 65535]` whenever possible), so a round-trip
through `read_capacitance` preserves the input to ~4 decimal digits.
Negative, `nan`/`inf`, or out-of-range values (exponent outside `[-30, 6]`)
raise `InvalidValueError`. Both reads and writes use a single Modbus
transaction so the value / exponent pair cannot be torn by a concurrent
peer.

### Low-level (raw addresses, no validation)

```python
values = client.read_holding(0x3007, count=2)
client.write_holding(0x3007, 50)
```

Walkthrough script:

```powershell
python example.py --port COM5 --slave 1 --model SmartPowerGen_2.0 --sp-p 50
```

## Auto-detection

The library can identify the connected SmartPower model automatically
using Modbus FC 0x2B/0x0E (Read Device Identification). Each firmware
ships with a unique `PRODUCT_CODE` constant — the library queries it on
connect and maps it to a `SmartPowerModel`.

Three ways to use it:

**Implicit (auto-detect at connect).** Pass `model=None` (or simply omit
`model=`) and the client identifies the device during `connect()`:

```python
from smartpower_modbus import SmartPowerClient, Register

with SmartPowerClient("COM5", slave_id=1) as client:
    print(client.model.value)              # "SmartPowerGen_2.0"
    print(client.read(Register.INPUT_REG_OUT_P))
```

**Explicit identification.**

```python
with SmartPowerClient("COM5", slave_id=1) as client:
    model = client.identify_model()        # SmartPowerModel.GEN_2_0
    info  = client.read_device_info()      # vendor / product_code / revision
    code  = client.read_product_code()     # "55370112"
```

**From the CLI.**

```powershell
smartpower-cli --port COM5 --slave 1 identify
# Vendor:       Ultraflex Power
# Product code: 55370112
# Revision:     1.0.0
# Detected:     SmartPowerGen_2.0
```

If the device reports a `PRODUCT_CODE` that doesn't match any known
model, the library raises `UnsupportedFirmwareBranchError` with the raw
code in the message so it can be added to
`smartpower_modbus/models.py:_MODEL_TO_PRODUCT_CODE`. If the device
returns Modbus exception 0x01 (Illegal Function), the library raises
`IllegalFunctionError` — the firmware on the slave does not implement
FC 0x2B and you must pass `model=` explicitly.

When **both** `model=` is given **and** auto-identification disagrees
(via an explicit `identify_model()` call), the explicit value wins and
a warning is logged.

## CLI

`--model` accepts a public SmartPower model name.

```powershell
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 read OUT_P OUT_I OUT_V
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 write HOLD_REG_SP_P 50
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 dump
smartpower-cli --port COM5 --slave 1 --model SmartPowerGen_2.0 probe
smartpower-cli --port COM5 --slave 1 identify
smartpower-cli --model SmartPowerGen_2.0 list-registers
smartpower-cli list-models
```

`identify` is the only wire-touching subcommand that does not need
`--model` — it auto-detects via `PRODUCT_CODE`.

### CLI: interpreted reads/writes and temperature units

Pass `-i` / `--interpret` to `read`, `write`, or `dump` to apply the
firmware's scaling factor and SI units. Combine with
`--temperature-unit {C,K,F}` (default `C`) to pick the temperature
display unit.

```powershell
# Raw uint16:
smartpower-cli --port COM6 --slave 1 --model SmartPowerGen_1.0 read OUT_I IN_COOLANT_T
# INPUT_REG_OUT_I        0x2012  =  20 (0x0014)
# INPUT_REG_IN_COOLANT_T 0x200B  =  2981 (0x0BA5)

# Interpreted (default Celsius):
smartpower-cli --port COM6 --slave 1 --model SmartPowerGen_1.0 read -i OUT_I IN_COOLANT_T
# INPUT_REG_OUT_I        0x2012  =  2 A
# INPUT_REG_IN_COOLANT_T 0x200B  =  24.95 °C

# Same data, Fahrenheit:
smartpower-cli --port COM6 --slave 1 --model SmartPowerGen_1.0 --temperature-unit F read -i IN_COOLANT_T
# INPUT_REG_IN_COOLANT_T 0x200B  =  76.91 °F

# Write a temperature setpoint in Celsius (the firmware stores Kelvin x10):
smartpower-cli --port COM6 --slave 1 --model SmartPowerGen_1.0 write -i HOLD_REG_THERMO_REG_EXT_SP 25
```

If the `smartpower-cli` entry point isn't on PATH, use
`python -m smartpower_modbus.cli ...`.

## Errors

All raised exceptions inherit from `SmartPowerError`:

- `UnsupportedFirmwareBranchError` — model / branch name not recognised
- `UnsupportedRegisterError` — register not exposed by the selected model
- `ReadOnlyRegisterError` — attempted to write a discrete input / input register
- `InvalidValueError` — value out of range or wrong type
- `SerialPortError` — could not open or hold the serial port
- `ModbusCommError` — base for transport-level failures
  - `ModbusTimeoutError`, `ModbusCrcError` — retried automatically on
    **reads** (up to `retries=` attempts). **Writes** are not retried by
    default so a torn / duplicate write can't silently bump a setpoint
    twice; pass `retry_writes=True` to the client if every writable
    register on your bus is idempotent.
  - `IllegalFunctionError`, `IllegalAddressError`, `IllegalValueError`,
    `SlaveDeviceFailureError` — Modbus exception responses
    0x01 / 0x02 / 0x03 / 0x04, **not** retried

## Adding a new SmartPower model

1. Add a new member to `SmartPowerModel` in
   `smartpower_modbus/models.py` with the canonical public name as the
   `.value` string.
2. Add a new member to `FirmwareBranch` in
   `smartpower_modbus/branches.py` with the exact firmware-repo branch
   name as its `.value`.
3. Add the new model → branch pair to `_MODEL_TO_BRANCH` **and** the
   model → product-code pair to `_MODEL_TO_PRODUCT_CODE` in `models.py`.
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

### Developer checks

CI runs the same commands; reproduce them locally before pushing:

```powershell
pip install -e .[dev]
python -m py_compile example.py
python -m ruff check .
python -m mypy smartpower_modbus
python -m pytest -q --cov=smartpower_modbus --cov-report=term-missing
python -m build                           # sdist + wheel
python -m twine check --strict dist/*     # packaging metadata
```

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Ultraflex Power.
