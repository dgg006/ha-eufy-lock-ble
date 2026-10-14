"""History framing, pagination, replay prevention and private access details."""

from datetime import datetime

from homeassistant.util import dt as dt_util
import pytest

from custom_components.eufy_lock_ble.eufy_ble import LockSession, ProtocolError, SessionError
from custom_components.eufy_lock_ble.eufy_ble.history import (
    build_history_request,
    parse_history_page,
)
from custom_components.eufy_lock_ble.eufy_ble.protocol import Tag, build_tlv
from custom_components.eufy_lock_ble.history import HistoryTracker

from .fake_lock import FakeLock
from .test_client import make_credentials, wire


def record_bytes(timestamp=1792000000, code=260, user=b"\x00\x01", name="Member A"):
    return build_tlv([
        (Tag.A1, timestamp.to_bytes(4, "little")),
        (Tag.A2, code.to_bytes(2, "big")),
        (Tag.A3, user), (Tag.A4, name.encode()),
    ]).ljust(40, b"\0")


def test_known_and_unknown_records_and_nonempty_final_page():
    raw = record_bytes(name="Member B")
    unknown = record_bytes(code=65530, user=b"", name="")
    page = parse_history_page(b"\x41" + raw + unknown)
    assert page.complete and len(page.records) == 2
    record = page.records[0]
    assert record.code == 260 and record.method == "fingerprint" and record.action == "unlock"
    assert record.user_id == "0001" and record.nickname == "Member B"
    assert record.as_dict()["occurred_at"].endswith("+00:00")
    assert "raw" not in record.as_dict()
    assert record.as_dict(include_raw=True)["raw"] == raw.hex()
    assert page.records[1].event_type == "unknown" and page.records[1].method is None
    assert page.records[1].code == 65530


@pytest.mark.parametrize("code,method", [(257, "manual"), (259, "passcode"), (261, "app")])
def test_method_does_not_guess_a_physical_key_user(code, method):
    record = parse_history_page(b"\x41" + record_bytes(code=code, user=b"", name="")).records[0]
    assert record.method == method
    assert record.as_dict()["user_name"] is None


@pytest.mark.parametrize("payload", [b"", b"\x42", b"\x00\xa1", b"\x41" + b"\xff" * 40])
def test_reject_truncated_or_invalid_history(payload):
    with pytest.raises(ProtocolError):
        parse_history_page(payload)


def test_request_matches_official_layout():
    assert build_history_request(3, 0x12345678) == bytes.fromhex("a1020300a20478563412")
    with pytest.raises(ProtocolError):
        build_history_request(-1, 0)


def test_baseline_overlap_restarts_old_records_and_last_unlock():
    tracker = HistoryTracker()
    first = parse_history_page(b"\x00" + record_bytes(timestamp=1000)).records
    assert tracker.ingest(first, complete=False, now=1001) == []
    assert not tracker.initialized
    assert tracker.ingest(first, complete=True, now=1001) == []
    new = parse_history_page(b"\x41" + record_bytes(timestamp=1002, name="Member B")).records
    assert tracker.ingest(new, complete=True, now=1003) == list(new)
    assert tracker.ingest(new, complete=True, now=1003) == []
    old = parse_history_page(b"\x41" + record_bytes(timestamp=900)).records
    assert tracker.ingest(old, complete=True, now=1003) == []
    locked = parse_history_page(b"\x41" + record_bytes(timestamp=1004, code=262)).records
    tracker.ingest(locked, complete=True, now=1005)
    assert tracker.latest.code == 262
    assert tracker.latest_unlock.nickname == "Member B"
    # Loading a cache must not turn old records into startup events.
    restarted = HistoryTracker()
    assert restarted.ingest(new, complete=False, now=1005) == []
    assert restarted.ingest(locked, complete=True, now=1005) == []


def test_delayed_records_are_retained_and_future_records_do_not_fire():
    tracker = HistoryTracker()
    baseline = parse_history_page(b"\x41" + record_bytes(timestamp=1000)).records
    tracker.ingest(baseline, complete=True, now=1500)
    delayed = parse_history_page(b"\x41" + record_bytes(timestamp=1100)).records
    assert tracker.ingest(delayed, complete=True, now=1500) == list(delayed)
    future = parse_history_page(b"\x41" + record_bytes(timestamp=2000)).records
    assert tracker.ingest(future, complete=True, now=1500) == []
    assert tracker.latest.timestamp == 2000
    legitimate = parse_history_page(b"\x41" + record_bytes(timestamp=1200)).records
    assert tracker.ingest(legitimate, complete=True, now=1500) == list(legitimate)


async def test_encrypted_history_read_does_not_actuate_or_delete():
    lock = FakeLock()
    lock.history_records = [record_bytes()]
    session = wire(lock, lambda t: LockSession(
        make_credentials(lock), t, now=lambda: datetime.now().astimezone()
    ))
    await session.handshake()
    page = await session.get_history_page()
    assert page.complete and page.records[0].method == "fingerprint"
    assert lock.locked and [cmd for cmd, _ in lock.received] == [0x18]
    lock.history_result = 0x42
    with pytest.raises(SessionError):
        await session.get_history_page()
    session.close()


async def test_history_entities_bus_event_and_overlap(hass, mock_bluetooth):
    from unittest.mock import patch

    from .helpers import FakeConnector, make_ble_device
    from .test_init import setup_entry

    now = int(dt_util.utcnow().timestamp())
    lock = FakeLock(clock=dt_util.now)
    lock.history_records = [record_bytes(timestamp=now - i) for i in range(8)]
    fake = FakeConnector(lock)
    with (
        patch("custom_components.eufy_lock_ble.coordinator.establish_connection", fake),
        patch("homeassistant.components.bluetooth.async_ble_device_from_address", return_value=make_ble_device()),
    ):
        events = []
        remove = hass.bus.async_listen("eufy_lock_ble_event", events.append)
        entry = await setup_entry(hass, lock, keep_connected=False)
        coordinator = entry.runtime_data
        assert coordinator.history_status == "ready"
        assert len(coordinator.history.records) == 8
        assert not events
        assert hass.states.get("sensor.back_door_last_unlock_method").state == "fingerprint"
        assert hass.states.get("sensor.back_door_last_unlock_user").state == "Member A"
        lock.history_records.insert(0, record_bytes(timestamp=now, user=b"\x00\x02", name="Member B"))
        coordinator._history_retry_at = 0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(events) == 1
        assert events[0].data["user_name"] == "Member B"
        assert hass.states.get("sensor.back_door_last_unlock_user").state == "Member B"
        assert hass.states.get("event.back_door_activity").attributes["event_type"] == "fingerprint_unlock"
        coordinator._history_retry_at = 0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(events) == 1
        lock.history_records.insert(0, record_bytes(timestamp=now, code=257, name="Member A"))
        coordinator._history_retry_at = 0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        # The lock can attach an account name to a manual record. Keep it in
        # attributes, but do not claim this identifies who turned a key/knob.
        assert hass.states.get("sensor.back_door_last_unlock_user").state == "unknown"
        assert events[-1].data["event_type"] == "manual_unlock"
        # History rejection does not make lock status or battery unavailable.
        lock.history_result = 0x42
        coordinator._history_retry_at = 0
        await coordinator.async_refresh()
        assert coordinator.history_status == "refused_42"
        assert hass.states.get("lock.back_door").state == "locked"
        remove()
        await hass.config_entries.async_unload(entry.entry_id)


async def test_large_history_background_scan_and_stalled_page(hass, mock_bluetooth):
    from unittest.mock import patch

    from .helpers import FakeConnector, make_ble_device, wait_until
    from .test_init import setup_entry

    now = int(dt_util.utcnow().timestamp())
    lock = FakeLock(clock=dt_util.now)
    lock.history_records = [record_bytes(timestamp=now - i) for i in range(30)]
    fake = FakeConnector(lock)
    with (
        patch("custom_components.eufy_lock_ble.coordinator.establish_connection", fake),
        patch("homeassistant.components.bluetooth.async_ble_device_from_address", return_value=make_ble_device()),
    ):
        events = []
        remove = hass.bus.async_listen("eufy_lock_ble_event", events.append)
        entry = await setup_entry(hass, lock, keep_connected=True)
        coordinator = entry.runtime_data
        await wait_until(lambda: coordinator.history_status == "ready", timeout=5)
        assert len(coordinator.history.records) == 30
        assert not events
        await hass.config_entries.async_unload(entry.entry_id)
        remove()

        lock.history_page_size = 1
        stalled = await setup_entry(hass, lock, keep_connected=False)
        assert stalled.runtime_data.history_status == "stalled"
        assert not stalled.runtime_data.history.initialized
        assert stalled.runtime_data.is_locked
        await hass.config_entries.async_unload(stalled.entry_id)
