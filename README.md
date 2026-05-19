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

## Supported firmware branches

The library currently knows the `AppAddress_t` register map for these
firmware branches:

| `FirmwareBranch` member                            | Firmware branch name                                 |
|----------------------------------------------------|------------------------------------------------------|
| `SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE`             | `SngleModule_5540_LF_MF_ExtPA_simple`                |
| `MEGA_MAIN`                                        | `MegaMain`                                           |
| `PRODUCTION_PHASE_1_FAST_1_15_BASE`                | `ProductionPhase1_Fast_1_15_base`                    |
| `GEN_1_5_MOD_5537_110_24_OUTPUTS_PWM_LIMIT`        | `Gen_1_5_MOD-5537-110_24_outputs_pwm_limit`          |

Differences captured:
- `MegaMain` renames `PA_COOLANT_FLOW` → `MCB_COOLANT_FLOW` (same address `0x200E`).
- `MegaMain` and `ProductionPhase1_Fast_1_15_base` fix the typo
  `ACIVE_PROFILE` → `ACTIVE_PROFILE` (same address `0x201E`).
- `INPUT_REG_THERMO_REG_LIMIT` (`0x2021`), `HOLD_REG_THERMO_REG_EXT_SP`
  (`0x3018`), `HOLD_REG_THERMO_REG_EXT_LIMIT` (`0x3019`) exist only on
  `SngleModule_5540_LF_MF_ExtPA_simple` and
  `ProductionPhase1_Fast_1_15_base`.

The legacy spellings remain accepted by `Register.from_name(...)`.

## Library usage

```python
from smartpower_modbus import FirmwareBranch, Register, SmartPowerClient

with SmartPowerClient(
    port="COM5", slave_id=1, branch=FirmwareBranch.MEGA_MAIN,
) as client:
    out_p   = client.read(Register.INPUT_REG_OUT_P)        # int (uint16)
    in_t    = client.read(Register.INPUT_REG_IN_COOLANT_T)  # int (int16, signed)
    fault   = client.read(Register.INPUT_FAULT)            # bool (discrete input)

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

```powershell
smartpower-cli --port COM5 --slave 1 --branch MegaMain read OUT_P OUT_I OUT_V
smartpower-cli --port COM5 --slave 1 --branch MegaMain write HOLD_REG_SP_P 50
smartpower-cli --port COM5 --slave 1 --branch MegaMain dump
smartpower-cli --port COM5 --slave 1 --branch MegaMain probe
smartpower-cli --branch MegaMain list-registers
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

## Adding a new firmware branch

1. `git show <branch>:App/Communication/ModBus.hpp` and diff `AppAddress_t`
   against the four already-mapped branches.
2. Add a new member to `FirmwareBranch` in
   `smartpower_modbus/branches.py` with the exact firmware branch name as
   the enum value.
3. In `smartpower_modbus/registers.py`, add the new branch to the
   `branches=` argument of any existing `Register` that the new firmware
   exposes, and add new `Register` members for any genuinely new addresses.
4. Run `pytest tests/`.

## Tests

```powershell
pip install -e .[test]
pytest
```
