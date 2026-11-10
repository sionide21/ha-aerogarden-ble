"""Sensor platform for the AeroGarden Bluetooth integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AeroGardenConfigEntry, AeroGardenCoordinator
from .entity import AeroGardenEntity
from .stemlight import LightStatus, PlantType

PARALLEL_UPDATES = 0


def _plant_type(status: LightStatus) -> str | None:
    try:
        return PlantType(status.plant_type).name.lower()
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class AeroGardenSensorEntityDescription(SensorEntityDescription):
    """Describes an AeroGarden sensor."""

    value_fn: Callable[[LightStatus], str | int | None]


SENSORS: tuple[AeroGardenSensorEntityDescription, ...] = (
    AeroGardenSensorEntityDescription(
        key="days_planted",
        translation_key="days_planted",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        value_fn=lambda status: status.days_planted,
    ),
    AeroGardenSensorEntityDescription(
        key="plant_type",
        translation_key="plant_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[plant.name.lower() for plant in PlantType],
        value_fn=_plant_type,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AeroGardenConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    async_add_entities(
        AeroGardenSensor(entry.runtime_data, description) for description in SENSORS
    )


class AeroGardenSensor(AeroGardenEntity, SensorEntity):
    """A value the light reports."""

    entity_description: AeroGardenSensorEntityDescription

    def __init__(
        self,
        coordinator: AeroGardenCoordinator,
        description: AeroGardenSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> str | int | None:
        """Return the value."""
        return self.entity_description.value_fn(self.status)
