"""Config and options flow tests."""

from __future__ import annotations

from unittest.mock import patch

from bleak.exc import BleakError
from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS, CONF_COUNTRY, CONF_EMAIL, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eufy_lock_ble.config_flow import MANUAL
from custom_components.eufy_lock_ble.const import (
    CONF_ADMIN_USER_ID,
    CONF_LOCK_PUBLIC_KEY,
    CONF_MANUAL_CREDENTIALS,
    CONF_NICKNAME,
    CONF_SERIAL,
    CONF_SHORT_USER_ID,
    CONF_SKIP_VERIFY,
    DOMAIN,
    OPT_KEEP_CONNECTED,
    OPT_POLL_INTERVAL,
)
from custom_components.eufy_lock_ble.coordinator import LockUnreachableError
from custom_components.eufy_lock_ble.eufy_ble import SessionError, SessionInfo

from .fake_lock import NICK, FakeLock
from .helpers import ADDRESS, entry_data, make_service_info

pytestmark = pytest.mark.usefixtures("mock_bluetooth")

PROBE = "custom_components.eufy_lock_ble.config_flow.async_probe_lock"


def _form_input(lock: FakeLock, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        CONF_NAME: "Back Door",
        CONF_SERIAL: lock.serial,
        CONF_ADMIN_USER_ID: lock.admin.hex(),
        CONF_LOCK_PUBLIC_KEY: lock.public_key_hex,
        CONF_SHORT_USER_ID: 0,
        CONF_NICKNAME: NICK,
        CONF_SKIP_VERIFY: True,
    }
    data.update(overrides)
    return data


async def _skip_account(hass: HomeAssistant, result):
    """Take the 'enter credentials manually' shortcut of the account step."""
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_EMAIL: "x@example.com",
            CONF_PASSWORD: "x",
            CONF_COUNTRY: "CA",
            CONF_MANUAL_CREDENTIALS: True,
        },
    )


def _info(lock: FakeLock) -> SessionInfo:
    return SessionInfo(b"\x00" * 16, None, "2.0.4.2", lock.serial, "1.2")


async def test_bluetooth_discovery_creates_entry(hass: HomeAssistant) -> None:
    lock = FakeLock()
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_service_info(),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "account"
    result = await _skip_account(hass, result)
    assert result["step_id"] == "credentials"
    assert result["description_placeholders"] == {"address": ADDRESS}

    with patch(PROBE, return_value=(_info(lock), True)) as probe:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form_input(lock, **{CONF_SKIP_VERIFY: False})
        )
    assert probe.call_count == 1
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Back Door"
    assert result["data"] == entry_data(lock)
    assert result["result"].unique_id == ADDRESS


async def test_discovery_already_configured(hass: HomeAssistant) -> None:
    lock = FakeLock()
    MockConfigEntry(domain=DOMAIN, data=entry_data(lock), unique_id=ADDRESS).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=make_service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({CONF_ADMIN_USER_ID: "abc"}, {CONF_ADMIN_USER_ID: "invalid_admin_user_id"}),
        ({CONF_LOCK_PUBLIC_KEY: "00" * 64}, {CONF_LOCK_PUBLIC_KEY: "invalid_public_key"}),
        ({CONF_SERIAL: "T8"}, {CONF_SERIAL: "invalid_serial"}),
    ],
)
async def test_credential_validation_errors(
    hass: HomeAssistant, overrides: dict[str, object], expected: dict[str, str]
) -> None:
    lock = FakeLock()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=make_service_info()
    )
    result = await _skip_account(hass, result)
    with patch(PROBE) as probe:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form_input(lock, **overrides)
        )
    probe.assert_not_called()
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (LockUnreachableError("gone"), "not_in_range"),
        (SessionError("rejected"), "handshake_rejected"),
        (BleakError("boom"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        (RuntimeError("?"), "unknown"),
    ],
)
async def test_probe_failures(hass: HomeAssistant, side_effect: Exception, error: str) -> None:
    lock = FakeLock()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=make_service_info()
    )
    result = await _skip_account(hass, result)
    with patch(PROBE, side_effect=side_effect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form_input(lock, **{CONF_SKIP_VERIFY: False})
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    # the user can still save by skipping the probe
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_input(lock))
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_probe_serial_mismatch(hass: HomeAssistant) -> None:
    lock = FakeLock()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=make_service_info()
    )
    result = await _skip_account(hass, result)
    other = SessionInfo(b"\x00" * 16, None, "2.0.4.2", "T8510Q1599999999", "1.2")
    with patch(PROBE, return_value=(other, True)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _form_input(lock, **{CONF_SKIP_VERIFY: False})
        )
    assert result["errors"] == {CONF_SERIAL: "serial_mismatch"}


async def test_user_flow_picks_discovered_device(hass: HomeAssistant) -> None:
    lock = FakeLock()
    with patch(
        "custom_components.eufy_lock_ble.config_flow.async_discovered_service_info",
        return_value=[make_service_info(), make_service_info("11:22:33:44:55:66", "SomethingElse")],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    choices = result["data_schema"].schema[CONF_ADDRESS].container
    assert set(choices) == {ADDRESS, MANUAL}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "account"
    result = await _skip_account(hass, result)
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_input(lock))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS


async def test_user_flow_manual_address(hass: HomeAssistant) -> None:
    lock = FakeLock()
    with patch(
        "custom_components.eufy_lock_ble.config_flow.async_discovered_service_info", return_value=[]
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: MANUAL}
    )
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "nope"}
    )
    assert result["errors"] == {CONF_ADDRESS: "invalid_address"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: "aa-bb-cc-dd-ee-ff"}
    )
    assert result["step_id"] == "account"
    result = await _skip_account(hass, result)
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _form_input(lock))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["result"].unique_id == ADDRESS


async def test_options_flow(hass: HomeAssistant) -> None:
    lock = FakeLock()
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(lock), unique_id=ADDRESS)
    entry.add_to_hass(hass)
    with patch("custom_components.eufy_lock_ble.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPT_KEEP_CONNECTED: False, OPT_POLL_INTERVAL: 120},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {OPT_KEEP_CONNECTED: False, OPT_POLL_INTERVAL: 120}
