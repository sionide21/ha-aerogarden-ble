"""Fixtures for the AeroGarden Bluetooth integration tests."""

from collections.abc import Generator
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.aerogarden_ble.const import DOMAIN
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

from . import ADDRESS, setup_integration

from tests.fake_light import FakeStemLight


@pytest.fixture(autouse=True)
def mock_bluetooth(enable_bluetooth: None) -> None:
    """Enable the bluetooth integration with a mocked scanner."""


@pytest.fixture(autouse=True)
def no_retry_backoff() -> Generator[None]:
    """Retry at once; asyncio.sleep never ends while time is frozen."""
    with patch("bleak_retry_connector.calculate_backoff_time", return_value=0):
        yield


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Freeze time at noon local time (US/Pacific), when the light is on."""
    freezer.move_to("2026-11-09T20:00:00+00:00")
    return freezer


@pytest.fixture
def fake_light() -> Generator[FakeStemLight]:
    """Put a simulated light behind establish_connection."""
    light = FakeStemLight()
    with patch(
        "custom_components.aerogarden_ble.stemlight.client.establish_connection",
        light.establish_connection,
    ):
        yield light


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the light."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Stem GrowLight (EEFF)",
        data={CONF_ADDRESS: ADDRESS},
        unique_id=format_mac(ADDRESS),
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_light: FakeStemLight
) -> MockConfigEntry:
    """Set up the integration and return its entry."""
    await setup_integration(hass, mock_config_entry)
    return mock_config_entry
