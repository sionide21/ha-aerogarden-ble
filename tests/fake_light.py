"""A simulated Stem light behind a fake BleakClient.

It behaves like a real light: every control write is acked and followed by a
status push, status queries get a type 0x04 reply, and writing the clock or
schedule re-applies the schedule.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import datetime as dt
import time
from typing import Any

from custom_components.aerogarden_ble.stemlight import protocol
from custom_components.aerogarden_ble.stemlight.protocol import PacketType, Setting

NotifyCallback = Callable[[Any, bytearray], None]


def _minutes(hour_minute: tuple[int, int]) -> int:
    return hour_minute[0] * 60 + hour_minute[1]


class FakeBleakClient:
    """Stands in for BleakClientWithServiceCache."""

    def __init__(
        self,
        light: FakeStemLight,
        disconnected_callback: Callable[[FakeBleakClient], None] | None,
    ) -> None:
        self.light = light
        self.is_connected = True
        self._disconnected_callback = disconnected_callback
        self.notify_callback: NotifyCallback | None = None

    async def start_notify(self, uuid: str, callback: NotifyCallback) -> None:
        assert uuid == protocol.NOTIFY_CHAR_UUID
        self.notify_callback = callback

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool) -> None:
        assert uuid == protocol.WRITE_CHAR_UUID
        assert response is False, "ae3b only supports write-without-response"
        await self.light.handle_write(self, bytes(data))

    async def disconnect(self) -> bool:
        self.drop()
        return True

    def drop(self) -> None:
        """Lose the connection, telling the client like bleak would."""
        if not self.is_connected:
            return
        self.is_connected = False
        if self._disconnected_callback:
            self._disconnected_callback(self)


class FakeStemLight:
    """Simulated light state and behaviour."""

    def __init__(self) -> None:
        self.plant_type = protocol.PlantType.HERBS
        # A power cycle resets the clock to 08:00.
        self._clock_base = 8 * 3600
        self._clock_set_at = time.time()
        self.clock_set = False
        self.sunrise_sunset = False
        self.override = False
        self.light_on = True
        self.on_time = (8, 0)
        self.off_time = (18, 0)
        self.days_planted = 294
        self.brightness = 100
        self.sunrise = 30
        self.sunset = 30
        self.mode: int = protocol.LightMode.FULL_SPECTRUM
        self.default_brightness = 100
        self.writes: list[bytes] = []
        self.connect_count = 0
        self.respond = True
        self.ack_payload = b"\x01"
        self.drop_on_next_write = False
        self.client: FakeBleakClient | None = None

    async def establish_connection(
        self,
        client_class: type,
        device: Any,
        name: str,
        disconnected_callback: Callable[[FakeBleakClient], None] | None = None,
        **kwargs: Any,
    ) -> FakeBleakClient:
        """Replace bleak_retry_connector.establish_connection."""
        self.connect_count += 1
        self.client = FakeBleakClient(self, disconnected_callback)
        return self.client

    @property
    def control_writes(self) -> list[bytes]:
        """Return the control frames written so far."""
        return [w for w in self.writes if w[7] == PacketType.CONTROL]

    @property
    def clock_seconds(self) -> int:
        """Return the light's clock, which runs with (frozen) wall time."""
        elapsed = int(time.time() - self._clock_set_at)
        return (self._clock_base + elapsed) % protocol.SECONDS_PER_DAY

    @clock_seconds.setter
    def clock_seconds(self, value: int) -> None:
        self._clock_base = value
        self._clock_set_at = time.time()

    def set_time(self, value: dt.time) -> None:
        """Set the light's clock without a write, as if it had been running."""
        self.clock_seconds = protocol.seconds_since_midnight(value)
        self.clock_set = True

    def flags(self) -> int:
        """Return the status flags byte."""
        return (
            (0x04 if self.light_on else 0)
            | (0x08 if self.override else 0)
            | (0x20 if self.sunrise_sunset else 0)
        )

    def status_plain(self, packet_type: int) -> bytes:
        """Return a decrypted status packet like the light sends."""
        on_duration = (_minutes(self.off_time) - _minutes(self.on_time)) % 1440
        payload = bytes(
            [
                self.plant_type,
                *self.clock_seconds.to_bytes(3, "little"),
                self.flags(),
                *self.on_time,
                *self.off_time,
                *on_duration.to_bytes(2, "little"),
                *self.days_planted.to_bytes(2, "little"),
                self.brightness,
                self.sunrise,
                self.sunset,
                self.mode,
                self.default_brightness,
                # Bit 0 is always set.
                0x01 | (0x02 if self.clock_set else 0),
                0,
                0,
            ]
        )
        return self._packet(packet_type, payload)

    @staticmethod
    def _packet(packet_type: int, payload: bytes) -> bytes:
        header = bytes((0xC5, len(payload), 0x00, packet_type))
        return (
            header
            + bytes((sum(header) & 0xFF,))
            + payload
            + bytes((sum(payload) & 0xFF,))
        )

    @staticmethod
    def frame(plain: bytes) -> bytearray:
        """Encrypt and frame a packet the way the light notifies it."""
        ciphertext = protocol.encrypt(plain)
        return bytearray(bytes((0xA5, len(ciphertext), 0x00, 0x01)) + ciphertext)

    def notify(self, plain: bytes) -> None:
        """Send a notification to the connected client."""
        assert self.client is not None
        assert self.client.notify_callback is not None
        asyncio.get_running_loop().call_soon(
            self.client.notify_callback, None, self.frame(plain)
        )

    def push_status(self) -> None:
        """Send an unsolicited status, like the light does every minute."""
        self.notify(self.status_plain(PacketType.STATUS_PUSH))

    async def handle_write(self, client: FakeBleakClient, data: bytes) -> None:
        """React to a write the way the light does."""
        self.writes.append(data)
        if self.drop_on_next_write:
            self.drop_on_next_write = False
            client.drop()
            return
        if not self.respond:
            return
        assert data[:4] == bytes((0x5A, 0x19, 0x00, 0x00))
        packet = data[4:]
        if packet[3] == PacketType.STATUS:
            self.notify(self.status_plain(PacketType.STATUS))
            return
        assert packet[3] == PacketType.CONTROL
        self._apply(int.from_bytes(packet[5:7], "big"), packet)
        self.notify(self._packet(PacketType.CONTROL, self.ack_payload))
        self.push_status()

    def _apply(self, mask: int, packet: bytes) -> None:
        if mask == 1 << Setting.CLOCK:
            self.clock_seconds = int.from_bytes(packet[7:10], "little")
            self.clock_set = True
            self._follow_schedule()
        elif mask == 1 << Setting.SCHEDULE:
            self.on_time = (packet[10], packet[11])
            self.off_time = (packet[12], packet[13])
            self._follow_schedule()
        elif mask == 1 << Setting.SUNRISE_SUNSET:
            self.sunrise_sunset = not self.sunrise_sunset
        elif mask == 1 << Setting.SUNRISE_SUNSET_DURATION:
            self.sunrise, self.sunset = packet[16], packet[17]
        elif mask == 1 << Setting.BRIGHTNESS:
            self.brightness = packet[18]
            self.light_on = packet[18] > 0
            self.override = True
        elif mask == 1 << Setting.LIGHT_MODE:
            self.mode = packet[19]
        elif mask == 1 << Setting.DEFAULT_BRIGHTNESS:
            self.default_brightness = packet[20]
            if self.light_on and not self.override:
                self.brightness = self.default_brightness
        else:
            raise AssertionError(f"unexpected control mask {mask:#06x}")

    def _follow_schedule(self) -> None:
        now = self.clock_seconds // 60
        on, off = _minutes(self.on_time), _minutes(self.off_time)
        lit = on <= now < off if on <= off else now >= on or now < off
        self.override = False
        self.light_on = lit
        self.brightness = self.default_brightness if lit else 0
