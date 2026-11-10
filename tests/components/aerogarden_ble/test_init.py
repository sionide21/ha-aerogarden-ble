"""Tests for setting up and unloading the AeroGarden Bluetooth integration."""

from unittest.mock import patch

from bleak_retry_connector import BleakNotFoundError
from pytest_homeassistant.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import setup_integration

from tests.fake_light import FakeStemLight


async def test_setup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Setup reads the light, sets its clock and registers the device."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert fake_light.clock_set
    device = device_registry.async_get_device_by_connection(
        (dr.CONNECTION_BLUETOOTH, "aa:bb:cc:dd:ee:ff"), init_integration.entry_id
    )
    assert device == snapshot


async def test_device_not_found(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Setup is retried while the light is out of range."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_cannot_connect(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Setup is retried while the light refuses connections."""
    with patch(
        "custom_components.aerogarden_ble.stemlight.client.establish_connection",
        side_effect=BleakNotFoundError("busy"),
    ):
        await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Unloading closes the connection."""
    assert fake_light.client is not None
    assert fake_light.client.is_connected

    await hass.config_entries.async_unload(init_integration.entry_id)

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert not fake_light.client.is_connected


async def test_disconnect_on_stop(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Stopping Home Assistant closes the connection."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert fake_light.client is not None
    assert not fake_light.client.is_connected
