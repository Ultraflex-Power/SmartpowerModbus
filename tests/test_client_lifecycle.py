"""Connect/close/context-manager behaviour and deprecated constructor knobs.

Covers what ``SmartPowerClient`` does around the boundary of a session —
the ``with`` block, port cleanup when auto-identification fails, and the
deprecated ``branch=`` kwarg that callers may still pass.
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    IllegalFunctionError,
    SmartPowerClient,
    SmartPowerError,
    SmartPowerModel,
)
from smartpower_modbus.branches import FirmwareBranch

from .conftest import _DeviceInfoResp, _ExcResp

# ---------- Deprecated branch= kwarg + model=/branch= validation ----------

def test_deprecated_branch_kwarg_still_works(fake_client):
    """SmartPowerClient(branch=...) is deprecated but still resolves."""
    import warnings as _w
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        c = SmartPowerClient(
            port="dummy", slave_id=1,
            branch=FirmwareBranch.MEGA_MAIN,   # deprecated form
            timeout=0.01, retries=0,
        )
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert c.model is SmartPowerModel.GEN_2_0


def test_client_rejects_both_model_and_branch():
    with pytest.raises(TypeError):
        SmartPowerClient(
            port="dummy", slave_id=1,
            model=SmartPowerModel.GEN_2_0,
            branch=FirmwareBranch.MEGA_MAIN,
        )


def test_client_requires_model_arg():
    """It is allowed to omit ``model=`` — that triggers auto-detection on
    connect. But not when combined with the deprecated ``branch=``."""
    # Constructor without model= is valid (auto-detect later).
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01)
    assert c.model is None


# ---------- Context manager ----------

def test_context_manager_connects_and_closes(fake_client):
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=0,
    )
    c._transport._client = fake_client
    with c as ctx:
        assert ctx is c
        assert c._connected is True
    assert c._connected is False


# ---------- Connect-time cleanup on auto-identify failure ----------

def test_connect_closes_transport_when_auto_identify_fails(fake_client):
    """Bug 1: if auto-identification raises during connect(), the
    serial transport must be closed and ``_connected`` reset, otherwise
    Python's ``with`` won't call __exit__ (since __enter__ never
    returned) and the port leaks."""
    # Device reports a product code the library doesn't recognise →
    # identify_model() raises UnsupportedFirmwareBranchError.
    fake_client.script(
        "read_device_information",
        _DeviceInfoResp({1: b"DEADBEEF"}),
    )
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01, retries=0)
    c._transport._client = fake_client
    with pytest.raises(SmartPowerError):
        c.connect()
    assert c._connected is False
    assert fake_client.close_count == 1


def test_connect_closes_transport_when_auto_identify_hits_illegal_function(fake_client):
    """Same as above but the device returns Modbus exception 0x01
    (slave doesn't support FC 0x2B) instead of an unknown code."""
    fake_client.script("read_device_information", _ExcResp(0x01))
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01, retries=0)
    c._transport._client = fake_client
    with pytest.raises(IllegalFunctionError):
        c.connect()
    assert c._connected is False
    assert fake_client.close_count == 1
