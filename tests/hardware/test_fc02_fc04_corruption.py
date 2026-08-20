"""Regression check for the customer-reported FC 0x02 → FC 0x04 firmware bug.

A single Modbus FC 0x02 (Read Discrete Inputs) of 16 bits at address
``0x0000`` corrupts the *next* FC 0x04 (Read Input Registers) response
covering ``0x2000``..``0x2007``. Four specific addresses come back at
fixed wrong values:

- ``0x2001`` ``INPUT_REG_RESET``           → ``1``
- ``0x2004`` ``INPUT_REG_PA_ENABLE_MASK``  → ``0``
- ``0x2005`` ``INPUT_REG_PA_MAX_WORK_SET`` → ``0``
- ``0x2006`` ``INPUT_REG_OUT_100_P``       → ``0``

The neighbouring ``0x2007`` ``INPUT_REG_OUT_100_I`` is unaffected. Idle
time between the FC 0x02 and the FC 0x04 (up to 1.5 s) does not clear
the state — only the next FC 0x04 does. Polling FC 0x04 alone is clean.
Zero CRC errors, zero exception responses, FC 0x08 diagnostic counters
all zero.

These tests reproduce the defect by issuing the customer's exact PDU
shape against the raw transport (so register-metadata batching cannot
mask the bug) and assert that the four target addresses are *not*
corrupted. Pass means the firmware is fixed; fail means the bug is
still present, with a signature dump the firmware team can use without
re-running with ``-v``.

The failure message distinguishes:

- *exact-signature match* — after-values equal the customer's reported
  ``{resetId=1, paEnableMask=0, paMaxWorkingSet=0, fullScaleOutputPowerRaw=0}``;
  this is the bug the customer reported.
- *different corruption pattern* — the same four addresses (or some of
  them) changed, but to other values; this is *related* but a distinct
  defect and should be triaged separately.

Caveat: if the baseline values of the four target registers happen to
coincide with the corruption signature (e.g. ``resetId`` already 1 and
the other three already 0 on the connected unit), the bug is
undetectable on that unit because the corrupted response is bit-for-bit
identical to the correct one. This is logged by the baseline fixture if
detected.

Gated by ``@pytest.mark.hardware_firmware_bug`` — requires both
``--hardware`` and ``--allow-firmware-bug-tests``. Read-only on the wire.
"""

from __future__ import annotations

import logging
import time

import pytest

from smartpower_modbus import SmartPowerClient

from .conftest import FC02_BASELINE_COUNT, FC02_BASELINE_START

pytestmark = pytest.mark.hardware_firmware_bug

logger = logging.getLogger(__name__)


# Customer's exact FC 0x02 probe: 16 discrete inputs starting at 0x0000.
FC02_PROBE_ADDR = 0x0000
FC02_PROBE_COUNT = 16

# Addresses the customer reported as corrupted, with their library names.
TARGETS: dict[int, str] = {
    0x2001: "INPUT_REG_RESET",
    0x2004: "INPUT_REG_PA_ENABLE_MASK",
    0x2005: "INPUT_REG_PA_MAX_WORK_SET",
    0x2006: "INPUT_REG_OUT_100_P",
}
UNAFFECTED_NEIGHBOUR_ADDR = 0x2007
UNAFFECTED_NEIGHBOUR_NAME = "INPUT_REG_OUT_100_I"

# Exact values the customer reported as the corrupted-state response for
# the four target addresses. Used to classify a positive detection as
# either the exact reported defect or a related-but-different pattern.
CORRUPTION_SIGNATURE: dict[int, int] = {
    0x2001: 1,
    0x2004: 0,
    0x2005: 0,
    0x2006: 0,
}


def _offset(addr: int) -> int:
    """Translate a register address to its index inside the read window."""
    return addr - FC02_BASELINE_START


def _flush_pending_corruption(hw_client: SmartPowerClient) -> None:
    """Issue one FC 0x04 to consume any latent FC 0x02 corruption state.

    The bug's persistence model is: each FC 0x02 arms the *next* FC 0x04
    to come back wrong, and only that FC 0x04 clears the arm. A prior
    test in the session (``test_register_sweep`` runs FC 0x02 over
    discrete-input ranges as part of ``dump()``) can leave the slave in
    the armed state. We absorb that here so each parametrised case
    starts from a known-clean slate and the trigger we measure is the
    FC 0x02 *we* issue, not whatever leaked in from earlier.
    """
    hw_client._transport.read_input(FC02_BASELINE_START, count=FC02_BASELINE_COUNT)


def _changed_targets(baseline: list[int], after: list[int]) -> list[int]:
    """Return target addresses whose value differs between baseline and after."""
    return [addr for addr in TARGETS if baseline[_offset(addr)] != after[_offset(addr)]]


def _matches_customer_signature(after: list[int]) -> bool:
    """True iff the after-window matches the customer's reported corrupt values
    at every target address.
    """
    return all(
        after[_offset(addr)] == expected
        for addr, expected in CORRUPTION_SIGNATURE.items()
    )


def _format_window_dump(baseline: list[int], after: list[int]) -> list[str]:
    """Render the full 0x2000..0x2007 window with per-register annotation.

    A buffer-overwrite firmware bug could touch addresses outside
    ``TARGETS`` (e.g. ``0x2000``, ``0x2002``, ``0x2003``). Dumping every
    register in the window — not only the four targets — keeps that
    visible to whoever reads the failure log.
    """
    lines: list[str] = []
    for off in range(FC02_BASELINE_COUNT):
        addr = FC02_BASELINE_START + off
        before = baseline[off]
        now = after[off]
        changed = before != now
        marker = "*" if changed else " "

        if addr in TARGETS:
            name = TARGETS[addr]
            expected = CORRUPTION_SIGNATURE[addr]
            if not changed:
                note = (
                    f"customer target — UNCHANGED "
                    f"(corruption signature wanted 0x{expected:04X})"
                )
            elif now == expected:
                note = (
                    f"customer target — matches customer signature "
                    f"(0x{expected:04X})"
                )
            else:
                note = (
                    f"customer target — CHANGED but to 0x{now:04X}, "
                    f"customer signature was 0x{expected:04X}"
                )
        elif addr == UNAFFECTED_NEIGHBOUR_ADDR:
            name = UNAFFECTED_NEIGHBOUR_NAME
            note = (
                "unaffected neighbour per customer (ok)"
                if not changed
                else "unaffected neighbour per customer — ALSO CHANGED (unexpected)"
            )
        else:
            name = ""
            note = "(not called out in customer report)"

        lines.append(
            f"  {marker} 0x{addr:04X} {name:<26s} 0x{before:04X} -> 0x{now:04X}  ({note})"
        )
    return lines


def _format_corruption_failure(
    *,
    device_info: dict[str, str],
    model_value: str,
    baseline: list[int],
    after: list[int],
    delay_ms: int,
    rep: int | None,
    reps_total: int | None,
    extra_tail: str | None = None,
) -> str:
    """Build a detailed multi-line message embedded in pytest.fail()."""
    exact = _matches_customer_signature(after)
    verdict = (
        "EXACT MATCH — values equal the customer's reported corruption "
        "signature {resetId=1, paEnableMask=0, paMaxWorkingSet=0, "
        "fullScaleOutputPowerRaw=0}."
        if exact
        else "DIFFERENT PATTERN — the four target addresses (or some of them) "
        "changed, but the after-values do NOT match the customer's "
        "reported signature. This is a related but distinct defect; "
        "do not assume the same root cause."
    )

    lines = [
        f"FC 0x02 → FC 0x04 corruption reproduced on {model_value} "
        f"(PRODUCT_CODE={device_info.get('product_code', '?')}, "
        f"firmware={device_info.get('revision', '?')}).",
        f"Trigger: FC 0x02 read of 0x{FC02_PROBE_ADDR:04X}/{FC02_PROBE_COUNT} bits.",
        f"Verdict: {verdict}",
        "",
        f"Full window 0x{FC02_BASELINE_START:04X}.."
        f"0x{FC02_BASELINE_START + FC02_BASELINE_COUNT - 1:04X}:",
    ]
    lines.extend(_format_window_dump(baseline, after))

    rep_str = "n/a" if rep is None else f"{rep + 1}/{reps_total}"
    lines.append("")
    lines.append(f"Inter-frame idle: {delay_ms} ms. Repetition: {rep_str}.")
    if extra_tail:
        lines.append("")
        lines.append(extra_tail)
    return "\n".join(lines)


# ---------- Tests ----------


@pytest.mark.parametrize("delay_ms", [0, 50, 250, 1500])
def test_fc02_then_fc04_corrupts_targets(
    hw_client: SmartPowerClient,
    fc02_corruption_baseline: list[int],
    hw_device_info: dict[str, str],
    delay_ms: int,
) -> None:
    """Issue one FC 0x02, wait ``delay_ms``, then re-read 0x2000..0x2007.

    Asserts that the four target addresses match the baseline (i.e. the
    bug is *gone*). The persistence claim is exercised by parametrising
    the inter-frame idle between FC 0x02 and FC 0x04: the customer
    observed corruption at idle = 0..1500 ms, so all four cases must
    pass on a fixed firmware.
    """
    _flush_pending_corruption(hw_client)

    hw_client._transport.read_discretes(FC02_PROBE_ADDR, count=FC02_PROBE_COUNT)
    if delay_ms:
        time.sleep(delay_ms / 1000.0)
    after = hw_client._transport.read_input(
        FC02_BASELINE_START, count=FC02_BASELINE_COUNT,
    )

    changed = _changed_targets(fc02_corruption_baseline, after)
    if changed:
        pytest.fail(_format_corruption_failure(
            device_info=hw_device_info,
            model_value=hw_client._require_model().value,
            baseline=fc02_corruption_baseline,
            after=after,
            delay_ms=delay_ms,
            rep=None,
            reps_total=None,
        ))


_DETERMINISM_REPS = 5


@pytest.mark.parametrize("rep", range(_DETERMINISM_REPS))
def test_fc02_corruption_is_deterministic_over_repeats(
    hw_client: SmartPowerClient,
    fc02_corruption_baseline: list[int],
    hw_device_info: dict[str, str],
    rep: int,
) -> None:
    """Back-to-back FC 0x02 → FC 0x04 pairs at zero idle must all keep
    the four target addresses unchanged.

    The customer's report is that the corruption is deterministic, not a
    one-shot. If a buggy unit happens to produce a clean response on one
    iteration but corrupted ones on the others, that itself is useful
    diagnostic data — each repetition is its own pytest node so the run
    log preserves the per-iteration outcome.
    """
    _flush_pending_corruption(hw_client)

    hw_client._transport.read_discretes(FC02_PROBE_ADDR, count=FC02_PROBE_COUNT)
    after = hw_client._transport.read_input(
        FC02_BASELINE_START, count=FC02_BASELINE_COUNT,
    )

    changed = _changed_targets(fc02_corruption_baseline, after)
    if changed:
        pytest.fail(_format_corruption_failure(
            device_info=hw_device_info,
            model_value=hw_client._require_model().value,
            baseline=fc02_corruption_baseline,
            after=after,
            delay_ms=0,
            rep=rep,
            reps_total=_DETERMINISM_REPS,
        ))


def test_no_drift_without_fc02_control(
    hw_client: SmartPowerClient,
    fc02_corruption_baseline: list[int],
    hw_device_info: dict[str, str],
) -> None:
    """Control: two FC 0x04 reads with no FC 0x02 between them must
    return identical values at the four target addresses.

    These four registers are configuration-ish (``RESET``,
    ``PA_ENABLE_MASK``, ``PA_MAX_WORK_SET``, ``OUT_100_P``) and don't
    update from telemetry, so a real drift between two adjacent FC 0x04
    reads would mean either: (a) someone else is writing to the slave,
    or (b) the corruption mechanism is not actually FC 0x02-gated.
    Either invalidates the parametrised reproducer below — failing this
    test first is a fast signal that the bench setup is not what we
    think it is.
    """
    _flush_pending_corruption(hw_client)

    first = hw_client._transport.read_input(
        FC02_BASELINE_START, count=FC02_BASELINE_COUNT,
    )
    second = hw_client._transport.read_input(
        FC02_BASELINE_START, count=FC02_BASELINE_COUNT,
    )

    drift = [addr for addr in TARGETS if first[_offset(addr)] != second[_offset(addr)]]
    if drift:
        pytest.fail(
            "Control case failed: target registers drifted between two "
            "FC 0x04 reads with no FC 0x02 between them — the corruption "
            "model in this test file does not apply to the connected "
            f"unit. Drifted addresses: {[f'0x{a:04X}' for a in drift]}. "
            f"first={[f'0x{v:04X}' for v in first]}, "
            f"second={[f'0x{v:04X}' for v in second]}. "
            f"Model={hw_client._require_model().value}, "
            f"PRODUCT_CODE={hw_device_info.get('product_code', '?')}."
        )

    # Belt-and-suspenders: also assert against the session baseline. If
    # someone else is writing to the slave between baseline capture and
    # this test, this assertion catches it.
    baseline_drift = [
        addr for addr in TARGETS
        if fc02_corruption_baseline[_offset(addr)] != first[_offset(addr)]
    ]
    if baseline_drift:
        logger.warning(
            "Target registers drifted between session baseline and this "
            "test's first read (no FC 0x02 between). Addresses: %s. "
            "Either another master is on the bus, or the slave's "
            "configuration changed mid-session.",
            [f"0x{a:04X}" for a in baseline_drift],
        )


def test_corruption_is_consumed_by_one_fc04(
    hw_client: SmartPowerClient,
    fc02_corruption_baseline: list[int],
    hw_device_info: dict[str, str],
) -> None:
    """Verify the customer's "one FC 0x04 consumes the state" claim.

    Sequence: flush → FC 0x02 → FC 0x04 (the corrupted one) →
    FC 0x04 (must be clean again, no FC 0x02 between).

    Three outcomes:

    - First FC 0x04 *is* corrupted, second FC 0x04 is clean: matches
      the customer's report. Test passes.
    - First FC 0x04 is clean (i.e. the bug isn't triggering on this
      unit at all): nothing to assert about consumption — skip rather
      than fail; the other parametrised tests already cover the
      "is the bug present" question per delay.
    - First FC 0x04 *is* corrupted, second FC 0x04 is *also* corrupted:
      the corruption is sticky / not consumed by one FC 0x04 — a worse
      / different defect than the customer reported. Fail with a
      dedicated message so firmware can branch on this.
    """
    _flush_pending_corruption(hw_client)

    hw_client._transport.read_discretes(FC02_PROBE_ADDR, count=FC02_PROBE_COUNT)
    first_after = hw_client._transport.read_input(
        FC02_BASELINE_START, count=FC02_BASELINE_COUNT,
    )
    second_after = hw_client._transport.read_input(
        FC02_BASELINE_START, count=FC02_BASELINE_COUNT,
    )

    first_changed = _changed_targets(fc02_corruption_baseline, first_after)
    if not first_changed:
        pytest.skip(
            "First FC 0x04 after FC 0x02 was clean — bug is not "
            "triggering on this unit, so the 'state is consumed by one "
            "FC 0x04' claim has nothing to verify. The four parametrised "
            "test_fc02_then_fc04_corrupts_targets nodes already report "
            "the bug's presence."
        )

    second_changed = _changed_targets(fc02_corruption_baseline, second_after)
    if second_changed:
        pytest.fail(_format_corruption_failure(
            device_info=hw_device_info,
            model_value=hw_client._require_model().value,
            baseline=fc02_corruption_baseline,
            after=second_after,
            delay_ms=0,
            rep=None,
            reps_total=None,
            extra_tail=(
                "STICKY CORRUPTION — a second FC 0x04 with no intervening "
                "FC 0x02 is also corrupt. The customer reported the state "
                "is consumed by exactly one FC 0x04, so this is a worse / "
                "different defect: route to firmware as a separate finding. "
                "The window above is the SECOND FC 0x04 (i.e. the one that "
                "should have been clean)."
            ),
        ))
