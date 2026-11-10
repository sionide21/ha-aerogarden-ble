"""Diagnostics support for the AeroGarden Bluetooth integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import AeroGardenConfigEntry

# The manufacturer data, and so the advertisement, contains the MAC address.
SERVICE_INFO_TO_REDACT = frozenset(
    {"address", "advertisement", "device", "manufacturer_data", "name", "source"}
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AeroGardenConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    device = coordinator.device
    service_info = bluetooth.async_last_service_info(
        hass, device.address, connectable=True
    )
    status = None
    if device.state is not None:
        status = asdict(device.state)
        status["extra"] = device.state.extra.hex()

    return {
        "service_info": async_redact_data(
            service_info.as_dict() if service_info else None,
            SERVICE_INFO_TO_REDACT,
        ),
        "last_poll_successful": coordinator.last_poll_successful,
        "status": status,
        # The raw packet helps with debugging fields the integration ignores.
        "last_status_packet": (
            device.last_status_packet.hex() if device.last_status_packet else None
        ),
    }
