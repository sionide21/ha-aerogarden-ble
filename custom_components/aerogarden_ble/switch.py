"""Switch platform for the AeroGarden Bluetooth integration."""

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .entity import AeroGardenEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AeroGardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sunrise and sunset switch."""
    async_add_entities([AeroGardenSunriseSunsetSwitch(entry.runtime_data)])


class AeroGardenSunriseSunsetSwitch(AeroGardenEntity, SwitchEntity):
    """Whether the light fades in and out at its on and off times."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "sunrise_sunset"

    def __init__(self, coordinator: AeroGardenCoordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, "sunrise_sunset")

    @property
    @override
    def is_on(self) -> bool:
        """Return whether sunrise and sunset are on."""
        return self.status.sunrise_sunset_enabled

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn sunrise and sunset on."""
        await self._async_command(self._device.set_sunrise_sunset_enabled(True))

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn sunrise and sunset off."""
        await self._async_command(self._device.set_sunrise_sunset_enabled(False))
