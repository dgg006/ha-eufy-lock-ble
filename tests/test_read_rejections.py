"""A refused read must not publish a successful state or settings update."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.eufy_lock_ble.eufy_ble import crypto
from custom_components.eufy_lock_ble.eufy_ble.client import (
    LockCredentials,
    LockRefusedError,
    LockSession,
)


@pytest.mark.parametrize(("method", "command"), [("query_status", 0x11), ("get_params", 0x21)])
@pytest.mark.parametrize("plaintext", [b"\x42", b"\x42\xa1\x01\x32\xa2\x01\x04"])
async def test_rejected_read_has_no_callback(method: str, command: int, plaintext: bytes) -> None:
    state, params = Mock(), Mock()
    creds = LockCredentials("T8510TEST12345678", bytes(20), crypto.generate_keypair().public_key())
    session = LockSession(
        creds, Mock(), now=lambda: datetime.now(UTC), on_state=state, on_params=params
    )
    session._command = AsyncMock(return_value=plaintext)
    with pytest.raises(LockRefusedError) as exc:
        await getattr(session, method)()
    assert exc.value.cmd == command
    assert exc.value.result == 0x42
    state.assert_not_called()
    params.assert_not_called()
