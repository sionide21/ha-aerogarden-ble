"""Light platform for the AeroGarden Bluetooth integration."""

from typing import Any, override

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .entity import AeroGardenEntity

PARALLEL_UPDATES = 1

BRIGHTNESS_SCALE = (1, 100)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AeroGardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light."""
    async_add_entities([AeroGardenLight(entry.runtime_data)])


class AeroGardenLight(AeroGardenEntity, LightEntity):
    """The grow light.

    Turning it on or off overrides the schedule until its next on or off time.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: AeroGardenCoordinator) -> None:
        """Initialize the light."""
        super().__init__(coordinator, None)

    @property
    @override
    def is_on(self) -> bool:
        """Return whether the light is lit."""
        return self.status.light_on

    @property
    @override
    def brightness(self) -> int | None:
        """Return the brightness."""
        if not self.status.brightness:
            return None
        return value_to_brightness(BRIGHTNESS_SCALE, self.status.brightness)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, or change its brightness."""
        if ATTR_BRIGHTNESS in kwargs:
            # Round so 50% stays 50%, but never to 0, which is off.
            percent = max(
                1, round(brightness_to_value(BRIGHTNESS_SCALE, kwargs[ATTR_BRIGHTNESS]))
            )
        elif self.status.light_on:
            return
        else:
            percent = self.status.default_brightness or 100
        await self._async_command(self._device.set_brightness(percent))

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self._async_command(self._device.set_brightness(0))
