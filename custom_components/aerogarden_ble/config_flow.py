"""Config flow for the AeroGarden Bluetooth integration."""

import logging
from typing import Any, override

from bluetooth_data_tools import human_readable_name
import probatio

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.device_registry import format_mac

from . import local_time
from .const import DOMAIN, LOCAL_NAME
from .stemlight import ADVERTISED_SERVICE_UUID, StemLight, StemLightError

_LOGGER = logging.getLogger(__name__)


def _is_stem_light(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Return whether the advertisement is from a Stem light.

    Other devices built on the same chip, like LD2410 radars, share af30.
    """
    return bool(
        discovery_info.name
        and discovery_info.name.startswith(LOCAL_NAME)
        and ADVERTISED_SERVICE_UUID in discovery_info.service_uuids
    )


def _title(discovery_info: BluetoothServiceInfoBleak) -> str:
    return human_readable_name(None, discovery_info.name, discovery_info.address)


async def _async_test_connection(discovery_info: BluetoothServiceInfoBleak) -> None:
    """Read the light's status once, which also sets its clock."""
    light = StemLight(discovery_info.device, now=local_time)
    try:
        await light.update()
    finally:
        await light.disconnect()


class AeroGardenConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AeroGarden Bluetooth."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a light found by Bluetooth discovery."""
        if not _is_stem_light(discovery_info):
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _title(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered light."""
        assert self._discovery_info is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _async_test_connection(self._discovery_info)
            except StemLightError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Unexpected error in config flow (step=bluetooth_confirm, "
                    "address=%s)",
                    self._discovery_info.address,
                )
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=self.context["title_placeholders"]["name"],
                    data={CONF_ADDRESS: self._discovery_info.address},
                )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
            errors=errors,
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a light that is in range."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]
            await self.async_set_unique_id(
                format_mac(discovery_info.address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            try:
                await _async_test_connection(discovery_info)
            except StemLightError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception(
                    "Unexpected error in config flow (step=user, address=%s)",
                    discovery_info.address,
                )
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=_title(discovery_info),
                    data={CONF_ADDRESS: discovery_info.address},
                )

        # Listing devices here, rather than relying on discovery, also works
        # right after a Marketplace install, before a restart loads matchers.
        current_addresses = self._async_current_ids(include_ignore=False)
        for discovery_info in async_discovered_service_info(self.hass):
            if (
                format_mac(discovery_info.address) in current_addresses
                or discovery_info.address in self._discovered_devices
                or not _is_stem_light(discovery_info)
            ):
                continue
            self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ADDRESS): probatio.In(
                        {
                            address: _title(info)
                            for address, info in self._discovered_devices.items()
                        }
                    ),
                }
            ),
            errors=errors,
        )
