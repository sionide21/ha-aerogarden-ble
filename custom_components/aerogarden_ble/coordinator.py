"""Coordinator for the AeroGarden Bluetooth integration."""

from collections.abc import Iterator
import datetime as dt
import logging
from typing import override

from bluetooth_data_tools import monotonic_time_coarse

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import BOUNDARY_DELAY, POLL_INTERVAL, REACHABLE_TIMEOUT, RETRY_INTERVAL
from .stemlight import LightStatus, StemLight, StemLightError

_LOGGER = logging.getLogger(__name__)

type AeroGardenConfigEntry = ConfigEntry[AeroGardenCoordinator]


def _boundaries(status: LightStatus) -> Iterator[dt.time]:
    """Yield the times of day the light changes on its own."""
    # Any date works; only the time of day is used.
    day = dt.date(2000, 1, 2)
    on = dt.datetime.combine(day, dt.time(*status.light_on_time))
    off = dt.datetime.combine(day, dt.time(*status.light_off_time))
    yield on.time()
    yield off.time()
    if status.sunrise_sunset_enabled:
        yield (on + dt.timedelta(minutes=status.sunrise_duration)).time()
        yield (off - dt.timedelta(minutes=status.sunset_duration)).time()
        yield (off + dt.timedelta(minutes=status.sunset_duration)).time()


def crossed_boundary(status: LightStatus, since: dt.datetime, now: dt.datetime) -> bool:
    """Return whether a schedule boundary passed between since and now."""
    for boundary in _boundaries(status):
        for day in (now.date(), now.date() - dt.timedelta(days=1)):
            moment = dt.datetime.combine(day, boundary, now.tzinfo) + BOUNDARY_DELAY
            if since < moment <= now:
                return True
    return False


class AeroGardenCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Poll the light when an advertisement shows it is in range."""

    def __init__(self, hass: HomeAssistant, device: StemLight, title: str) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=device.address,
            mode=BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_update,
            connectable=True,
        )
        self.device = device
        self.title = title
        self.last_update = dt_util.now()
        # Commands and the light's own pushes report status between polls.
        device.register_callback(self._async_handle_status)

    @callback
    def _async_handle_status(self, status: LightStatus) -> None:
        """Push a new status to the entities."""
        self.async_update_listeners()

    @callback
    def light_reachable(self) -> bool:
        """Return whether connecting to the light can work right now.

        The light advertises several times a second, except while something,
        such as the AeroGarden app in the foreground, is connected to it.
        Connecting without advertisements only fails after a long timeout.
        """
        if self.device.is_connected:
            return True
        info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        )
        last_seen = max(info.time if info else 0, self.device.last_disconnected or 0)
        return monotonic_time_coarse() - last_seen < REACHABLE_TIMEOUT

    @callback
    def _needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Return whether the light should be polled now."""
        now = dt_util.now()
        age = now - self.last_update
        if not self.last_poll_successful:
            return age >= RETRY_INTERVAL
        if age >= POLL_INTERVAL:
            return True
        status = self.device.state
        return status is not None and crossed_boundary(status, self.last_update, now)

    async def _async_update(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Poll the light."""
        await self.device.update()

    @override
    async def _async_poll(self) -> None:
        """Poll the light and log availability changes once."""
        assert self._last_service_info

        try:
            await self._async_poll_data(self._last_service_info)
        except StemLightError as err:
            if self.last_poll_successful:
                _LOGGER.info("%s is unavailable: %s", self.title, err)
                self.last_poll_successful = False
                self.async_update_listeners()
            return
        except Exception:
            if self.last_poll_successful:
                _LOGGER.exception("%s: unexpected error while polling", self.title)
                self.last_poll_successful = False
                self.async_update_listeners()
            return
        finally:
            self.last_update = dt_util.now()

        if not self.last_poll_successful:
            _LOGGER.info("%s is back online", self.title)
            self.last_poll_successful = True

        self._async_handle_bluetooth_poll()

    @callback
    @override
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Connect through whichever adapter or proxy heard the light last."""
        self.device.set_ble_device(service_info.device)
        super()._async_handle_bluetooth_event(service_info, change)
