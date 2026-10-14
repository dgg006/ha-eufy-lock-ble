"""Bounded local access history and replay protection."""

from __future__ import annotations

from .eufy_ble.history import LockRecord

HISTORY_LIMIT = 100
FRESH_EVENT_SECONDS = 120


class HistoryTracker:
    """Keep recent records; the first complete scan establishes a quiet baseline."""

    def __init__(self) -> None:
        self.records: list[LockRecord] = []
        self.initialized = False
        self._seen: set[str] = set()
        self._latest_timestamp = 0

    def ingest(
        self, records: tuple[LockRecord, ...], *, complete: bool, now: float
    ) -> list[LockRecord]:
        """Return newly observed records after the initial scan, even if delayed."""
        new = []
        previous_latest = self._latest_timestamp
        for record in sorted(records, key=lambda item: item.timestamp):
            if record.record_id in self._seen:
                continue
            self._seen.add(record.record_id)
            if (
                self.initialized
                and record.timestamp >= previous_latest
                and record.timestamp <= now
            ):
                new.append(record)
        # Preserve device order for records sharing the same one-second timestamp.
        merged = {record.record_id: record for record in records}
        for record in self.records:
            merged.setdefault(record.record_id, record)
        self.records = sorted(merged.values(), key=lambda item: item.timestamp, reverse=True)[
            :HISTORY_LIMIT
        ]
        valid_timestamps = [record.timestamp for record in self.records if record.timestamp <= now]
        if valid_timestamps:
            self._latest_timestamp = max(self._latest_timestamp, max(valid_timestamps))
        if len(self._seen) > 4096:
            # The timestamp watermark prevents older pruned IDs from firing again.
            self._seen = {record.record_id for record in self.records}
        if complete:
            self.initialized = True
        return new

    @property
    def latest(self) -> LockRecord | None:
        """Latest record, including alarms that are not a lock/unlock action."""
        return self.records[0] if self.records else None

    @property
    def latest_unlock(self) -> LockRecord | None:
        """Keep the last access visible after a subsequent lock or alarm."""
        return next((record for record in self.records if record.action == "unlock"), None)
