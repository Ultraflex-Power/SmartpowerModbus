"""Connectivity + identification smoke tests against a real SmartPower module.

Gated by ``@pytest.mark.hardware`` — pass ``--hardware --port=...`` to opt in.
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    Register,
    SmartPowerClient,
    SmartPowerModel,
)

pytestmark = pytest.mark.hardware


def test_port_opens_and_baseline_read(hw_client: SmartPowerClient) -> None:
    """The session fixture already ran connect(); a baseline telemetry
    read here is the cheapest possible end-to-end sanity check.
    """
    # INPUT_REG_IN_V is the mains-input voltage; always exposed on every
    # supported firmware branch and always non-zero on a powered unit
    # (so a "read returns 0" failure is informative).
    in_v_raw = hw_client.read(Register.INPUT_REG_IN_V)
    assert isinstance(in_v_raw, int)
    # The scaled value is 0.1 V/lsb; >= 50 V is a permissive sanity floor
    # (a powered three-phase unit reads ~230 V phase-to-neutral). A unit
    # in standby with mains absent will fail this — that's intentional;
    # the smoke test should detect a "we read 0 because nothing was on
    # the bus" outcome.
    in_v = in_v_raw * Register.INPUT_REG_IN_V.scale
    assert in_v >= 50.0, (
        f"INPUT_REG_IN_V={in_v} V — is the unit mains-powered? "
        f"(raw={in_v_raw})"
    )


def test_read_device_info(hw_client: SmartPowerClient) -> None:
    """FC 0x2B/0x0E returns vendor / product_code / revision."""
    info = hw_client.read_device_info()
    assert set(info.keys()) >= {"vendor", "product_code", "revision"}
    assert info["product_code"], "device returned an empty PRODUCT_CODE"
    assert info["vendor"], "device returned an empty vendor string"


def test_product_code_matches_resolved_model(hw_client: SmartPowerClient) -> None:
    """The resolved model on the client must match what the device reports.

    Covers two paths:
    - ``--model`` supplied: the device's PRODUCT_CODE must agree with it.
    - ``--model`` omitted: connect() auto-identified via FC 0x2B, so
      this is a tautology — but it's still a useful check that the
      lookup didn't quietly resolve to a different member.
    """
    reported = SmartPowerModel.from_product_code(hw_client.read_product_code())
    resolved = hw_client._require_model()
    assert reported is resolved, (
        f"Device reports {reported.value} but client is configured for "
        f"{resolved.value} — bus may be connected to the wrong unit, or "
        f"the model table is stale."
    )


def test_probe_model_includes_resolved(hw_client: SmartPowerClient) -> None:
    """``probe_model()`` returns a tuple of candidates; the resolved
    model must be among them. Two-element ambiguity (Solo+Gen_1.0 or
    Gen_1.5+Gen_2.0) is expected and not a failure.
    """
    candidates = hw_client.probe_model()
    assert hw_client._require_model() in candidates, (
        f"probe_model() returned {[c.value for c in candidates]}, "
        f"which does not include the resolved model "
        f"{hw_client._require_model().value}."
    )


def test_safety_bit_heat_is_off(hw_client: SmartPowerClient) -> None:
    """Belt-and-suspenders: the autouse safety guard already enforces
    this, but a dedicated test name makes the intent obvious in the
    pytest output and gives us a clean failure when the guard is
    accidentally removed.

    Only ``INPUT_HEAT`` is asserted off — ``INPUT_ENABLE`` is allowed to
    be high (output stage energised but not commanded to heat is a
    normal bench state). See the guard docstring in
    ``tests/hardware/conftest.py`` for the rationale.
    """
    assert hw_client.read(Register.INPUT_HEAT) is False
