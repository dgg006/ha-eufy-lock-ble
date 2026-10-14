"""User-name edits use lock IDs, remain private and preserve existing options."""

from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eufy_lock_ble.const import (
    DOMAIN,
    OPT_KEEP_CONNECTED,
    OPT_POLL_INTERVAL,
    OPT_USER_ALIASES,
)
from custom_components.eufy_lock_ble.eufy_ble.history import parse_history_page
from custom_components.eufy_lock_ble.history import HistoryTracker

from .fake_lock import FakeLock
from .helpers import ADDRESS, FakeConnector, entry_data, make_ble_device
from .test_history import record_bytes


async def test_edit_alias_preserve_clear_and_reject_bad_names(hass):
    lock = FakeLock()
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(lock), unique_id=ADDRESS,
                            options={OPT_USER_ALIASES: {'0002': 'Existing alias'}})
    entry.add_to_hass(hass)
    with patch('custom_components.eufy_lock_ble.async_setup_entry', return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
    history = HistoryTracker()
    history.ingest(parse_history_page(b'\x41' + record_bytes()).records,
                   complete=True, now=1792000001)
    entry.runtime_data = SimpleNamespace(history=history)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result['flow_id'], {
        OPT_KEEP_CONNECTED: True, OPT_POLL_INTERVAL: 300, 'edit_user_names': True,
    })
    assert result['step_id'] == 'select_user'
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'user_id': '0001'})
    assert result['step_id'] == 'user_name'
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'display_name': 'x' * 65})
    assert result['errors'] == {'display_name': 'invalid_user_name'}
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'display_name': '  Local member  '})
    assert result['type'] == FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_USER_ALIASES] == {'0001': 'Local member', '0002': 'Existing alias'}
    assert 'edit_user_names' not in entry.options
    # Ordinary connection edits must not silently delete aliases.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result['flow_id'], {
        OPT_KEEP_CONNECTED: False, OPT_POLL_INTERVAL: 120,
    })
    assert entry.options[OPT_USER_ALIASES]['0001'] == 'Local member'
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result['flow_id'], {
        OPT_KEEP_CONNECTED: False, OPT_POLL_INTERVAL: 120, 'edit_user_names': True,
    })
    result = await hass.config_entries.options.async_configure(result['flow_id'], {'user_id': '0001'})
    await hass.config_entries.options.async_configure(result['flow_id'], {'display_name': ''})
    assert entry.options[OPT_USER_ALIASES] == {'0002': 'Existing alias'}


async def test_alias_updates_sensors_events_and_diagnostics_without_reconnect(hass, mock_bluetooth):
    import json

    from custom_components.eufy_lock_ble.diagnostics import async_get_config_entry_diagnostics

    from .test_init import setup_entry

    now = int(dt_util.utcnow().timestamp())
    lock = FakeLock(clock=dt_util.now)
    lock.history_records = [record_bytes(timestamp=now - 1)]
    connector = FakeConnector(lock)
    with (
        patch('custom_components.eufy_lock_ble.coordinator.establish_connection', connector),
        patch('homeassistant.components.bluetooth.async_ble_device_from_address', return_value=make_ble_device()),
    ):
        entry = await setup_entry(hass, lock, keep_connected=False)
        coordinator = entry.runtime_data
        record_id = coordinator.history.latest.record_id
        hass.config_entries.async_update_entry(entry, options={
            OPT_KEEP_CONNECTED: False, OPT_USER_ALIASES: {'0001': 'Private display alias'},
        })
        await hass.async_block_till_done()
        assert entry.runtime_data is coordinator
        assert len(connector.clients) == 1
        state = hass.states.get('sensor.back_door_last_unlock_user')
        assert state.state == 'Private display alias'
        assert state.attributes['reported_user_name'] == 'Member A'
        assert state.attributes['user_id'] == '0001'
        assert coordinator.history.latest.record_id == record_id
        assert coordinator.history.latest.nickname == 'Member A'
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert 'Private display alias' not in json.dumps(diagnostics)
        assert diagnostics['entry']['options'][OPT_USER_ALIASES] == '**REDACTED**'
        events = []
        remove = hass.bus.async_listen('eufy_lock_ble_event', events.append)
        lock.history_records.insert(0, record_bytes(timestamp=now, code=259))
        coordinator._history_retry_at = 0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert events[0].data['user_name'] == 'Private display alias'
        assert events[0].data['reported_user_name'] == 'Member A'
        remove()
        await hass.config_entries.async_unload(entry.entry_id)
