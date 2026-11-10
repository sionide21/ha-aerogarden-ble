"""Time platform for the AeroGarden Bluetooth integration."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import datetime as dt
from typing import Any, override

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .entity import AeroGardenEntity
from .stemlight import LightStatus, StemLight

PARALLEL_UPDATES = 1


def _time(hour_minute: tuple[int, int]) -> dt.time | None:
    try:
        return dt.time(*hour_minute)
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class AeroGardenTimeEntityDescription(TimeEntityDescription):
    """Describes an AeroGarden time entity."""

    value_fn: Callable[[LightStatus], tuple[int, int]]
    set_fn: Callable[
        [StemLight, LightStatus, dt.time], Coroutine[Any, Any, LightStatus]
    ]


# The light sets both times in one command.
TIMES: tuple[AeroGardenTimeEntityDescription, ...] = (
    AeroGardenTimeEntityDescription(
        key="light_on_time",
        translation_key="light_on_time",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda status: status.light_on_time,
        set_fn=lambda device, status, value: device.set_schedule(
            value, dt.time(*status.light_off_time)
        ),
    ),
    AeroGardenTimeEntityDescription(
        key="light_off_time",
        translation_key="light_off_time",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda status: status.light_off_time,
        set_fn=lambda device, status, value: device.set_schedule(
            dt.time(*status.light_on_time), value
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AeroGardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the schedule time entities."""
    async_add_entities(
        AeroGardenTime(entry.runtime_data, description) for description in TIMES
    )


class AeroGardenTime(AeroGardenEntity, TimeEntity):
    """A time of day in the light's schedule."""

    entity_description: AeroGardenTimeEntityDescription

    def __init__(
        self,
        coordinator: AeroGardenCoordinator,
        description: AeroGardenTimeEntityDescription,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> dt.time | None:
        """Return the time."""
        return _time(self.entity_description.value_fn(self.status))

    @override
    async def async_set_value(self, value: dt.time) -> None:
        """Change the time."""
        await self._async_command(
            self.entity_description.set_fn(self._device, self.status, value)
        )
