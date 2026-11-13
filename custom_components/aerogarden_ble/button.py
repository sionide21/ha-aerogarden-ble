"""Button platform for the AeroGarden Bluetooth integration."""

from typing import override

from homeassistant.components.button import ButtonEntity
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
    """Set up the refresh button."""
    async_add_entities([AeroGardenRefreshButton(entry.runtime_data)])


class AeroGardenRefreshButton(AeroGardenEntity, ButtonEntity):
    """Read the light right away instead of at the next poll."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "refresh"

    def __init__(self, coordinator: AeroGardenCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "refresh")

    @property
    @override
    def available(self) -> bool:
        """Stay available after a failed poll, when refreshing is most useful."""
        return self.coordinator.available

    @override
    async def async_press(self) -> None:
        """Read the light."""
        await self._async_command(self.coordinator.async_poll_now())
