"""Constants for the eufy Smart Lock (BLE) integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "eufy_lock_ble"
MANUFACTURER = "eufy (Anker)"

PLATFORMS: list[Platform] = [Platform.LOCK, Platform.SENSOR, Platform.BINARY_SENSOR]

# config entry data
CONF_SERIAL = "serial"
CONF_ADMIN_USER_ID = "admin_user_id"
CONF_LOCK_PUBLIC_KEY = "lock_public_key"
CONF_SHORT_USER_ID = "short_user_id"
CONF_NICKNAME = "nickname"
CONF_SKIP_VERIFY = "skip_verify"
CONF_MANUAL_CREDENTIALS = "manual_credentials"
CONF_CAPTCHA_CODE = "captcha_code"
CONF_VERIFY_CODE = "verify_code"
CONF_LOCK = "lock"

# options
OPT_KEEP_CONNECTED = "keep_connected"
OPT_POLL_INTERVAL = "poll_interval"

DEFAULT_KEEP_CONNECTED = True
DEFAULT_POLL_INTERVAL = 300
MIN_POLL_INTERVAL = 30
MAX_POLL_INTERVAL = 86400

# timing
HEARTBEAT_INTERVAL = 15.0
RECONNECT_MIN_BACKOFF = 5.0
RECONNECT_MAX_BACKOFF = 120.0
# the lock (or the proxy) closing a healthy session is ordinary BLE life: go straight
# back instead of backing off
RECONNECT_AFTER_DROP = 1.0
# the connectivity sensor only reports "disconnected" once the link has been down
# this long, so a drop followed by a quick reconnect does not flood the logbook
CONNECTED_DEBOUNCE = 60.0
CONNECT_TIMEOUT = 30.0
# whole connect + handshake sequence, and a bounded BLE disconnect, so the
# reconnect loop can never wedge on a proxy that lost the device
CONNECT_TOTAL_TIMEOUT = 75.0
DISCONNECT_TIMEOUT = 10.0
# how long the lock keeps its last state after a drop before going unavailable
AVAILABILITY_GRACE = 120.0
STATE_SETTLE_TIMEOUT = 6.0
OPERATION_CONNECT_WAIT = 12.0
ADV_NOTIFY_INTERVAL = 30.0

# Manufacturer model names, not a list of hardware-tested compatibility.
MODELS: dict[str, str] = {
    "T8500": "Smart Lock",
    "T8510": "Smart Lock Touch",
    "T8510P": "Smart Lock S230",
    "T8520": "Smart Lock Touch & Wi-Fi",
    "T8530": "Video Smart Lock S330",
}


def model_id_from_serial(serial: str) -> str:
    """Keep known suffix variants distinct from their similar-looking base model."""
    return next(
        (prefix for prefix in sorted(MODELS, key=len, reverse=True) if serial.startswith(prefix)),
        serial[:5],
    )
