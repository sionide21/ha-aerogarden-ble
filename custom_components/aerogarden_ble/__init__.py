"""The AeroGarden Bluetooth integration."""

import datetime as dt

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothReachabilityIntent
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .stemlight import StemLight, StemLightError

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


def local_time() -> dt.time:
    """Return the time of day in Home Assistant's time zone."""
    return dt_util.now().time()


async def async_setup_entry(hass: HomeAssistant, entry: AeroGardenConfigEntry) -> None:
    """Set up an AeroGarden light from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
    if not ble_device:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={
                "address": address,
                "reason": bluetooth.async_address_reachability_diagnostics(
                    hass, address.upper(), BluetoothReachabilityIntent.CONNECTION
                ),
            },
        )

    device = StemLight(ble_device, now=local_time)
    try:
        await device.update()
    except StemLightError as err:
        await device.disconnect()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = AeroGardenCoordinator(hass, device, entry.title)
    entry.runtime_data = coordinator
    entry.async_on_unload(device.disconnect)

    async def _async_stop(event: Event) -> None:
        await device.disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_start())


async def async_unload_entry(hass: HomeAssistant, entry: AeroGardenConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
