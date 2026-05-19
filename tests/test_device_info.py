"""FC 0x2B / 0x0E device-info, model identification, and probe.

The SmartPower firmware exposes vendor / product-code / revision via
Modbus FC 0x2B (Read Device Identification). The library uses this to
auto-identify which ``SmartPowerModel`` is on the wire. These tests
cover ``read_device_info``, ``read_product_code``, ``identify_model``,
the structural ``probe_model`` fallback, and the deprecated
``probe_branch`` alias.
"""

from __future__ import annotations

import pytest

from smartpower_modbus import (
    IllegalFunctionError,
    Register,
    SmartPowerClient,
    SmartPowerModel,
    UnsupportedFirmwareBranchError,
)
from smartpower_modbus.branches import FirmwareBranch

from .conftest import FakeSerialClient, _Call, _DeviceInfoResp, _ExcResp, _Resp

# ---------- probe_model / probe_branch ----------

def test_probe_model_returns_ext_group_when_address_succeeds(client, fake_client):
    fake_client.script("read_input_registers", _Resp(registers=[123]))
    candidates = client.probe_model()
    assert SmartPowerModel.SOLO in candidates
    assert SmartPowerModel.GEN_1_0 in candidates
    assert SmartPowerModel.GEN_2_0 not in candidates


def test_probe_model_returns_non_ext_group_on_illegal_address(client, fake_client):
    fake_client.script("read_input_registers", _ExcResp(0x02))
    candidates = client.probe_model()
    assert SmartPowerModel.GEN_2_0 in candidates
    assert SmartPowerModel.GEN_1_5 in candidates
    assert SmartPowerModel.SOLO not in candidates


def test_probe_branch_is_deprecated_alias_returning_firmware_branches(client, fake_client):
    """Old probe_branch() still works but emits a DeprecationWarning and
    returns FirmwareBranch values (the legacy contract)."""
    import warnings as _w
    fake_client.script("read_input_registers", _Resp(registers=[7]))
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        candidates = client.probe_branch()
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert FirmwareBranch.SNGLE_MODULE_5540_LF_MF_EXTPA_SIMPLE in candidates
    assert FirmwareBranch.PRODUCTION_PHASE_1_FAST_1_15_BASE in candidates


# ---------- read_device_info / read_product_code ----------

def test_read_device_info_returns_three_named_fields(client, fake_client):
    fake_client.script("read_device_information", _DeviceInfoResp({
        0: b"Ultraflex Power",
        1: b"55370112",
        2: b"1.2.3",
    }))
    info = client.read_device_info()
    assert info == {
        "vendor": "Ultraflex Power",
        "product_code": "55370112",
        "revision": "1.2.3",
    }
    call = fake_client.calls[-1]
    assert call.name == "read_device_information"
    # read_code=0x01 (basic) returns the three mandatory objects.
    assert call.kwargs["read_code"] == 0x01
    assert call.kwargs["object_id"] == 0
    assert call.kwargs["slave"] == 1


def test_read_product_code_uses_basic_conformity_request(client, fake_client):
    """SmartPower firmware ships with MEI_DEV_ONE_OBJ_ENA disabled, so
    the slave rejects read_code=0x04 with Modbus exception 0x02.
    ``read_product_code()`` must use read_code=0x01 (basic), starting at
    object_id=1 (PRODUCT_CODE) so the fall-through returns at least the
    product code."""
    fake_client.script(
        "read_device_information",
        _DeviceInfoResp({1: b"55370112", 2: b"1.0.0"}),
    )
    code = client.read_product_code()
    assert code == "55370112"
    call = fake_client.calls[-1]
    assert call.kwargs["read_code"] == 0x01  # basic, not 0x04
    assert call.kwargs["object_id"] == 1


def test_read_product_code_strips_null_bytes_and_whitespace(client, fake_client):
    fake_client.script(
        "read_device_information",
        _DeviceInfoResp({1: b"55370112\x00\x00"}),
    )
    assert client.read_product_code() == "55370112"


# ---------- identify_model ----------

def test_identify_model_resolves_known_product_code(client, fake_client):
    """Returning '55370250' (the GEN_1_0 product code, stored without the
    '0x' prefix the firmware emits) resolves to SmartPowerModel.GEN_1_0."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"55370250"}))
    # Client is currently configured for GEN_2_0; identify should disagree
    # with the configured model but still RETURN the device-reported one.
    result = client.identify_model()
    assert result is SmartPowerModel.GEN_1_0
    # The disagreement must NOT silently overwrite an explicitly-set model.
    assert client.model is SmartPowerModel.GEN_2_0


def test_identify_model_accepts_firmware_literal_0x_prefix(client, fake_client):
    """ProductionPhase1 firmware reports its product code with a literal
    "0x" prefix in the C-string."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"0x55370250"}))
    assert client.identify_model() is SmartPowerModel.GEN_1_0


def test_identify_model_raises_on_unknown_product_code(client, fake_client):
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"DEADBEEF"}))
    with pytest.raises(UnsupportedFirmwareBranchError, match="DEADBEEF"):
        client.identify_model()


def test_identify_model_raises_on_unsupported_function_code(client, fake_client):
    """Device returns exception 0x01 (illegal function) when it doesn't
    implement FC 0x2B at all."""
    fake_client.script("read_device_information", _ExcResp(0x01))
    with pytest.raises(IllegalFunctionError):
        client.identify_model()


def test_auto_identify_at_connect_when_model_is_none(fake_client):
    """Constructing with model=None should cause connect() to auto-detect
    via FC 0x2B and set self.model."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"55370111"}))
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01, retries=0)
    c._transport._client = fake_client
    assert c.model is None
    c.connect()
    try:
        assert c.model is SmartPowerModel.GEN_1_5
    finally:
        c.close()


def test_no_model_set_blocks_high_level_read(fake_client):
    """If model is None and connect hasn't run yet, calling read() must
    raise rather than guess."""
    from smartpower_modbus import SmartPowerError
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01)
    c._transport._client = fake_client
    # Don't call connect(); model is still None.
    with pytest.raises(SmartPowerError, match="model is not set"):
        c.read(Register.INPUT_REG_OUT_P)


def test_identify_model_sets_model_when_none(fake_client):
    """Calling identify_model() on a client without a configured model
    must set self.model to the resolved value."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"55400400"}))
    c = SmartPowerClient(port="dummy", slave_id=1, timeout=0.01, retries=0)
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    try:
        result = c.identify_model()
        assert result is SmartPowerModel.SOLO
        assert c.model is SmartPowerModel.SOLO
    finally:
        c.close()


def test_identify_model_warns_on_mismatch_but_keeps_explicit_model(client, fake_client, caplog):
    """If the user explicitly configured a model and the device reports
    a different one, the explicit value wins but a warning is logged."""
    fake_client.script("read_device_information", _DeviceInfoResp({1: b"55370111"}))
    # Client fixture was constructed with GEN_2_0; device reports GEN_1_5.
    import logging
    with caplog.at_level(logging.WARNING, logger="smartpower_modbus.client"):
        result = client.identify_model()
    assert result is SmartPowerModel.GEN_1_5
    assert client.model is SmartPowerModel.GEN_2_0  # unchanged
    assert any("disagrees" in r.message for r in caplog.records)


# ---------- MEI transport behaviour ----------

def test_read_device_information_uses_kwarg_shim(fake_client):
    """Bug 2: the MEI request must honour pymodbus's ``slave=`` /
    ``device_id=`` / ``unit=`` drift the same way regular FC calls do.

    We swap in a fake whose ``read_device_information`` accepts only
    ``device_id=`` (modelled after pymodbus 3.8+). If the transport
    bypassed the shim with a hard-coded ``slave=``, this would fail
    with a TypeError that bubbled up as ModbusCommError."""
    class _DeviceIdOnlyClient(FakeSerialClient):
        def read_device_information(self, *, read_code=0x01, object_id=0, device_id=None, **kw):
            self.calls.append(_Call(
                "read_device_information", -1,
                {"read_code": read_code, "object_id": object_id, "device_id": device_id, **kw},
            ))
            return self._next(
                "read_device_information",
                _DeviceInfoResp({1: b"55370112"}),
            )

    fc = _DeviceIdOnlyClient()
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=0,
    )
    c._transport._client = fc
    c._transport.connect()
    c._connected = True
    code = c.read_product_code()
    assert code == "55370112"
    # Confirm the shim picked the right kwarg name.
    assert fc.calls[-1].kwargs["device_id"] == 1
    c.close()


def test_read_device_information_retries_on_timeout(fake_client):
    """Bug 2: the MEI path must use the same retry loop as the other
    reads — two timeouts then success returns the third response."""
    from pymodbus.exceptions import ModbusIOException
    c = SmartPowerClient(
        port="dummy", slave_id=1,
        model=SmartPowerModel.GEN_2_0,
        timeout=0.01, retries=2,
    )
    c._transport._client = fake_client
    c._transport.connect()
    c._connected = True
    fake_client.script(
        "read_device_information",
        ModbusIOException("response timeout"),
        ModbusIOException("response timeout"),
        _DeviceInfoResp({1: b"55370112"}),
    )
    code = c.read_product_code()
    assert code == "55370112"
    n_calls = sum(1 for call in fake_client.calls if call.name == "read_device_information")
    assert n_calls == 3
    c.close()
