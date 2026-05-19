"""Unit conversion for interpreted register reads/writes.

The SmartPower firmware encodes physical quantities as scaled 16-bit
integers — for example, "input water temperature" at 0x200B is stored as
``temperature_in_kelvin * 10``. The ``scale`` and ``unit`` fields on
``RegisterMeta`` describe that mapping:

    interpreted_value = raw * scale

For temperatures (``unit == "K"``) the library additionally lets the
caller pick the display unit — Celsius by default, with Kelvin and
Fahrenheit available. Conversion is applied on top of the scaling so a
raw value of 2981 reads as 24.95 °C, 298.1 K, or 76.91 °F.

Spec reference: ``Doc/SDR-1MOD-537-250-00_A6_USP_Modbus.doc``.
"""

from __future__ import annotations

from enum import Enum


class TemperatureUnit(Enum):
    """Display unit for registers whose firmware unit is Kelvin.

    The library always interprets the firmware-side value as Kelvin (per
    the Modbus spec); this enum only controls what the caller sees.
    """

    CELSIUS = "C"
    KELVIN = "K"
    FAHRENHEIT = "F"

    @classmethod
    def from_name(cls, name: str) -> "TemperatureUnit":
        s = name.strip().upper().lstrip("°")  # strip leading ° if present
        for u in cls:
            if u.value == s or u.name == s:
                return u
        raise ValueError(
            f"Unknown temperature unit: {name!r}. Known: "
            + ", ".join(u.value for u in cls)
        )


# Absolute-zero offset between Kelvin and Celsius.
_K_TO_C = 273.15


def kelvin_to(value_kelvin: float, unit: TemperatureUnit) -> float:
    """Convert a value in Kelvin to the requested temperature unit."""
    if unit is TemperatureUnit.KELVIN:
        return value_kelvin
    if unit is TemperatureUnit.CELSIUS:
        return value_kelvin - _K_TO_C
    if unit is TemperatureUnit.FAHRENHEIT:
        return (value_kelvin - _K_TO_C) * 9.0 / 5.0 + 32.0
    raise ValueError(f"Unsupported temperature unit: {unit!r}")


def kelvin_from(value: float, unit: TemperatureUnit) -> float:
    """Inverse of ``kelvin_to`` — convert a value in ``unit`` to Kelvin."""
    if unit is TemperatureUnit.KELVIN:
        return value
    if unit is TemperatureUnit.CELSIUS:
        return value + _K_TO_C
    if unit is TemperatureUnit.FAHRENHEIT:
        return (value - 32.0) * 5.0 / 9.0 + _K_TO_C
    raise ValueError(f"Unsupported temperature unit: {unit!r}")


def is_temperature_unit(unit: str) -> bool:
    """Whether a ``RegisterMeta.unit`` string identifies a temperature."""
    return unit == "K"
