"""Transport-agnostic implementation of the eufy BLE smart-lock protocol.

This package has no Home Assistant dependency so it can be unit-tested and
reused elsewhere.  See ``client.py`` for the session state machine and
``protocol.py`` / ``crypto.py`` for the wire format and primitives.
"""

from .client import (
    LockCredentials,
    LockRefusedError,
    LockSession,
    LockTimeoutError,
    SessionError,
    Transport,
)
from .crypto import CryptoError
from .protocol import (
    CHAR_AUX_NOTIFY_UUID,
    CHAR_COMMAND_UUID,
    SERVICE_UUID,
    Cmd,
    LockStatus,
    ProtocolError,
    SessionInfo,
    Tag,
)

__all__ = [
    "CHAR_AUX_NOTIFY_UUID",
    "CHAR_COMMAND_UUID",
    "SERVICE_UUID",
    "Cmd",
    "CryptoError",
    "LockCredentials",
    "LockRefusedError",
    "LockSession",
    "LockStatus",
    "LockTimeoutError",
    "ProtocolError",
    "SessionError",
    "SessionInfo",
    "Tag",
    "Transport",
]
