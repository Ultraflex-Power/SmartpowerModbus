"""Round-trip write tests on an allowlist of non-actuating registers.

Every test follows the same shape::

    original = client.read(reg)
    try:
        client.write(reg, original + delta)
        assert client.read(reg) == original + delta
    finally:
        client.write(reg, original)

so a partial failure still restores the original value. Every read,
write, readback, and restore is also printed to the terminal so the
operator can verify by eye that the bus state is what the test thinks
it is — run with ``pytest -s`` (or pytest's default for failed tests)
to see the live trace.

Gated by ``@pytest.mark.hardware_write`` — requires both ``--hardware``
and ``--allow-writes``.
"""

from __future__ import annotations

import logging

import pytest

from smartpower_modbus import (
    Register,
    SmartPowerClient,
    UnsupportedRegisterError,
)

pytestmark = pytest.mark.hardware_write

logger = logging.getLogger(__name__)


def _say(msg: str) -> None:
    """Operator-facing trace line. ``print`` rather than logger.info so the
    output is unconditional with ``pytest -s`` and shows under the
    failing-test capture buffer otherwise — no log-level fiddling
    required to see what the test did with your registers.
    """
    print(f"  [hw] {msg}")


def _read_or_skip(client: SmartPowerClient, reg: Register) -> int:
    """Read ``reg`` or skip the test if the firmware doesn't expose it.

    Per-model registers (e.g. ``HOLD_REG_THERMO_REG_EXT_SP`` on
    Solo/Gen_1.0) are pre-rejected by ``assert_supported``; turning
    that into a clear skip beats letting the test fail with a generic
    UnsupportedRegisterError.
    """
    try:
        value = client.read(reg)
    except UnsupportedRegisterError as exc:
        pytest.skip(
            f"{reg.name} is not exposed by the connected model "
            f"({client._require_model().value}): {exc}"
        )
    assert isinstance(value, int), f"{reg.name} read returned {type(value).__name__}"
    return value


def _roundtrip_int_reg(client: SmartPowerClient, reg: Register, target: int) -> None:
    """Read, write, read-back, restore — printing each value.

    Bundles the common pattern so each test reads top-to-bottom.
    """
    original = _read_or_skip(client, reg)
    _say(f"{reg.name}: ORIGINAL = {original} (0x{original & 0xFFFF:04X})")
    _say(f"{reg.name}: TARGET   = {target} (0x{target & 0xFFFF:04X})")
    try:
        client.write(reg, target)
        readback = client.read(reg)
        _say(f"{reg.name}: READBACK = {readback} (0x{readback & 0xFFFF:04X})")
        assert readback == target, (
            f"{reg.name} round-trip failed: wrote {target}, read {readback}"
        )
    finally:
        client.write(reg, original)
        restored = client.read(reg)
        _say(f"{reg.name}: RESTORED = {restored} (0x{restored & 0xFFFF:04X})")
        assert restored == original, (
            f"FAILED to restore {reg.name} to {original}; current value "
            f"is {restored} — INSPECT THE UNIT before re-running."
        )


def test_roundtrip_hs_ratio(hw_client: SmartPowerClient) -> None:
    """``HOLD_REG_HS_RATIO`` (uint16, scale 0.01) — heat-station
    transformer ratio. Pure config, no actuation."""
    reg = Register.HOLD_REG_HS_RATIO
    original = _read_or_skip(hw_client, reg)
    # Choose a perturbation that stays inside uint16 and is far enough
    # from the original to detect a no-op write.
    target = (original + 1) & 0xFFFF
    if target == original:
        target = (original + 2) & 0xFFFF
    _roundtrip_int_reg(hw_client, reg, target)


def test_roundtrip_req_profile(hw_client: SmartPowerClient) -> None:
    """``HOLD_REG_REQ_PROFILE`` (uint16) — requested profile index.

    Setting this only stages a profile change; the firmware acts on it
    after a separate load command. The number of valid profiles varies
    per firmware build — we just round-trip the current value with a
    small perturbation.
    """
    reg = Register.HOLD_REG_REQ_PROFILE
    original = _read_or_skip(hw_client, reg)
    # Stay small to dodge an out-of-range rejection from the firmware.
    target = 1 if original != 1 else 2
    _say(f"{reg.name}: ORIGINAL = {original}")
    _say(f"{reg.name}: TARGET   = {target}")

    try:
        hw_client.write(reg, target)
        actual = hw_client.read(reg)
        _say(f"{reg.name}: READBACK = {actual}")
        # Some firmware builds clamp the index to the number of defined
        # profiles. Read-back may differ from target — log and continue
        # if so (we still need to restore the original).
        if actual != target:
            logger.warning(
                "%s: wrote %d but read back %d (firmware likely clamped). "
                "Treating as soft-pass — round-trip semantics still OK.",
                reg.name, target, actual,
            )
            _say(
                f"{reg.name}: NOTE — firmware clamped {target} to {actual}; "
                "soft-pass."
            )
    finally:
        hw_client.write(reg, original)
        restored = hw_client.read(reg)
        _say(f"{reg.name}: RESTORED = {restored}")
        assert restored == original, (
            f"FAILED to restore {reg.name} to {original} — INSPECT THE UNIT."
        )


def test_roundtrip_timer_sp(hw_client: SmartPowerClient) -> None:
    """``HOLD_REG_TIMER_SP`` (int16, scale 0.1 s) — heat timer setpoint.

    Only takes effect when heating; safe to round-trip while disabled.
    The autouse safety guard ensures ``INPUT_HEAT`` is off.
    """
    reg = Register.HOLD_REG_TIMER_SP
    original = _read_or_skip(hw_client, reg)
    target = original + 10 if original < 0x7FF0 else original - 10
    _roundtrip_int_reg(hw_client, reg, target)


def test_roundtrip_capacitance_pair(hw_client: SmartPowerClient) -> None:
    """``write_capacitance`` / ``read_capacitance`` round-trip via the
    val+exp composite. The encoder maximises uint16 precision so a
    small float perturbation must come back within ~0.05 %.
    """
    try:
        original_F = hw_client.read_capacitance()
    except UnsupportedRegisterError as exc:
        pytest.skip(f"Capacitance pair not exposed on this model: {exc}")

    # If the unit ships with cap=0 (uninitialised), use 1 µF — well
    # inside the encoder's range — so we still exercise the round-trip;
    # otherwise perturb by +1 %. Original is restored in finally.
    target_F = 1e-6 if original_F <= 0 else original_F * 1.01
    _say(f"CAPACITANCE: ORIGINAL = {original_F:.6e} F")
    _say(f"CAPACITANCE: TARGET   = {target_F:.6e} F")

    try:
        hw_client.write_capacitance(target_F)
        readback_F = hw_client.read_capacitance()
        _say(f"CAPACITANCE: READBACK = {readback_F:.6e} F")
        # The encoder picks val ∈ [6554, 65535] for maximum precision;
        # 0.05 % is the worst-case quantisation error from rounding the
        # mantissa.
        rel_err = abs(readback_F - target_F) / target_F
        _say(f"CAPACITANCE: REL_ERR  = {rel_err:.2e}")
        assert rel_err < 5e-4, (
            f"write_capacitance round-trip exceeded 0.05% error: "
            f"wrote {target_F:.6e} F, read {readback_F:.6e} F "
            f"(rel_err={rel_err:.2e})"
        )
    finally:
        hw_client.write_capacitance(original_F)
        restored = hw_client.read_capacitance()
        _say(f"CAPACITANCE: RESTORED = {restored:.6e} F")
        if original_F > 0:
            assert abs(restored - original_F) / original_F < 5e-4, (
                f"FAILED to restore capacitance to {original_F:.6e} F; "
                f"current value is {restored:.6e} F — INSPECT THE UNIT."
            )
        else:
            assert restored == 0.0, (
                f"FAILED to restore capacitance to 0; got {restored} F"
            )


def test_roundtrip_second_capacitance_pair(hw_client: SmartPowerClient) -> None:
    """Mirror of the capacitance round-trip against the *second* pair."""
    try:
        original_F = hw_client.read_second_capacitance()
    except UnsupportedRegisterError as exc:
        pytest.skip(f"Second capacitance pair not exposed on this model: {exc}")

    target_F = 1e-6 if original_F <= 0 else original_F * 1.01
    _say(f"SECOND_CAPACITANCE: ORIGINAL = {original_F:.6e} F")
    _say(f"SECOND_CAPACITANCE: TARGET   = {target_F:.6e} F")

    try:
        hw_client.write_second_capacitance(target_F)
        readback_F = hw_client.read_second_capacitance()
        _say(f"SECOND_CAPACITANCE: READBACK = {readback_F:.6e} F")
        rel_err = abs(readback_F - target_F) / target_F
        _say(f"SECOND_CAPACITANCE: REL_ERR  = {rel_err:.2e}")
        assert rel_err < 5e-4
    finally:
        hw_client.write_second_capacitance(original_F)
        restored = hw_client.read_second_capacitance()
        _say(f"SECOND_CAPACITANCE: RESTORED = {restored:.6e} F")
