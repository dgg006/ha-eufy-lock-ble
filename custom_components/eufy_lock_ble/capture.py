"""User-initiated, bounded local capture of advertisements from one lock."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from uuid import uuid4

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import api as bluetooth_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

CAPTURE_SECONDS = 600
MAX_REPORTS = 20000
CAPTURE_DIRECTORY = "eufy_lock_ble_captures"
MARKERS = {"fingerprint", "passcode", "physical_key", "thumbturn"}


def _write_capture(path: Path, data: dict[str, Any]) -> None:
    """Write atomically outside www; these local logs can contain Bluetooth IDs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".capture-", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class AdvertisementCapture:
    """Observe HA's shared scanner; never connect, write GATT or start a scanner."""

    def __init__(self, hass: HomeAssistant, address: str, on_update: Callable[[], None]) -> None:
        self.hass = hass
        self.address = address
        self._on_update = on_update
        self.active = False
        self.status = "idle"
        self.filename: str | None = None
        self.started_at = None
        self.ends_at = None
        self.mode = "unknown"
        self.reports = 0
        self.marker_count = 0
        self.distinct_payloads = 0
        self.last_report_at = None
        self.last_error: str | None = None
        self._started_monotonic = 0.0
        self._entries: list[dict[str, Any]] = []
        self._signatures: set[str] = set()
        self._metadata: dict[str, Any] = {}
        self._unsub: Callable[[], None] | None = None
        self._cancel_end: Callable[[], None] | None = None
        self._cancel_checkpoint: Callable[[], None] | None = None
        self._tasks: set[asyncio.Task] = set()
        self._io_lock = asyncio.Lock()

    def _task(self, coroutine) -> None:
        task = self.hass.async_create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_start(self) -> None:
        """Start ten minutes of observation after the coordinator disconnects."""
        if self.active:
            return
        async with self._io_lock:
            self.started_at = dt_util.utcnow()
            self.ends_at = self.started_at + timedelta(seconds=CAPTURE_SECONDS)
            self._started_monotonic = time.monotonic()
            self.filename = f"capture_{self.started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}.json"
            self._entries = []
            self._signatures.clear()
            self.reports = self.marker_count = self.distinct_payloads = 0
            self.last_report_at = self.last_error = None
            every = getattr(bluetooth_api, "async_register_advertisement_callback", None)
            self.mode = "every_delivered_advertisement" if every else "changed_advertisements_only"
            self._metadata = {
                "format_version": 1, "capture_id": self.filename,
                "started_at": self.started_at.isoformat(), "timezone": self.hass.config.time_zone,
                "duration_seconds": CAPTURE_SECONDS, "address": self.address,
                "mode": self.mode, "status": "capturing", "periodic_polling_paused": True,
                "limitations": [
                    "Only advertisements delivered by Home Assistant/proxies are observed.",
                    "Manufacturer/service fields may be merged across packets; raw bytes may be unavailable.",
                    "Duplicate filtering, packet loss and scanner settings can hide radio activity.",
                    "This capture does not initiate a GATT connection or change proxy scan settings.",
                ],
            }
            self.status = "capturing"
            self.active = True
            try:
                if every:
                    self._unsub = every(self.hass, self._on_advertisement, self.address)
                else:
                    self._unsub = bluetooth.async_register_callback(
                        self.hass, self._on_changed_advertisement,
                        {"address": self.address, "connectable": False},
                        bluetooth.BluetoothScanningMode.PASSIVE,
                    )
                await self._async_save()
            except Exception:
                self.active = False
                self.status = "error"
                if self._unsub:
                    self._unsub()
                    self._unsub = None
                raise
            self._cancel_end = async_call_later(self.hass, CAPTURE_SECONDS, self._auto_stop)
            self._cancel_checkpoint = async_call_later(self.hass, 30, self._checkpoint)
        self._on_update()

    @callback
    def _on_changed_advertisement(self, info, _change) -> None:
        self._on_advertisement(info)

    @callback
    def _on_advertisement(self, info) -> None:
        if not self.active or info.address.upper() != self.address.upper():
            return
        if self.reports >= MAX_REPORTS:
            return
        now = dt_util.utcnow()
        raw = getattr(info, "raw", None)
        payload = {
            "name": info.name,
            "manufacturer_data": {f"{key:04x}": bytes(value).hex()
                                  for key, value in info.manufacturer_data.items()},
            "service_data": {key: bytes(value).hex() for key, value in info.service_data.items()},
            "service_uuids": list(info.service_uuids),
            "raw": bytes(raw).hex() if isinstance(raw, (bytes, bytearray, memoryview)) else None,
        }
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
        self._signatures.add(signature)
        self.distinct_payloads = len(self._signatures)
        self.reports += 1
        self.last_report_at = now
        seen_time = getattr(info, "time", None)
        self._entries.append({
            "kind": "advertisement", "received_at": now.isoformat(),
            "elapsed_seconds": round(time.monotonic() - self._started_monotonic, 3),
            "source": info.source, "rssi": info.rssi, "tx_power": info.tx_power,
            "connectable": info.connectable, "advertisement_time": seen_time,
            "cached_before_start": seen_time < self._started_monotonic if seen_time else None,
            "payload_id": signature, **payload,
        })
        if self.reports == MAX_REPORTS:
            self._task(self.async_stop("report_limit"))
        # Avoid a state write per radio packet; markers and checkpoints refresh UI.

    @callback
    def mark(self, marker: str) -> None:
        """Record an annotation only; it never performs the labelled door action."""
        if not self.active:
            return
        if marker not in MARKERS | {"ha_command"}:
            raise ValueError("unknown capture marker")
        if self.marker_count >= 200:
            return
        self.marker_count += 1
        self._entries.append({
            "kind": "marker", "marker": marker, "received_at": dt_util.utcnow().isoformat(),
            "elapsed_seconds": round(time.monotonic() - self._started_monotonic, 3),
        })
        self._on_update()

    @callback
    def _auto_stop(self, _now) -> None:
        self._task(self.async_stop("duration_elapsed"))

    @callback
    def _checkpoint(self, _now) -> None:
        self._cancel_checkpoint = None
        if self.active:
            self._task(self._async_checkpoint())

    async def _async_checkpoint(self) -> None:
        async with self._io_lock:
            if not self.active:
                return
            try:
                await self._async_save()
            except OSError as err:
                self.last_error = type(err).__name__
                self.status = "save_error"
            self._on_update()
            self._cancel_checkpoint = async_call_later(self.hass, 30, self._checkpoint)

    async def _async_save(self) -> None:
        if not self.filename:
            return
        data = {**self._metadata, "report_count": self.reports,
                "distinct_payloads": self.distinct_payloads, "marker_count": self.marker_count,
                "saved_at": dt_util.utcnow().isoformat(), "entries": list(self._entries)}
        path = Path(self.hass.config.path(CAPTURE_DIRECTORY, self.filename))
        await self.hass.async_add_executor_job(_write_capture, path, data)

    async def async_stop(self, reason: str = "user_stopped") -> None:
        """Stop subscriptions first and save the final file; safe on repeated calls."""
        async with self._io_lock:
            if not self.active:
                return
            self.active = False
            if self._unsub:
                self._unsub()
                self._unsub = None
            for cancel in (self._cancel_end, self._cancel_checkpoint):
                if cancel:
                    cancel()
            self._cancel_end = self._cancel_checkpoint = None
            self._metadata.update({"status": "finished", "stop_reason": reason,
                                   "ended_at": dt_util.utcnow().isoformat()})
            try:
                await self._async_save()
                self.status = "saved"
            except OSError as err:
                self.last_error = type(err).__name__
                self.status = "error"
            self._on_update()

    async def async_shutdown(self) -> None:
        """Keep a partial file on an integration reload or HA shutdown."""
        await self.async_stop("integration_unloaded")
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
