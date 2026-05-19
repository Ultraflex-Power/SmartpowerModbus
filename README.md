# smartpower-modbus

A Python library for Modbus RTU communication with SmartPower
(`MOD-537-250`) control boards. Wraps [`pymodbus`](https://pypi.org/project/pymodbus/)
with branch-aware typed register accessors, structured exceptions, and a
small CLI.

## Install

```powershell
pip install -e .
```

Requires Python 3.10+ and `pymodbus>=3.7,<4`.

## Supported platforms

Canonical `FirmwareBranch` identifiers are platform names. Each one maps to
the exact firmware-repo branch where its `AppAddress_t` enum lives — the
firmware branch string is the serialized contract and is what gets stored
in configs and shown in logs.

| `FirmwareBranch` member  | Firmware branch (`.value`)                            | Platform           |
|--------------------------|-------------------------------------------------------|--------------------|
| `SMARTPOWER_SOLO`        | `SngleModule_5540_LF_MF_ExtPA_simple`                 | SmartPower Solo    |
| `SMARTPOWER_GEN_1_0`     | `Gen_1_5_MOD-5537-110_24_outputs_pwm_limit`           | SmartPower Gen 1.0 |
| `SMARTPOWER_GEN_1_5`     | `ProductionPhase1_Fast_1_15_base`                     | SmartPower Gen 1.5 |
| `SMARTPOWER_GEN_2_0`     | `MegaMain`                                            | SmartPower Gen 2.0 |

The firmware-repo branch names diverged from the product platforms over
time — most notably the firmware branch literally called `Gen_1_5_…`
actually carries the **Gen 1.0** firmware, and `ProductionPhase1_…`
carries the **Gen 1.5** firmware. The platform identifier on the
`FirmwareBranch` member is the canonical name.

### Backward compatibility

The previous Python identifiers are preserved as enum **aliases**:

| Legacy identifier (still works)                | Canonical identifier   |
|------------------------------------------------|------------------------|
| `SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE`         | `SMARTPOWER_SOLO`      |
| `MEGA_MAIN`                                    | `SMARTPOWER_GEN_2_0`   |
| `PRODUCTION_PHASE_1_FAST_1_15_BASE`            | `SMARTPOWER_GEN_1_5`   |
| `GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT`    | `SMARTPOWER_GEN_1_0`   |

`FirmwareBranch.MEGA_MAIN is FirmwareBranch.SMARTPOWER_GEN_2_0` is `True`,
and `FirmwareBranch.from_name(...)` accepts either spelling plus the
firmware branch string (`"MegaMain"`).

### Per-platform register differences

- The Gen 2.0 firmware renames `PA_COOLANT_FLOW` → `MCB_COOLANT_FLOW`
  (same address `0x200E`).
- Gen 2.0 and Gen 1.5 fix the typo `ACIVE_PROFILE` → `ACTIVE_PROFILE`
  (same address `0x201E`).
- `INPUT_REG_THERMO_REG_LIMIT` (`0x2021`), `HOLD_REG_THERMO_REG_EXT_SP`
  (`0x3018`), and `HOLD_REG_THERMO_REG_EXT_LIMIT` (`0x3019`) exist only
  on `SMARTPOWER_SOLO` and `SMARTPOWER_GEN_1_5`.

The legacy register spellings remain accepted by `Register.from_name(...)`.

## Library usage

```python
from smartpower_modbus import FirmwareBranch, Register, SmartPowerClient

with SmartPowerClient(
    port="COM5", slave_id=1, branch=FirmwareBranch.SMARTPOWER_GEN_2_0,
) as client:
    out_p   = client.read(Register.INPUT_REG_OUT_P)         # int (uint16)
    in_t    = client.read(Register.INPUT_REG_IN_COOLANT_T)  # int (int16, signed)
    fault   = client.read(Register.INPUT_FAULT)             # bool (discrete input)

    client.write(Register.HOLD_REG_SP_P, 50)
    assert client.read(Register.HOLD_REG_SP_P) == 50
```

Low-level access (raw addresses, no validation):

```python
values = client.read_holding(0x3007, count=2)
client.write_holding(0x3007, 50)
```

Run the included walkthrough:

```powershell
python example.py --port COM5 --slave 1 --branch MegaMain --sp-p 50
```

## CLI

`--branch` accepts either the platform identifier (`SMARTPOWER_GEN_2_0`) or
the firmware-repo branch string (`MegaMain`).

```powershell
smartpower-cli --port COM5 --slave 1 --branch SMARTPOWER_GEN_2_0 read OUT_P OUT_I OUT_V
smartpower-cli --port COM5 --slave 1 --branch SMARTPOWER_GEN_2_0 write HOLD_REG_SP_P 50
smartpower-cli --port COM5 --slave 1 --branch SMARTPOWER_GEN_2_0 dump
smartpower-cli --port COM5 --slave 1 --branch SMARTPOWER_GEN_2_0 probe
smartpower-cli --branch SMARTPOWER_GEN_2_0 list-registers
smartpower-cli list-branches
```

If the `smartpower-cli` entry point isn't on PATH, you can equivalently use
`python -m smartpower_modbus.cli ...` from the install environment.

## Errors

All raised exceptions inherit from `SmartPowerError`:

- `UnsupportedFirmwareBranchError` — branch name not recognised
- `UnsupportedRegisterError` — register not exposed by the selected branch
- `ReadOnlyRegisterError` — attempted to write a discrete input / input register
- `InvalidValueError` — value out of range or wrong type
- `SerialPortError` — could not open or hold the serial port
- `ModbusCommError` — base for transport-level failures
  - `ModbusTimeoutError`, `ModbusCrcError` — retried automatically (configurable)
  - `IllegalAddressError`, `IllegalValueError`, `SlaveDeviceFailureError` — Modbus
    exception responses 0x02 / 0x03 / 0x04, **not** retried

## Adding a new firmware branch / platform

1. `git show <branch>:App/Communication/ModBus.hpp` and diff `AppAddress_t`
   against the four already-mapped branches.
2. Add a new member to `FirmwareBranch` in
   `smartpower_modbus/branches.py` with the platform-style identifier and
   the exact firmware branch string as its `.value`.
3. In `smartpower_modbus/registers.py`, add the new member to the
   `branches=` argument of any existing `Register` that the new firmware
   exposes, and add new `Register` members for any genuinely new addresses.
4. Run `pytest tests/`.

## Tests

```powershell
pip install -e .[test]
pytest
```
