"""Config and options flows for eufy Smart Lock (BLE).

Setup order: pick the lock (Bluetooth discovery, list, or manual address) ->
log into the eufy account to pull the per-lock secrets (or choose to type them)
-> review the credentials and test the handshake -> done.  The account
password is only held in memory for the duration of the flow.
"""

from __future__ import annotations

import logging
from typing import Any

from bleak.exc import BleakError
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_NAME,
    CONF_PASSWORD,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
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
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    OPT_KEEP_CONNECTED,
    OPT_POLL_INTERVAL,
    OPT_USER_ALIASES,
)
from .coordinator import LockUnreachableError, async_probe_lock
from .eufy_ble import LockCredentials, SessionError
from .eufy_ble.client import InvalidCredential
from .eufy_ble.cloud import (
    AccountLockedOut,
    CaptchaRequired,
    CloudLock,
    EufyCloud,
    EufyCloudError,
    InvalidAuth,
    InvalidVerifyCode,
    VerifyCodeRequired,
)

_LOGGER = logging.getLogger(__name__)

MANUAL = "manual"
NAME_PREFIXES = ("T85",)
EUFY_MANUFACTURER_ID = 0x0802


def looks_like_eufy_lock(info: BluetoothServiceInfoBleak) -> bool:
    """Heuristic used to pre-filter discovered devices in the user step."""
    name = info.name or ""
    return name.startswith(NAME_PREFIXES) or EUFY_MANUFACTURER_ID in info.manufacturer_data


def _serial_from_name(name: str | None) -> str:
    """``T8510_XXXXXXXX`` -> ``T8510`` prefix hint (user fills the rest)."""
    if name and "_" in name:
        return name.split("_", 1)[0]
    return ""


class EufyLockConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise."""
        self._address: str | None = None
        self._name: str | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._cloud: EufyCloud | None = None
        self._account: dict[str, Any] = {}
        self._captcha_id: str | None = None
        self._captcha_image: str = ""
        self._locks: dict[str, CloudLock] = {}
        self._prefill: dict[str, Any] = {}

    # ---------------------------------------------------------------- discovery
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a device found by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_account()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user pick a discovered lock or enter an address."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address == MANUAL:
                return await self.async_step_manual()
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._address = address
            info = self._discovered.get(address)
            self._name = (info.name if info else None) or address
            return await self.async_step_account()

        current = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current or not looks_like_eufy_lock(info):
                continue
            self._discovered[info.address] = info

        choices = {
            addr: f"{info.name or 'eufy lock'} ({addr})" for addr, info in self._discovered.items()
        }
        choices[MANUAL] = "Enter a Bluetooth address manually"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(choices)}),
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual address entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            if len(address) != 17 or address.count(":") != 5:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                self._address = address
                self._name = address
                return await self.async_step_account()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )

    # ------------------------------------------------------------ eufy account
    def _show_account(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        assert self._address is not None
        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL, default=self._account.get(CONF_EMAIL, "")): str,
                vol.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_COUNTRY,
                    default=self._account.get(CONF_COUNTRY) or self.hass.config.country or "US",
                ): selector.CountrySelector(),
                vol.Optional(CONF_MANUAL_CREDENTIALS, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="account",
            data_schema=schema,
            errors=errors or {},
            description_placeholders={"address": self._address, "name": self._name or ""},
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the eufy account, or let the user type the secrets."""
        if user_input is None:
            return self._show_account()
        if user_input.get(CONF_MANUAL_CREDENTIALS):
            return await self.async_step_credentials()
        self._account = {
            CONF_EMAIL: user_input[CONF_EMAIL].strip(),
            CONF_COUNTRY: user_input.get(CONF_COUNTRY) or "US",
        }
        self._cloud = EufyCloud(
            async_get_clientsession(self.hass),
            self._account[CONF_EMAIL],
            user_input[CONF_PASSWORD],
            self._account[CONF_COUNTRY],
            now=dt_util.now,
        )
        return await self._async_cloud_login()

    async def _async_cloud_login(
        self, *, verify_code: str | None = None, captcha: tuple[str, str] | None = None
    ) -> ConfigFlowResult:
        assert self._cloud is not None
        try:
            await self._cloud.login(verify_code=verify_code, captcha=captcha)
        except CaptchaRequired as req:
            self._captcha_id = req.captcha_id
            self._captcha_image = req.image_data_url
            return self._show_captcha({"base": "invalid_captcha"} if captcha else None)
        except VerifyCodeRequired:
            return self._show_verify_code()
        except InvalidVerifyCode:
            return self._show_verify_code({"base": "invalid_verify_code"})
        except InvalidAuth:
            self._cloud = None
            return self._show_account({"base": "invalid_auth"})
        except AccountLockedOut:
            self._cloud = None
            return self._show_account({"base": "locked_out"})
        except EufyCloudError as err:
            _LOGGER.debug("eufy cloud login failed: %s", type(err).__name__)
            self._cloud = None
            return self._show_account({"base": "cannot_connect_cloud"})
        return await self._async_cloud_inventory()

    def _show_captcha(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema({vol.Required(CONF_CAPTCHA_CODE): str}),
            errors=errors or {},
            description_placeholders={"captcha": f"![captcha]({self._captcha_image})"},
        )

    async def async_step_captcha(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Captcha answer."""
        if user_input is None or self._cloud is None or self._captcha_id is None:
            return self._show_captcha()
        return await self._async_cloud_login(
            captcha=(self._captcha_id, user_input[CONF_CAPTCHA_CODE])
        )

    def _show_verify_code(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="verify_code",
            data_schema=vol.Schema({vol.Required(CONF_VERIFY_CODE): str}),
            errors=errors or {},
            description_placeholders={"email": self._account.get(CONF_EMAIL, "")},
        )

    async def async_step_verify_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """E-mailed verification code."""
        if user_input is None or self._cloud is None:
            return self._show_verify_code()
        return await self._async_cloud_login(verify_code=user_input[CONF_VERIFY_CODE])

    async def _async_cloud_inventory(self) -> ConfigFlowResult:
        assert self._cloud is not None and self._address is not None
        try:
            locks = await self._cloud.list_locks()
        except EufyCloudError as err:
            _LOGGER.debug("eufy cloud inventory failed: %s", type(err).__name__)
            return self._show_account({"base": "cannot_connect_cloud"})
        finally:
            # nothing from the account is kept beyond this point
            self._cloud = None
        if not locks:
            return self._show_account({"base": "no_locks"})
        match = next((lock for lock in locks if lock.matches_address(self._address)), None)
        if match is None and len(locks) == 1:
            match = locks[0]
        if match is not None:
            self._apply_lock(match)
            return await self.async_step_credentials()
        self._locks = {lock.serial: lock for lock in locks}
        return await self.async_step_select_lock()

    async def async_step_select_lock(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Several locks on the account and none matched the Bluetooth address."""
        if user_input is not None and user_input[CONF_LOCK] in self._locks:
            self._apply_lock(self._locks[user_input[CONF_LOCK]])
            return await self.async_step_credentials()
        choices = {
            serial: f"{lock.name} ({serial}{', BT ' + lock.bt_mac if lock.bt_mac else ''})"
            for serial, lock in self._locks.items()
        }
        return self.async_show_form(
            step_id="select_lock",
            data_schema=vol.Schema({vol.Required(CONF_LOCK): vol.In(choices)}),
            description_placeholders={"address": self._address or ""},
        )

    def _apply_lock(self, lock: CloudLock) -> None:
        self._prefill = {
            CONF_NAME: lock.name,
            CONF_SERIAL: lock.serial,
            CONF_ADMIN_USER_ID: lock.admin_user_id,
            CONF_LOCK_PUBLIC_KEY: lock.public_key or "",
            CONF_SHORT_USER_ID: lock.short_user_id,
            CONF_NICKNAME: lock.nickname,
        }

    # -------------------------------------------------------------- credentials
    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Review the per-device secrets and (optionally) test the handshake."""
        assert self._address is not None
        errors: dict[str, str] = {}
        defaults: dict[str, Any] = {
            CONF_NAME: self._name or "eufy lock",
            CONF_SERIAL: _serial_from_name(self._name),
            CONF_ADMIN_USER_ID: "",
            CONF_LOCK_PUBLIC_KEY: "",
            CONF_SHORT_USER_ID: 0,
            CONF_NICKNAME: "",
            CONF_SKIP_VERIFY: False,
        }
        defaults.update(self._prefill)

        if user_input is not None:
            defaults.update(user_input)
            credentials: LockCredentials | None = None
            try:
                credentials = LockCredentials.from_strings(
                    serial=user_input[CONF_SERIAL],
                    admin_user_id=user_input[CONF_ADMIN_USER_ID],
                    lock_public_key=user_input[CONF_LOCK_PUBLIC_KEY],
                    short_user_id=user_input.get(CONF_SHORT_USER_ID, 0),
                    nickname=user_input.get(CONF_NICKNAME, ""),
                )
            except InvalidCredential as err:
                errors[err.field] = err.code

            if credentials is not None and not user_input.get(CONF_SKIP_VERIFY):
                try:
                    info, _state = await async_probe_lock(self.hass, self._address, credentials)
                except LockUnreachableError:
                    errors["base"] = "not_in_range"
                except SessionError:
                    errors["base"] = "handshake_rejected"
                except (BleakError, TimeoutError) as err:
                    _LOGGER.debug("Probe failed: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error probing the lock")
                    errors["base"] = "unknown"
                else:
                    if info.serial and info.serial != credentials.serial:
                        errors[CONF_SERIAL] = "serial_mismatch"

            if credentials is not None and not errors:
                data = {
                    CONF_ADDRESS: self._address,
                    CONF_NAME: user_input[CONF_NAME].strip() or self._name,
                    CONF_SERIAL: credentials.serial,
                    CONF_ADMIN_USER_ID: credentials.admin_user_id.hex(),
                    CONF_LOCK_PUBLIC_KEY: user_input[CONF_LOCK_PUBLIC_KEY].strip(),
                    CONF_SHORT_USER_ID: credentials.short_user_id,
                    CONF_NICKNAME: credentials.nickname,
                }
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
                vol.Required(CONF_SERIAL, default=defaults[CONF_SERIAL]): str,
                vol.Required(CONF_ADMIN_USER_ID, default=defaults[CONF_ADMIN_USER_ID]): str,
                vol.Required(CONF_LOCK_PUBLIC_KEY, default=defaults[CONF_LOCK_PUBLIC_KEY]): str,
                vol.Optional(CONF_SHORT_USER_ID, default=defaults[CONF_SHORT_USER_ID]): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=0xFFFF)
                ),
                vol.Optional(CONF_NICKNAME, default=defaults[CONF_NICKNAME]): str,
                vol.Optional(CONF_SKIP_VERIFY, default=defaults[CONF_SKIP_VERIFY]): bool,
            }
        )
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            errors=errors,
            description_placeholders={"address": self._address},
        )

    # ------------------------------------------------------------------ options
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EufyLockOptionsFlow:
        """Return the options flow."""
        return EufyLockOptionsFlow()


class EufyLockOptionsFlow(OptionsFlow):
    """Connection settings and local user display names."""

    def __init__(self) -> None:
        self._pending_options: dict[str, Any] = {}
        self._known_users: dict[str, str] = {}
        self._selected_user = ""

    def _collect_users(self) -> dict[str, str]:
        coordinator = getattr(self.config_entry, "runtime_data", None)
        users: dict[str, str] = {}
        if coordinator is not None:
            for record in coordinator.history.records:
                if record.user_id:
                    users.setdefault(record.user_id, record.nickname or record.user_id)
        for user_id in self.config_entry.options.get(OPT_USER_ALIASES, {}):
            users.setdefault(user_id, user_id)
        return users

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Preserve aliases when saving ordinary connection settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._pending_options = {
                **self.config_entry.options,
                OPT_KEEP_CONNECTED: user_input[OPT_KEEP_CONNECTED],
                OPT_POLL_INTERVAL: user_input[OPT_POLL_INTERVAL],
            }
            if not user_input.get("edit_user_names"):
                return self.async_create_entry(title="", data=self._pending_options)
            self._known_users = self._collect_users()
            if self._known_users:
                return await self.async_step_select_user()
            errors["base"] = "no_history_users"

        options = self._pending_options or self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_KEEP_CONNECTED,
                    default=options.get(OPT_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED),
                ): bool,
                vol.Required(
                    OPT_POLL_INTERVAL,
                    default=options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL)
                ),
                vol.Optional("edit_user_names", default=False): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_select_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a user from observed IDs or previously configured aliases."""
        errors = {}
        if user_input is not None:
            if user_input["user_id"] in self._known_users:
                self._selected_user = user_input["user_id"]
                return await self.async_step_user_name()
            errors["base"] = "unknown_user"
        aliases = self._pending_options.get(OPT_USER_ALIASES, {})
        choices = [
            selector.SelectOptionDict(
                value=user_id,
                label=(f"{aliases[user_id]} ({reported})" if aliases.get(user_id) else reported),
            )
            for user_id, reported in sorted(self._known_users.items())
        ]
        return self.async_show_form(
            step_id="select_user",
            data_schema=vol.Schema({vol.Required("user_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(options=choices, mode=selector.SelectSelectorMode.DROPDOWN)
            )}), errors=errors,
        )

    async def async_step_user_name(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit or remove one alias without renaming anything on the lock/cloud."""
        errors = {}
        aliases = dict(self._pending_options.get(OPT_USER_ALIASES, {}))
        if user_input is not None:
            name = user_input.get("display_name", "").strip()
            if len(name) > 64 or any(ord(char) < 32 for char in name):
                errors["display_name"] = "invalid_user_name"
            else:
                if name:
                    aliases[self._selected_user] = name
                else:
                    aliases.pop(self._selected_user, None)
                self._pending_options[OPT_USER_ALIASES] = aliases
                return self.async_create_entry(title="", data=self._pending_options)
        return self.async_show_form(
            step_id="user_name",
            description_placeholders={
                "reported_name": self._known_users[self._selected_user],
                "user_id": self._selected_user,
            },
            data_schema=vol.Schema({
                vol.Optional("display_name", default=aliases.get(self._selected_user, "")):
                    selector.TextSelector(),
            }), errors=errors,
        )
