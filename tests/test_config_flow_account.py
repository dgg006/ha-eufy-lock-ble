"""Config flow: fetching the lock secrets from the eufy account."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_COUNTRY, CONF_EMAIL, CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import voluptuous as vol

from custom_components.eufy_lock_ble.const import (
    CONF_ADMIN_USER_ID,
    CONF_CAPTCHA_CODE,
    CONF_LOCK,
    CONF_LOCK_PUBLIC_KEY,
    CONF_MANUAL_CREDENTIALS,
    CONF_NICKNAME,
    CONF_SERIAL,
    CONF_SHORT_USER_ID,
    CONF_SKIP_VERIFY,
    CONF_VERIFY_CODE,
    DOMAIN,
)

from . import cloud_fixtures as cf
from .fake_lock import FakeLock
from .helpers import ADDRESS, make_service_info

pytestmark = pytest.mark.usefixtures("mock_bluetooth")

ACCOUNT_INPUT = {CONF_EMAIL: cf.EMAIL, CONF_PASSWORD: "hunter2", CONF_COUNTRY: "CA"}


def _defaults(result) -> dict[str, object]:
    """Default values of the shown form, keyed by field name."""
    out: dict[str, object] = {}
    for key in result["data_schema"].schema:
        default = getattr(key, "default", None)
        if default is not None and default is not vol.UNDEFINED:
            out[str(key)] = default() if callable(default) else default
    return out


async def _start(hass: HomeAssistant, info=None):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=info or make_service_info(),
    )


async def test_discovery_starts_with_account(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["step_id"] == "account"
    assert result["description_placeholders"]["address"] == ADDRESS
    assert _defaults(result)[CONF_COUNTRY] in ("US", hass.config.country)


async def test_manual_shortcut(hass: HomeAssistant) -> None:
    lock = FakeLock()
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ACCOUNT_INPUT, CONF_MANUAL_CREDENTIALS: True}
    )
    assert result["step_id"] == "credentials"
    assert _defaults(result)[CONF_ADMIN_USER_ID] == ""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Back Door",
            CONF_SERIAL: lock.serial,
            CONF_ADMIN_USER_ID: lock.admin.hex(),
            CONF_LOCK_PUBLIC_KEY: lock.public_key_hex,
            CONF_SHORT_USER_ID: 0,
            CONF_NICKNAME: "",
            CONF_SKIP_VERIFY: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_login_prefills_credentials(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    cf.mock_domain(aioclient_mock)
    bodies = cf.mock_login_sequence(aioclient_mock, [cf.login_ok()])
    cf.mock_inventory(aioclient_mock, bodies, locks=2)

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT_INPUT)
    # two locks on the account, but one matches the advertised address -> straight to review
    assert result["step_id"] == "credentials"
    defaults = _defaults(result)
    assert defaults[CONF_NAME] == "Back Door"
    assert defaults[CONF_SERIAL] == cf.LOCK_SN
    assert defaults[CONF_ADMIN_USER_ID] == cf.ADMIN
    assert defaults[CONF_LOCK_PUBLIC_KEY] == cf.PUBLIC_KEY
    assert defaults[CONF_SHORT_USER_ID] == 0
    assert defaults[CONF_NICKNAME] == "abc***"

    submit = dict(defaults)
    submit[CONF_SKIP_VERIFY] = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], submit)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SERIAL] == cf.LOCK_SN
    assert result["data"][CONF_ADMIN_USER_ID] == cf.ADMIN
    assert CONF_PASSWORD not in result["data"]
    assert CONF_EMAIL not in result["data"]


async def test_two_factor_then_select_lock(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    cf.mock_domain(aioclient_mock)
    bodies = cf.mock_login_sequence(aioclient_mock, [cf.login_need_verify(), cf.login_ok()])
    cf.mock_inventory(aioclient_mock, bodies, locks=2)

    # an address that matches none of the account's locks
    result = await _start(hass, make_service_info("99:88:77:66:55:44", "T8510_DEADBEEF"))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT_INPUT)
    assert result["step_id"] == "verify_code"
    assert result["description_placeholders"]["email"] == cf.EMAIL
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VERIFY_CODE: "123456"}
    )
    assert result["step_id"] == "select_lock"
    choices = result["data_schema"].schema[CONF_LOCK].container
    assert set(choices) == {cf.LOCK_SN, cf.OTHER_SN}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCK: cf.OTHER_SN}
    )
    assert result["step_id"] == "credentials"
    assert _defaults(result)[CONF_SERIAL] == cf.OTHER_SN


async def test_captcha_loop(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    cf.mock_domain(aioclient_mock)
    bodies = cf.mock_login_sequence(
        aioclient_mock,
        [cf.login_need_captcha("cap-1"), cf.login_need_captcha("cap-2"), cf.login_ok()],
    )
    cf.mock_inventory(aioclient_mock, bodies)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT_INPUT)
    assert result["step_id"] == "captcha"
    assert result["description_placeholders"]["captcha"].startswith(
        "![captcha](data:image/png;base64,"
    )
    assert result["errors"] == {}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CAPTCHA_CODE: "wrong"}
    )
    assert result["step_id"] == "captcha"
    assert result["errors"] == {"base": "invalid_captcha"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CAPTCHA_CODE: "right"}
    )
    assert result["step_id"] == "credentials"


@pytest.mark.parametrize(
    ("code", "error"),
    [(26055, "invalid_auth"), (100028, "locked_out"), (999, "cannot_connect_cloud")],
)
async def test_login_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, code: int, error: str
) -> None:
    cf.mock_domain(aioclient_mock)
    cf.mock_login_sequence(aioclient_mock, [{"code": code, "msg": "nope", "data": None}])
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT_INPUT)
    assert result["step_id"] == "account"
    assert result["errors"] == {"base": error}
    assert _defaults(result)[CONF_EMAIL] == cf.EMAIL


async def test_account_without_locks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    cf.mock_domain(aioclient_mock)
    cf.mock_login_sequence(aioclient_mock, [cf.login_ok()])
    aioclient_mock.post(f"{cf.API_BASE}/v2/house/station_list", json=cf.ok([]))
    aioclient_mock.post(f"{cf.API_BASE}/v2/house/device_list", json=cf.ok([]))
    aioclient_mock.post(f"{cf.API_BASE}/v1/house/list", json=cf.ok([]))
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ACCOUNT_INPUT)
    assert result["step_id"] == "account"
    assert result["errors"] == {"base": "no_locks"}
