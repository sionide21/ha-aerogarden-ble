"""Select platform for the AeroGarden Bluetooth integration."""

from typing import override

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .entity import AeroGardenEntity
from .stemlight import LightMode

PARALLEL_UPDATES = 1

LIGHT_MODES = {
    "white": LightMode.WHITE,
    "full_spectrum": LightMode.FULL_SPECTRUM,
    "warm": LightMode.WARM,
    "cool": LightMode.COOL,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AeroGardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light mode select."""
    async_add_entities([AeroGardenLightModeSelect(entry.runtime_data)])


class AeroGardenLightModeSelect(AeroGardenEntity, SelectEntity):
    """The light's colour mode."""

    _attr_options = list(LIGHT_MODES)
    _attr_translation_key = "light_mode"

    def __init__(self, coordinator: AeroGardenCoordinator) -> None:
        """Initialize the select."""
        super().__init__(coordinator, "light_mode")

    @property
    @override
    def current_option(self) -> str | None:
        """Return the current mode."""
        for option, mode in LIGHT_MODES.items():
            if mode == self.status.light_mode:
                return option
        return None

    @override
    async def async_select_option(self, option: str) -> None:
        """Change the mode."""
        await self._async_command(self._device.set_light_mode(LIGHT_MODES[option]))
