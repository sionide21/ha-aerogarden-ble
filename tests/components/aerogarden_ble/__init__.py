"""Tests for the AeroGarden Bluetooth integration."""

from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.bluetooth import (
    generate_advertisement_data,
    generate_ble_device,
    inject_bluetooth_service_info,
)

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

ADDRESS = "AA:BB:CC:DD:EE:FF"
SERVICE_UUIDS = ["0000af30-0000-1000-8000-00805f9b34fb"]


def make_service_info(
    name: str, address: str, manufacturer_data: dict[int, bytes], time: float = 0
) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data=manufacturer_data,
        service_uuids=SERVICE_UUIDS,
        service_data={},
        source="local",
        device=generate_ble_device(address=address, name=name),
        advertisement=generate_advertisement_data(
            local_name=name,
            manufacturer_data=manufacturer_data,
            service_uuids=SERVICE_UUIDS,
        ),
        time=time,
        connectable=True,
        tx_power=-127,
    )


# Laid out like a real light's advertisement, which ends with its address.
STEM_SERVICE_INFO = make_service_info(
    "Stem GrowLight",
    ADDRESS,
    {0x05D6: bytes.fromhex("0800") + b"JLAISDK" + bytes.fromhex("aabbccddeeff")},
)

# Another device on the same Jieli chip, sharing the service UUID.
OTHER_JIELI_SERVICE_INFO = make_service_info(
    "JL Speaker",
    "11:22:33:44:55:66",
    {0x05D6: bytes.fromhex("0800") + b"JLAISDK" + bytes.fromhex("112233445566")},
)


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration with the light in range."""
    entry.add_to_hass(hass)
    inject_bluetooth_service_info(hass, STEM_SERVICE_INFO)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
