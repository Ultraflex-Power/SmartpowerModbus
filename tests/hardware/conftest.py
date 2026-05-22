"""Fixtures for the live-hardware test suite.

The opt-in CLI flags and skip plumbing live in the top-level
``tests/conftest.py`` (pytest only honours ``pytest_addoption`` from the
rootdir-level conftest). This file defines the fixtures that the marker'd
tests use to talk to the real device.

A single ``hw_client`` is opened at session scope and reused across every
test — the SmartPower bus is half-duplex and slow, so re-opening the port
between tests would add noticeable latency for little benefit.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator

import pytest

from smartpower_modbus import (
    DEFAULT_BAUDRATE,
    Register,
    SmartPowerClient,
    SmartPowerModel,
)
from smartpower_modbus.units import TemperatureUnit

logger = logging.getLogger(__name__)


# ---------- Config-derived fixtures ----------

@pytest.fixture(scope="session")
def hw_port(pytestconfig) -> str:
    port = pytestconfig.getoption("--port")
    if not port:
        # The top-level conftest already auto-skips hardware tests when
        # --port is missing, but we still raise here so an accidental
        # direct use of this fixture in a non-marker'd test is loud.
        pytest.skip("--port is required for hardware tests")
    return port


@pytest.fixture(scope="session")
def hw_baud(pytestconfig) -> int:
    return pytestconfig.getoption("--baud") or DEFAULT_BAUDRATE


@pytest.fixture(scope="session")
def hw_slave_id(pytestconfig) -> int:
    return pytestconfig.getoption("--slave-id")


@pytest.fixture(scope="session")
def hw_model(pytestconfig) -> SmartPowerModel | None:
    """Configured model, or ``None`` to auto-identify via FC 0x2B on connect."""
    name = pytestconfig.getoption("--model")
    if name is None:
        return None
    return SmartPowerModel.from_name(name)


@pytest.fixture(scope="session")
def hw_temperature_unit(pytestconfig) -> TemperatureUnit:
    return TemperatureUnit.from_name(pytestconfig.getoption("--temperature-unit"))


@pytest.fixture(scope="session")
def hw_timeout(pytestconfig) -> float:
    return pytestconfig.getoption("--hw-timeout")


@pytest.fixture(scope="session")
def hw_retries(pytestconfig) -> int:
    return pytestconfig.getoption("--hw-retries")


# ---------- The live client ----------

@pytest.fixture(scope="session")
def hw_client(
    hw_port: str,
    hw_baud: int,
    hw_slave_id: int,
    hw_model: SmartPowerModel | None,
    hw_temperature_unit: TemperatureUnit,
    hw_timeout: float,
    hw_retries: int,
) -> Iterator[SmartPowerClient]:
    """Session-scoped ``SmartPowerClient`` against the configured port.

    Auto-identifies the model via FC 0x2B if ``--model`` was not passed.
    Closes the port on session teardown so a follow-up test run (or a
    manual ``smartpower-cli`` invocation) finds the port free.
    """
    logger.info(
        "Opening live SmartPower client on port=%s baud=%d slave=%d model=%s",
        hw_port, hw_baud, hw_slave_id,
        hw_model.value if hw_model else "auto",
    )
    client = SmartPowerClient(
        port=hw_port,
        slave_id=hw_slave_id,
        model=hw_model,
        baudrate=hw_baud,
        timeout=hw_timeout,
        retries=hw_retries,
        temperature_unit=hw_temperature_unit,
    )
    # connect() runs the FC 0x2B auto-id when model is None and closes
    # the port itself if that fails, so a clean ``with`` block isn't
    # required for safety here — but it keeps the fixture readable.
    with client:
        logger.info("Connected; resolved model=%s", client._require_model().value)
        yield client


# ---------- Per-test safety guard ----------

@pytest.fixture(autouse=True)
def _hw_safety_guard(request, hw_client: SmartPowerClient) -> None:
    """Refuse to run a hardware test while the unit is actively heating.

    Only fires for tests carrying a ``hardware*`` marker (so non-hardware
    fixtures elsewhere aren't accidentally pulled in). The check uses
    ``INPUT_HEAT``, the firmware's view of "we are commanded to heat
    *right now*" — which is more authoritative than the coil setpoint.

    ``INPUT_ENABLE`` is *not* guarded: the output stage being energised
    without a heating command is a normal bench-test state, and the
    allowlisted writes / read sweeps do nothing that depends on
    ENABLE=0. If you want a stricter "fully de-energised" gate, clear
    ``COIL_ENABLE`` from the operator console before running the suite.
    """
    if not any(
        m.name in ("hardware", "hardware_write", "hardware_fault")
        for m in request.node.iter_markers()
    ):
        return

    heat = hw_client.read(Register.INPUT_HEAT)
    if heat:
        pytest.fail(
            f"Refusing to run hardware test {request.node.name!r} while unit "
            f"is heating: INPUT_HEAT={heat}. Stop heating before running "
            f"the suite (clear COIL_HEAT).",
        )


# ---------- Helpers reused across files ----------

@pytest.fixture()
def _drain_deprecation_warnings():
    """Silence the library's own deprecation warnings within a test.

    Several composite paths still surface DeprecationWarning (the
    ``branch=`` alias, ``probe_branch``, …). The hardware suite never
    uses those — but if a future test does, we want the failure on the
    *behaviour*, not on the warning filter.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield
