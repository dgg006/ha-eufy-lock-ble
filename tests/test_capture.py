"""Bounded disconnected captures, annotation-only controls and local file output."""

import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest

from custom_components.eufy_lock_ble.capture import CAPTURE_SECONDS, AdvertisementCapture

from .fake_lock import FakeLock
from .helpers import ADDRESS, FakeConnector, make_ble_device, make_service_info
from .test_init import setup_entry


async def test_every_report_markers_and_atomic_file(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    received = []
    cancelled = []

    def register(_hass, callback, address):
        assert address == ADDRESS
        received.append(callback)
        return lambda: cancelled.append(True)

    capture = AdvertisementCapture(hass, ADDRESS, lambda: None)
    with patch('custom_components.eufy_lock_ble.capture.bluetooth_api.async_register_advertisement_callback',
               register, create=True):
        await capture.async_start()
        first_filename = capture.filename
        await capture.async_start()  # repeated presses do not lose the active log
        assert capture.filename == first_filename
        assert capture.mode == 'every_delivered_advertisement'
        info = make_service_info()
        received[0](info)
        received[0](info)  # unchanged payload still counts as a delivered report
        capture.mark('fingerprint')
        received[0](make_service_info(address='11:22:33:44:55:66'))
        assert capture.reports == 2 and capture.distinct_payloads == 1
        assert capture.marker_count == 1
        await capture.async_stop()
        assert capture.status == 'saved' and not capture.active and cancelled == [True]
        filename = Path(hass.config.path('eufy_lock_ble_captures', capture.filename))
        data = json.loads(filename.read_text(encoding='utf-8'))
        assert data['stop_reason'] == 'user_stopped'
        assert data['report_count'] == 2 and data['marker_count'] == 1
        assert data['entries'][0]['manufacturer_data']['0802'].startswith('aabbccddeeff')
        assert data['entries'][-1]['marker'] == 'fingerprint'
        assert data['periodic_polling_paused']
        received[0](info)
        assert capture.reports == 2
        assert not list(filename.parent.glob('.capture-*'))
        await capture.async_shutdown()


async def test_fallback_checkpoint_and_shutdown(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    capture = AdvertisementCapture(hass, ADDRESS, lambda: None)
    with (
        patch('custom_components.eufy_lock_ble.capture.bluetooth_api.async_register_advertisement_callback',
              None, create=True),
        patch('custom_components.eufy_lock_ble.capture.bluetooth.async_register_callback',
              return_value=lambda: None) as register,
    ):
        await capture.async_start()
        assert capture.mode == 'changed_advertisements_only'
        assert register.call_args.args[3].value == 'passive'
        capture._on_changed_advertisement(make_service_info(), None)
        await capture._async_checkpoint()
        await capture.async_shutdown()
        data = json.loads(Path(hass.config.path('eufy_lock_ble_captures', capture.filename)).read_text())
        assert data['stop_reason'] == 'integration_unloaded' and data['report_count'] == 1


async def test_capture_auto_stops_and_reports_save_error(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    capture = AdvertisementCapture(hass, ADDRESS, lambda: None)
    with patch('custom_components.eufy_lock_ble.capture.bluetooth.async_register_callback',
               return_value=lambda: None):
        await capture.async_start()
        assert (capture.ends_at - capture.started_at).total_seconds() == CAPTURE_SECONDS
        capture._auto_stop(dt_util.utcnow())
        await hass.async_block_till_done()
        assert not capture.active and capture.status == 'saved'
        await capture.async_start()
        with patch('custom_components.eufy_lock_ble.capture._write_capture', side_effect=OSError):
            await capture.async_stop()
        assert capture.status == 'error' and capture.last_error == 'OSError'
        await capture.async_shutdown()


async def test_capture_buttons_pause_polling_and_never_actuate(hass, mock_bluetooth, tmp_path):
    hass.config.config_dir = str(tmp_path)
    lock = FakeLock(clock=dt_util.now)
    connector = FakeConnector(lock)
    with (
        patch('custom_components.eufy_lock_ble.coordinator.establish_connection', connector),
        patch('homeassistant.components.bluetooth.async_ble_device_from_address', return_value=make_ble_device()),
    ):
        entry = await setup_entry(hass, lock, keep_connected=False)
        before = list(lock.received)
        await hass.services.async_call('button', 'press',
            {'entity_id': 'button.back_door_start_bluetooth_capture'}, blocking=True)
        coordinator = entry.runtime_data
        assert coordinator.capture.active
        await coordinator.async_refresh()
        assert lock.received == before
        assert len(connector.clients) == 1 and not connector.current.is_connected
        await hass.services.async_call('button', 'press',
            {'entity_id': 'button.back_door_mark_fingerprint_test'}, blocking=True)
        assert coordinator.capture.marker_count == 1 and lock.received == before
        await hass.services.async_call('button', 'press',
            {'entity_id': 'button.back_door_stop_bluetooth_capture'}, blocking=True)
        assert coordinator.capture.status == 'saved'
        await hass.config_entries.async_unload(entry.entry_id)


async def test_capture_requires_disconnected_mode(hass, mock_bluetooth):
    from unittest.mock import MagicMock

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.eufy_lock_ble.const import DOMAIN
    from custom_components.eufy_lock_ble.coordinator import EufyLockCoordinator
    from custom_components.eufy_lock_ble.eufy_ble import LockCredentials

    lock = FakeLock()
    coordinator = EufyLockCoordinator(hass, MockConfigEntry(domain=DOMAIN), ADDRESS, MagicMock(spec=LockCredentials))
    assert coordinator.keep_connected
    with pytest.raises(HomeAssistantError, match='Disable Keep connected'):
        await coordinator.async_start_capture()
    assert not coordinator.capture.active
    assert lock.received == []


async def test_capture_report_limit_stops_collection(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    capture = AdvertisementCapture(hass, ADDRESS, lambda: None)
    with (
        patch('custom_components.eufy_lock_ble.capture.MAX_REPORTS', 2),
        patch('custom_components.eufy_lock_ble.capture.bluetooth.async_register_callback',
              return_value=lambda: None),
    ):
        await capture.async_start()
        for _ in range(5):
            capture._on_advertisement(make_service_info())
        await hass.async_block_till_done()
        assert not capture.active and capture.reports == 2
        data = json.loads(Path(hass.config.path('eufy_lock_ble_captures', capture.filename)).read_text())
        assert data['stop_reason'] == 'report_limit'
        await capture.async_shutdown()


async def test_failed_initial_save_releases_subscription_and_poll_pause(hass):
    capture = AdvertisementCapture(hass, ADDRESS, lambda: None)
    removed = []
    with (
        patch('custom_components.eufy_lock_ble.capture.bluetooth.async_register_callback',
              return_value=lambda: removed.append(True)),
        patch('custom_components.eufy_lock_ble.capture._write_capture', side_effect=OSError),
        pytest.raises(OSError),
    ):
        await capture.async_start()
    assert not capture.active and capture.status == 'error' and removed == [True]
    await capture.async_shutdown()
