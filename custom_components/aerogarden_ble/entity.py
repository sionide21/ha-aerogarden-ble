"""Base entity for the AeroGarden Bluetooth integration."""

from collections.abc import Awaitable
from typing import override

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac

from .const import DOMAIN
from .coordinator import AeroGardenCoordinator
from .stemlight import LightStatus, StemLightConnectionError, StemLightError


class AeroGardenEntity(PassiveBluetoothCoordinatorEntity[AeroGardenCoordinator]):
    """Base class for AeroGarden entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AeroGardenCoordinator, key: str | None) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device = coordinator.device
        mac = format_mac(coordinator.device.address)
        self._attr_unique_id = f"{mac}_{key}" if key else mac
        self._attr_device_info = DeviceInfo(
            manufacturer="AeroGarden",
            model="Stem Grow Light",
            connections={(dr.CONNECTION_BLUETOOTH, mac)},
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the light is in range and answered the last poll."""
        return (
            super().available
            and self.coordinator.last_poll_successful
            and self._device.state is not None
        )

    @property
    def status(self) -> LightStatus:
        """Return the last status the light reported."""
        assert self._device.state is not None
        return self._device.state

    @override
    async def async_update(self) -> None:
        """Read the light right away, for the update entity action."""
        await self._async_command(self.coordinator.async_poll_now())

    async def _async_command[T](self, coro: Awaitable[T]) -> T:
        """Run a light command, translating errors."""
        try:
            return await coro
        except StemLightConnectionError as err:
            # A silent light is most likely connected to something else.
            if not self.coordinator.light_reachable():
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="light_busy"
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
        except StemLightError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_error",
                translation_placeholders={"error": str(err)},
            ) from err
