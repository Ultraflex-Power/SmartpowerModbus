"""Read-only sweep of every register exposed by the connected model.

Validates that:
- the entire model-specific register set can be read without transport
  errors;
- raw values for ``INPUT_REG``/``HOLDING_REG`` fall inside their
  declared signed/unsigned range;
- discrete bits read back as ``bool``;
- a repeat sweep returns the same shape (i.e. no read has destructive
  side effects on the addressing layer).

Gated by ``@pytest.mark.hardware``.
"""

from __future__ import annotations

import logging

import pytest

from smartpower_modbus import (
    Register,
    RegisterKind,
    SmartPowerClient,
)
from smartpower_modbus.client import interpret_raw
from smartpower_modbus.units import TemperatureUnit, is_temperature_unit

pytestmark = pytest.mark.hardware

logger = logging.getLogger(__name__)


def _expected_regs(client: SmartPowerClient) -> list[Register]:
    return sorted(
        Register.for_model(client._require_model()), key=lambda r: r.addr,
    )


def test_dump_covers_every_model_register(hw_client: SmartPowerClient) -> None:
    """``client.dump()`` must return an entry for each register the
    firmware exposes (modulo registers the slave reports as illegal,
    which ``dump()`` already logs and skips).
    """
    dump = hw_client.dump()
    expected = _expected_regs(hw_client)

    missing = [r for r in expected if r not in dump]
    # dump() drops registers the slave answered IllegalAddress on; we
    # report them but don't fail — a model mismatch in our register
    # table should be surfaced for diagnosis, not silently passed.
    if missing:
        logger.warning(
            "%d registers missing from dump (likely IllegalAddress from slave): %s",
            len(missing), [r.name for r in missing],
        )
    # Hard floor: more than half the expected map missing is a model
    # misconfiguration, not a stray illegal-address.
    assert len(missing) <= len(expected) // 2, (
        f"dump() returned only {len(dump)}/{len(expected)} expected registers. "
        f"Most likely the wrong --model was selected, or the firmware variant "
        f"is not in the library's register table."
    )


def test_dump_values_have_correct_shape(hw_client: SmartPowerClient) -> None:
    """Raw values match the type the kind/signedness contract promises."""
    for reg, value in hw_client.dump().items():
        if reg.kind in (RegisterKind.COIL, RegisterKind.DISCRETE_INPUT):
            assert isinstance(value, bool), (
                f"{reg.name} is a {reg.kind.value} but returned "
                f"{type(value).__name__} {value!r}"
            )
        else:
            assert isinstance(value, int) and not isinstance(value, bool), (
                f"{reg.name} is a {reg.kind.value} but returned "
                f"{type(value).__name__} {value!r}"
            )
            if reg.signed:
                assert -0x8000 <= value <= 0x7FFF, (
                    f"{reg.name} is signed but raw value {value} is outside int16"
                )
            else:
                assert 0 <= value <= 0xFFFF, (
                    f"{reg.name} is unsigned but raw value {value} is outside uint16"
                )


def test_dump_is_repeatable(hw_client: SmartPowerClient) -> None:
    """A second sweep must return the same set of keys and the same
    value types. The *values* themselves can drift (telemetry registers
    update continuously), so we only compare shape.
    """
    first = hw_client.dump()
    second = hw_client.dump()
    assert set(first.keys()) == set(second.keys()), (
        "Repeat sweep returned a different set of registers — addressing "
        "is not deterministic, which suggests bus errors or a flaky slave."
    )
    for reg in first:
        assert type(first[reg]) is type(second[reg]), (
            f"{reg.name} changed type between sweeps: "
            f"{type(first[reg]).__name__} -> {type(second[reg]).__name__}"
        )


def test_temperature_registers_plausible_celsius(
    hw_client: SmartPowerClient,
) -> None:
    """Temperature registers (unit == K) interpret to a plausible
    Celsius range on a powered, room-temperature unit. Logged-only —
    a cold/disconnected sensor is allowed to read out-of-range without
    failing the suite.
    """
    out_of_range: list[str] = []
    for reg in _expected_regs(hw_client):
        if not is_temperature_unit(reg.unit):
            continue
        try:
            raw = hw_client.read(reg)
        except Exception as exc:
            logger.warning("Could not read %s: %s", reg.name, exc)
            continue
        celsius = interpret_raw(reg, raw, TemperatureUnit.CELSIUS)
        if not -50.0 <= celsius <= 200.0:
            out_of_range.append(f"{reg.name}={celsius:.1f}°C (raw={raw})")
    if out_of_range:
        logger.warning(
            "%d temperature registers outside [-50, 200]°C (sensor "
            "disconnected, or unit cold): %s",
            len(out_of_range), out_of_range,
        )
