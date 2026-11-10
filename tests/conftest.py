"""Fixtures shared by all tests."""

from collections.abc import Generator
from unittest.mock import patch

import pytest

from custom_components.aerogarden_ble.stemlight import client


@pytest.fixture(autouse=True)
def no_reconnect_delay() -> Generator[None]:
    """Reconnect at once and forget disconnects from other tests."""
    client._last_disconnect.clear()
    with patch.object(client, "RECONNECT_DELAY", 0):
        yield
