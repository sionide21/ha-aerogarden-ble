"""Bluetooth client for the AeroGarden Stem Grow Light.

The client connects when it has something to do and disconnects again after
a short idle period, so the phone app and Bluetooth proxies stay usable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import datetime as dt
import logging
import sys
import time

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    BleakNotFoundError,
    establish_connection,
    retry_bluetooth_connection_error,
)

from . import protocol
from .exceptions import (
    StemLightCommandError,
    StemLightConnectionError,
    StemLightTimeoutError,
)
from .protocol import LightMode, LightStatus, Notification, PacketType

_LOGGER = logging.getLogger(__name__)

DISCONNECT_DELAY = 10.0
RESPONSE_TIMEOUT = 5.0
CLOCK_DRIFT_TOLERANCE = 60
OPERATION_ATTEMPTS = 2
# Connecting within a few milliseconds of a disconnect times out after 20 s;
# two seconds later it works.
RECONNECT_DELAY = 3.0

type StatusCallback = Callable[[LightStatus], None]

# Per address, so a new client (say, after the config flow's test
# connection) also waits.
_last_disconnect: dict[str, float] = {}

_DBUS_NO_OBJECT_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownObject",
    }
)


def _clock_drift(light_seconds: int, now: dt.time) -> int:
    """Return how many seconds the light's clock is off, across midnight."""
    diff = abs(light_seconds - protocol.seconds_since_midnight(now))
    return min(diff, protocol.SECONDS_PER_DAY - diff)


async def _prefer_le_bearer(device: BLEDevice) -> bool | None:
    """Ask BlueZ to connect over LE.

    The light's advertising flags claim BR/EDR support, so BlueZ pages it over
    BR/EDR and times out. PreferredBearer needs BlueZ 5.80 with experimental
    features. Returns None when the device is not on a local BlueZ adapter.
    """
    details = device.details
    if (
        sys.platform != "linux"
        or not isinstance(details, dict)
        or not isinstance(path := details.get("path"), str)
    ):
        return None
    # dbus-fast is a bleak dependency on Linux.
    from dbus_fast import BusType, Message, MessageType, Variant  # noqa: PLC0415
    from dbus_fast.aio import MessageBus  # noqa: PLC0415

    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except Exception:
        _LOGGER.debug("Could not reach the system D-Bus", exc_info=True)
        return False
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=path,
                interface="org.freedesktop.DBus.Properties",
                member="Set",
                signature="ssv",
                body=["org.bluez.Device1", "PreferredBearer", Variant("s", "le")],
            )
        )
    finally:
        bus.disconnect()
    if reply is None or reply.message_type is not MessageType.ERROR:
        return True
    _LOGGER.debug("Setting PreferredBearer failed: %s %s", reply.error_name, reply.body)
    # BlueZ drops the device object while the light isn't advertising.
    if reply.error_name in _DBUS_NO_OBJECT_ERRORS:
        return None
    return False


class StemLight:
    """An AeroGarden Stem Grow Light."""

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        now: Callable[[], dt.time] | None = None,
        disconnect_delay: float = DISCONNECT_DELAY,
    ) -> None:
        """Initialize the client.

        now returns the local time of day the light's clock is set to.
        """
        self._ble_device = ble_device
        self._now = now or (lambda: dt.datetime.now().time())
        self._disconnect_delay = disconnect_delay
        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._expected_disconnect = False
        self._warned_bearer = False
        self._waiters: dict[int, list[asyncio.Future[Notification]]] = {}
        self._callbacks: list[StatusCallback] = []
        self.state: LightStatus | None = None
        self.last_status_packet: bytes | None = None

    @property
    def address(self) -> str:
        """Return the Bluetooth address."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Return the advertised name, or the address."""
        return self._ble_device.name or self._ble_device.address

    @property
    def is_connected(self) -> bool:
        """Return whether a connection is open."""
        return self._client is not None and self._client.is_connected

    @property
    def last_disconnected(self) -> float | None:
        """Return when (time.monotonic) any client last lost the light."""
        return _last_disconnect.get(self.address)

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Use a newer BLEDevice, for example from a better placed adapter."""
        self._ble_device = ble_device

    def register_callback(self, callback: StatusCallback) -> Callable[[], None]:
        """Call callback with every new status. Returns an unsubscribe function."""
        self._callbacks.append(callback)

        def _unregister() -> None:
            self._callbacks.remove(callback)

        return _unregister

    async def update(self) -> LightStatus:
        """Read the light's status."""
        return await self._execute(self._query)

    async def set_brightness(self, percent: int) -> LightStatus:
        """Set the brightness until the next schedule boundary (0 = off)."""
        return await self._command(protocol.set_brightness(percent))

    async def set_default_brightness(self, percent: int) -> LightStatus:
        """Set the brightness the schedule uses."""
        return await self._command(protocol.set_default_brightness(percent))

    async def set_light_mode(self, mode: LightMode) -> LightStatus:
        """Set the light colour mode."""
        return await self._command(protocol.set_light_mode(mode))

    async def set_schedule(self, on: dt.time, off: dt.time) -> LightStatus:
        """Set the times of day the light turns on and off."""
        return await self._command(protocol.set_schedule(on, off))

    async def set_sunrise_sunset_durations(
        self, sunrise: int, sunset: int
    ) -> LightStatus:
        """Set the sunrise and sunset durations in minutes."""
        return await self._command(
            protocol.set_sunrise_sunset_durations(sunrise, sunset)
        )

    async def set_sunrise_sunset_enabled(self, enabled: bool) -> LightStatus:
        """Turn sunrise and sunset on or off."""

        async def _set(client: BleakClientWithServiceCache) -> LightStatus:
            status = await self._query(client)
            if status.sunrise_sunset_enabled == enabled:
                return status
            # The light only offers a toggle.
            await self._control(client, protocol.toggle_sunrise_sunset())
            return await self._query(client)

        return await self._execute(_set)

    async def sync_clock(self) -> LightStatus:
        """Set the light's clock to now."""

        async def _sync(client: BleakClientWithServiceCache) -> LightStatus:
            await self._send_clock(client)
            return await self._query(client)

        return await self._execute(_sync)

    async def disconnect(self) -> None:
        """Close the connection, if any."""
        self._cancel_disconnect_timer()
        async with self._operation_lock:
            await self._disconnect()

    async def _command(self, frame: bytes) -> LightStatus:
        async def _send(client: BleakClientWithServiceCache) -> LightStatus:
            await self._control(client, frame)
            return await self._query(client)

        return await self._execute(_send)

    async def _execute[T](
        self, operation: Callable[[BleakClientWithServiceCache], Awaitable[T]]
    ) -> T:
        """Run operation on a connection, translating errors."""
        async with self._operation_lock:
            self._cancel_disconnect_timer()
            try:
                return await self._execute_with_retry(operation)
            except TimeoutError as err:
                await self._disconnect()
                raise StemLightTimeoutError(f"{self.name} did not respond") from err
            except (BleakError, EOFError, BrokenPipeError, AttributeError) as err:
                await self._disconnect()
                raise StemLightConnectionError(
                    f"{self.name}: {err or type(err).__name__}"
                ) from err
            finally:
                self._schedule_disconnect()

    @retry_bluetooth_connection_error(OPERATION_ATTEMPTS)
    async def _execute_with_retry[T](
        self, operation: Callable[[BleakClientWithServiceCache], Awaitable[T]]
    ) -> T:
        client = await self._ensure_connected()
        return await operation(client)

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            return self._client
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return self._client
            bearer = await _prefer_le_bearer(self._ble_device)
            if bearer is False and not self._warned_bearer:
                self._warned_bearer = True
                _LOGGER.warning(
                    "Could not tell BlueZ to reach %s over Bluetooth LE. If the "
                    "connection keeps failing, use an ESPHome Bluetooth proxy or "
                    "run bluetoothd with --experimental",
                    self.name,
                )
            if (since := _last_disconnect.get(self.address)) is not None and (
                wait := RECONNECT_DELAY - (time.monotonic() - since)
            ) > 0:
                _LOGGER.debug("%s: waiting %.1fs to reconnect", self.name, wait)
                await asyncio.sleep(wait)
            _LOGGER.debug("%s: connecting", self.name)
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self.name,
                    self._disconnected,
                    use_services_cache=True,
                    ble_device_callback=lambda: self._ble_device,
                )
            except BleakNotFoundError as err:
                # establish_connection already retried; don't repeat it all.
                raise StemLightConnectionError(str(err)) from err
            self._expected_disconnect = False
            try:
                await client.start_notify(
                    protocol.NOTIFY_CHAR_UUID, self._notification_handler
                )
                self._client = client
                await self._check_clock(client)
            except BaseException:
                self._client = None
                self._expected_disconnect = True
                await client.disconnect()
                raise
            _LOGGER.debug("%s: connected", self.name)
            return client

    async def _check_clock(self, client: BleakClientWithServiceCache) -> None:
        """Set the light's clock if it lost power or drifted."""
        status = await self._query(client)
        drift = _clock_drift(status.clock_seconds, self._now())
        if not status.clock_set or drift > CLOCK_DRIFT_TOLERANCE:
            _LOGGER.debug(
                "%s: setting clock (set=%s, drift=%ss)",
                self.name,
                status.clock_set,
                drift,
            )
            await self._send_clock(client)

    async def _send_clock(self, client: BleakClientWithServiceCache) -> None:
        seconds = protocol.seconds_since_midnight(self._now())
        await self._control(client, protocol.set_clock(seconds))

    async def _query(self, client: BleakClientWithServiceCache) -> LightStatus:
        notification = await self._request(
            client, protocol.status_query(), PacketType.STATUS
        )
        if notification.status is None:
            raise StemLightCommandError(
                f"{self.name} sent a short status: {notification.plain.hex()}"
            )
        return notification.status

    async def _control(self, client: BleakClientWithServiceCache, frame: bytes) -> None:
        ack = await self._request(client, frame, PacketType.CONTROL)
        if not ack.ack_ok:
            raise StemLightCommandError(
                f"{self.name} rejected {frame.hex()}: {ack.plain.hex()}"
            )

    async def _request(
        self,
        client: BleakClientWithServiceCache,
        frame: bytes,
        reply_type: PacketType,
    ) -> Notification:
        """Write frame and wait for the first notification of reply_type."""
        waiter: asyncio.Future[Notification] = (
            asyncio.get_running_loop().create_future()
        )
        waiters = self._waiters.setdefault(reply_type, [])
        waiters.append(waiter)
        try:
            _LOGGER.debug("%s: sending %s", self.name, frame.hex())
            # ae3b only supports write-without-response.
            await client.write_gatt_char(
                protocol.WRITE_CHAR_UUID, frame, response=False
            )
            async with asyncio.timeout(RESPONSE_TIMEOUT):
                return await waiter
        finally:
            if waiter in waiters:
                waiters.remove(waiter)

    def _notification_handler(
        self, _char: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        notification = protocol.parse_notification(data)
        if notification is None:
            _LOGGER.debug("%s: ignoring notification %s", self.name, data.hex())
            return
        _LOGGER.debug("%s: received %s", self.name, notification.plain.hex())
        for waiter in self._waiters.pop(notification.packet_type, []):
            if not waiter.done():
                waiter.set_result(notification)
        if (status := notification.status) is None:
            return
        self.last_status_packet = notification.plain
        if status == self.state:
            return
        self.state = status
        for callback in list(self._callbacks):
            callback(status)

    def _disconnected(self, client: BleakClientWithServiceCache) -> None:
        _last_disconnect[self.address] = time.monotonic()
        if self._expected_disconnect:
            _LOGGER.debug("%s: disconnected", self.name)
        else:
            _LOGGER.debug("%s: disconnected unexpectedly", self.name)
        if client is self._client:
            self._client = None
        for waiters in self._waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(BleakError("Disconnected"))
        self._waiters.clear()

    def _schedule_disconnect(self) -> None:
        self._cancel_disconnect_timer()
        self._disconnect_timer = asyncio.get_running_loop().call_later(
            self._disconnect_delay, self._timed_disconnect
        )

    def _cancel_disconnect_timer(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    def _timed_disconnect(self) -> None:
        self._disconnect_timer = None
        self._disconnect_task = asyncio.create_task(self.disconnect())

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        self._expected_disconnect = True
        _LOGGER.debug("%s: disconnecting", self.name)
        try:
            await client.disconnect()
        except BleakError:
            _LOGGER.debug("%s: error while disconnecting", self.name, exc_info=True)
