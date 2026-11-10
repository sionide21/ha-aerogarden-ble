"""Tests for the Stem light protocol encoder and decoder."""

import datetime as dt

import pytest

from custom_components.aerogarden_ble.stemlight import protocol
from custom_components.aerogarden_ble.stemlight.protocol import LightMode

# A real light's reply to a status query.
STATUS_NOTIFICATION = bytes.fromhex(
    "a5 20 00 01 16 55 75 23 b6 86 41 6a 2b a2 51 78 fe b9"
    " d1 c0 85 33 86 39 9b b2 de 4d 0b 63 b8 77 8c 9e 09 f7"
)
STATUS_PLAIN = bytes.fromhex(
    "c5 15 00 04 de 00 a8 74 00 84 08 00 12 00 58 02 26 01 64 1e 1e 02 64 01 00 00 42"
)


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (
            protocol.status_query(),
            "5a 19 00 00 c5 13 00 04 dc 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00",
        ),
        (
            protocol.set_brightness(50),
            "5a 19 00 00 c5 13 00 05 dd 00 80 00 00 00 00 00 00 00 00 00 00 00 32 00 00 00 00 00 b2",
        ),
        (
            protocol.set_brightness(0),
            "5a 19 00 00 c5 13 00 05 dd 00 80 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 80",
        ),
        (
            protocol.set_default_brightness(80),
            "5a 19 00 00 c5 13 00 05 dd 00 20 00 00 00 00 00 00 00 00 00 00 00 00 00 50 00 00 00 70",
        ),
        (
            protocol.set_light_mode(LightMode.WARM),
            "5a 19 00 00 c5 13 00 05 dd 00 40 00 00 00 00 00 00 00 00 00 00 00 00 03 00 00 00 00 43",
        ),
        (
            protocol.set_schedule(dt.time(7, 0), dt.time(23, 0)),
            "5a 19 00 00 c5 13 00 05 dd 08 00 00 00 00 07 00 17 00 00 00 00 00 00 00 00 00 00 00 26",
        ),
        (
            protocol.set_sunrise_sunset_durations(15, 30),
            "5a 19 00 00 c5 13 00 05 dd 01 00 00 00 00 00 00 00 00 00 00 0f 1e 00 00 00 00 00 00 2e",
        ),
        (
            protocol.toggle_sunrise_sunset(),
            "5a 19 00 00 c5 13 00 05 dd 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 02",
        ),
        (
            protocol.set_clock(protocol.seconds_since_midnight(dt.time(12, 34, 56))),
            "5a 19 00 00 c5 13 00 05 dd 10 00 f0 b0 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 b0",
        ),
    ],
)
def test_frames(frame: bytes, expected: str) -> None:
    """Every encoder produces the bytes the light expects."""
    assert frame.hex(" ") == expected


@pytest.mark.parametrize(
    ("encoder", "value"),
    [
        (protocol.set_brightness, 101),
        (protocol.set_brightness, -1),
        (protocol.set_default_brightness, 101),
        (protocol.set_clock, protocol.SECONDS_PER_DAY),
    ],
)
def test_encoders_reject_out_of_range(encoder, value: int) -> None:
    """Out-of-range values raise instead of wrapping into a byte."""
    with pytest.raises(ValueError):
        encoder(value)


def test_encrypt_matches_light() -> None:
    """The simulator's encryption reproduces a real notification."""
    assert protocol.encrypt(STATUS_PLAIN) == STATUS_NOTIFICATION[4:]


def test_parse_real_status() -> None:
    """A real status notification decodes into its fields."""
    notification = protocol.parse_notification(STATUS_NOTIFICATION)

    assert notification is not None
    assert notification.plain == STATUS_PLAIN
    assert notification.packet_type == protocol.PacketType.STATUS
    status = notification.status
    assert status == protocol.LightStatus(
        plant_type=0,
        clock_seconds=0x0074A8,
        flags=0x84,
        light_on_time=(8, 0),
        light_off_time=(18, 0),
        on_duration_minutes=600,
        days_planted=294,
        brightness=100,
        sunrise_duration=30,
        sunset_duration=30,
        light_mode=LightMode.FULL_SPECTRUM,
        default_brightness=100,
        clock_set=False,
        extra=b"\x00\x00",
    )
    assert status.light_on
    assert not status.brightness_override
    assert not status.sunrise_sunset_enabled


def test_parse_ack() -> None:
    """A control ack decodes without a status."""
    plain = bytes.fromhex("c5010005cb0101")
    notification = protocol.parse_notification(
        b"\xa5\x10\x00\x01" + protocol.encrypt(plain)
    )

    assert notification is not None
    assert notification.packet_type == protocol.PacketType.CONTROL
    assert notification.ack_ok
    assert notification.status is None


def test_parse_short_status() -> None:
    """A status without the two unused trailing bytes still decodes."""
    payload = bytes(
        [2, 0, 0, 0, 0x24, 7, 0, 23, 0, 0x84, 3, 5, 0, 60, 15, 30, 3, 80, 0x02]
    )
    plain = (
        bytes((0xC5, 0x13, 0x00, 0x03, 0xDB)) + payload + bytes((sum(payload) & 0xFF,))
    )
    notification = protocol.parse_notification(
        b"\xa5\x20\x00\x01" + protocol.encrypt(plain)
    )

    assert notification is not None
    assert notification.status is not None
    assert notification.status.on_duration_minutes == 900
    assert notification.status.sunrise_sunset_enabled
    assert notification.status.extra == b""


@pytest.mark.parametrize(
    "value",
    [
        b"",
        b"\x00\x19\x00\x00" + bytes(32),
        b"\xa5\x19\x00\x00" + bytes(31),
        b"\xa5\x19\x00\x00" + bytes(32),
    ],
)
def test_parse_rejects_garbage(value: bytes) -> None:
    """Unframed, misaligned or wrongly keyed data is ignored."""
    assert protocol.parse_notification(value) is None
