"""Encoder and decoder for the AeroGarden Stem Grow Light BLE protocol.

Commands are plaintext writes to WRITE_CHAR_UUID. Status arrives as AES-CBC
encrypted notifications on NOTIFY_CHAR_UUID. Nothing here touches Bluetooth.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from enum import IntEnum

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ADVERTISED_SERVICE_UUID = "0000af30-0000-1000-8000-00805f9b34fb"
SERVICE_UUID = "0000ae3a-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000ae3b-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000ae3c-0000-1000-8000-00805f9b34fb"

AES_KEY = b"GrowLightBluetoo"
AES_IV = bytes.fromhex("123456789abcdef0123456789abcdef0")

PACKET_LEN = 25
SECONDS_PER_DAY = 86400

_MAGIC = 0xC5
_PAYLOAD_LEN = 0x13
_FRAME_STARTS = (0xA5, 0x5A)


class PacketType(IntEnum):
    """Packet type byte (offset 3)."""

    STATUS_PUSH = 0x03
    STATUS = 0x04
    CONTROL = 0x05


class Setting(IntEnum):
    """Bit numbers in the big-endian change mask of a control packet."""

    CLOCK = 12
    SCHEDULE = 11
    SUNRISE_SUNSET = 9
    SUNRISE_SUNSET_DURATION = 8
    BRIGHTNESS = 7
    LIGHT_MODE = 6
    DEFAULT_BRIGHTNESS = 5


class LightMode(IntEnum):
    """Light colour mode."""

    WHITE = 1
    FULL_SPECTRUM = 2
    WARM = 3
    COOL = 4


class PlantType(IntEnum):
    """Plant type chosen in the app during setup."""

    HERBS = 0
    TOMATOES = 1
    LETTUCE = 2
    PEPPERS = 3
    FLOWERS = 4
    SEED_STARTING = 5
    CUSTOM = 6
    GROW_BOWL = 7
    VEGGIES = 8
    SALADS = 9


@dataclass(frozen=True, slots=True)
class LightStatus:
    """Decoded status payload."""

    plant_type: int
    clock_seconds: int
    flags: int
    light_on_time: tuple[int, int]
    light_off_time: tuple[int, int]
    on_duration_minutes: int
    days_planted: int
    brightness: int
    sunrise_duration: int
    sunset_duration: int
    light_mode: int
    default_brightness: int
    clock_set: bool
    extra: bytes

    @property
    def light_on(self) -> bool:
        """Return whether the light is lit right now."""
        return bool(self.flags & 0x04)

    @property
    def brightness_override(self) -> bool:
        """Return whether brightness was changed until the next on or off time."""
        return bool(self.flags & 0x08)

    @property
    def sunrise_sunset_enabled(self) -> bool:
        """Return whether sunrise and sunset are on."""
        return bool(self.flags & 0x20)


@dataclass(frozen=True, slots=True)
class Notification:
    """A decrypted notification."""

    packet_type: int
    plain: bytes
    payload: bytes
    status: LightStatus | None

    @property
    def ack_ok(self) -> bool:
        """Return whether this is a control ack reporting success."""
        return self.packet_type == PacketType.CONTROL and self.payload == b"\x01"


def seconds_since_midnight(value: dt.time) -> int:
    """Return the number of whole seconds between midnight and value."""
    return value.hour * 3600 + value.minute * 60 + value.second


def _frame(packet: bytes | bytearray) -> bytes:
    """Wrap a packet in the 4-byte transport header."""
    return bytes((0x5A, len(packet), 0x00, 0x00)) + bytes(packet)


def _packet(packet_type: PacketType) -> bytearray:
    pkt = bytearray(PACKET_LEN)
    pkt[0:4] = bytes((_MAGIC, _PAYLOAD_LEN, 0x00, packet_type))
    pkt[4] = sum(pkt[0:4]) & 0xFF
    return pkt


def _control(setting: Setting, slots: dict[int, int] | None = None) -> bytes:
    """Build a framed control packet that changes one setting."""
    pkt = _packet(PacketType.CONTROL)
    pkt[5:7] = (1 << setting).to_bytes(2, "big")
    for offset, value in (slots or {}).items():
        pkt[offset] = value
    pkt[24] = sum(pkt[5:24]) & 0xFF
    return _frame(pkt)


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {value}")


def status_query() -> bytes:
    """Ask the light to report its status."""
    # The light accepts the query with the trailing checksum left at zero.
    return _frame(_packet(PacketType.STATUS))


def set_clock(seconds: int) -> bytes:
    """Set the light's clock to seconds since local midnight."""
    _check_range("seconds", seconds, 0, SECONDS_PER_DAY - 1)
    return _control(Setting.CLOCK, dict(enumerate(seconds.to_bytes(3, "little"), 7)))


def set_schedule(on: dt.time, off: dt.time) -> bytes:
    """Set the times of day the light turns on and off."""
    return _control(
        Setting.SCHEDULE,
        {10: on.hour, 11: on.minute, 12: off.hour, 13: off.minute},
    )


def set_sunrise_sunset_durations(sunrise: int, sunset: int) -> bytes:
    """Set the sunrise and sunset durations in minutes."""
    _check_range("sunrise", sunrise, 0, 255)
    _check_range("sunset", sunset, 0, 255)
    return _control(Setting.SUNRISE_SUNSET_DURATION, {16: sunrise, 17: sunset})


def toggle_sunrise_sunset() -> bytes:
    """Turn sunrise and sunset on if they are off, or off if they are on."""
    return _control(Setting.SUNRISE_SUNSET)


def set_brightness(percent: int) -> bytes:
    """Set the brightness until the next scheduled on or off time (0 = off)."""
    _check_range("percent", percent, 0, 100)
    return _control(Setting.BRIGHTNESS, {18: percent})


def set_default_brightness(percent: int) -> bytes:
    """Set the brightness the schedule uses."""
    _check_range("percent", percent, 0, 100)
    return _control(Setting.DEFAULT_BRIGHTNESS, {20: percent})


def set_light_mode(mode: LightMode) -> bytes:
    """Set the light colour mode."""
    return _control(Setting.LIGHT_MODE, {19: mode})


def encrypt(plain: bytes) -> bytes:
    """Encrypt like the light does. Only needed for tests and simulators."""
    pad = 16 - len(plain) % 16
    encryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).encryptor()
    return encryptor.update(plain + bytes((pad,)) * pad) + encryptor.finalize()


def _decrypt(ciphertext: bytes) -> bytes | None:
    if not ciphertext or len(ciphertext) % 16:
        return None
    decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV)).decryptor()
    plain = decryptor.update(ciphertext) + decryptor.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16:
        plain = plain[:-pad]
    return plain


def decrypt_notification(value: bytes | bytearray) -> bytes | None:
    """Return the decrypted packet, or None if value is not a framed packet."""
    if len(value) < 5 or value[0] not in _FRAME_STARTS:
        return None
    return _decrypt(bytes(value[4:]))


def _parse_status(p: bytes) -> LightStatus | None:
    # The fields below take 19 bytes; the light sends 21, the last two unused.
    if len(p) < 19:
        return None
    return LightStatus(
        plant_type=p[0],
        clock_seconds=int.from_bytes(p[1:4], "little"),
        flags=p[4],
        light_on_time=(p[5], p[6]),
        light_off_time=(p[7], p[8]),
        on_duration_minutes=int.from_bytes(p[9:11], "little"),
        days_planted=int.from_bytes(p[11:13], "little"),
        brightness=p[13],
        sunrise_duration=p[14],
        sunset_duration=p[15],
        light_mode=p[16],
        default_brightness=p[17],
        clock_set=bool(p[18] & 0x02),
        extra=bytes(p[19:]),
    )


def parse_notification(value: bytes | bytearray) -> Notification | None:
    """Decode a notification, or return None if it is not a framed packet."""
    plain = decrypt_notification(value)
    if plain is None or len(plain) < 5 or plain[0] != _MAGIC:
        return None
    packet_type = plain[3]
    payload = plain[5 : 5 + plain[1]]
    status = None
    if packet_type in (PacketType.STATUS_PUSH, PacketType.STATUS):
        status = _parse_status(payload)
    return Notification(
        packet_type=packet_type, plain=plain, payload=payload, status=status
    )
