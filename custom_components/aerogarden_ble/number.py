"""Number platform for the AeroGarden Bluetooth integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .entity import AeroGardenEntity
from .stemlight import LightStatus, StemLight

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class AeroGardenNumberEntityDescription(NumberEntityDescription):
    """Describes an AeroGarden number entity."""

    value_fn: Callable[[LightStatus], int]
    set_fn: Callable[[StemLight, LightStatus, int], Coroutine[Any, Any, LightStatus]]


NUMBERS: tuple[AeroGardenNumberEntityDescription, ...] = (
    AeroGardenNumberEntityDescription(
        key="default_brightness",
        translation_key="default_brightness",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda status: status.default_brightness,
        set_fn=lambda device, status, value: device.set_default_brightness(value),
    ),
    # The light sets both durations in one command.
    AeroGardenNumberEntityDescription(
        key="sunrise_duration",
        translation_key="sunrise_duration",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        value_fn=lambda status: status.sunrise_duration,
        set_fn=lambda device, status, value: device.set_sunrise_sunset_durations(
            value, status.sunset_duration
        ),
    ),
    AeroGardenNumberEntityDescription(
        key="sunset_duration",
        translation_key="sunset_duration",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        value_fn=lambda status: status.sunset_duration,
        set_fn=lambda device, status, value: device.set_sunrise_sunset_durations(
            status.sunrise_duration, value
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AeroGardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    async_add_entities(
        AeroGardenNumber(entry.runtime_data, description) for description in NUMBERS
    )


class AeroGardenNumber(AeroGardenEntity, NumberEntity):
    """A numeric light setting."""

    entity_description: AeroGardenNumberEntityDescription

    def __init__(
        self,
        coordinator: AeroGardenCoordinator,
        description: AeroGardenNumberEntityDescription,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> int:
        """Return the current value."""
        return self.entity_description.value_fn(self.status)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Change the value."""
        await self._async_command(
            self.entity_description.set_fn(self._device, self.status, int(value))
        )
