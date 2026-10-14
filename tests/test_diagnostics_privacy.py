"""Exported diagnostics must not identify the account or the physical lock."""

import json
from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eufy_lock_ble.const import DOMAIN
from custom_components.eufy_lock_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.eufy_lock_ble.eufy_ble.history import LockRecord
from custom_components.eufy_lock_ble.history import HistoryTracker


async def test_identifiers_and_nested_tokens_are_redacted(hass: HomeAssistant) -> None:
    private = {
        "address": "D1:E2:F3:04:15:26",
        "serial": "T8510PRIVATE00001",
        "name": "A private door name",
        "admin_user_id": "a unique account identifier",
        "lock_public_key": "a unique lock public key",
        "nickname": "private-account-nickname",
        "short_user_id": 54321,
        "email": "private-person@example.invalid",
        "password": "a private password",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=private,
        options={"nested": {"auth_token": "private-token-value"}},
    )
    entry.runtime_data = SimpleNamespace(
        info=SimpleNamespace(firmware="2.0.2.3", hardware="test"),
        is_locked=True, battery=50, connected=True, link_up=True,
        connected_since=None, disconnects=0, keep_connected=True, rssi=-60,
        last_seen=None, last_update_success=True,
        params={0xA1: b"\x02", 0xEE: b"a private unknown field"},
        history_status="ready", history=HistoryTracker(),
    )
    entry.runtime_data.history.ingest(
        (LockRecord(1792000000, 260, "private-event-user", "private-household-name", b"private raw history"),),
        complete=True, now=1792000001,
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    encoded = json.dumps(result)
    for value in [*private.values(), "private-token-value", "a private unknown field",
                  "private-event-user", "private-household-name", "private raw history"]:
        assert str(value) not in encoded
    assert all(value == "**REDACTED**" for value in result["entry"]["data"].values())
    assert result["state"]["firmware"] == "2.0.2.3"
    assert result["state"]["params"] == {"sound_volume": "medium"}
