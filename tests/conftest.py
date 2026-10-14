"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
import sys
from unittest.mock import PropertyMock, patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components/ in every test."""
    return


if sys.platform == "win32":  # pragma: no cover
    # pytest-socket (pulled in by the HA test plugin) only allows AF_UNIX sockets, but
    # asyncio's ProactorEventLoop on Windows needs an AF_INET self-pipe.  Neutralise
    # the guard locally; CI runs on Linux with the guard active.
    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None
    pytest_socket.socket_allow_hosts = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def _windows_bluetooth_adapters_history() -> Generator[None]:
    """On Windows the mocked Linux adapters still try to read BlueZ history over D-Bus."""
    if sys.platform != "win32":  # pragma: no cover
        yield
        return
    from bluetooth_adapters.systems.linux import LinuxAdapters

    with patch.object(LinuxAdapters, "history", new_callable=PropertyMock, return_value={}):
        yield
