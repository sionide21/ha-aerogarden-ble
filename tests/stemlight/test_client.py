"""Tests for the Stem light Bluetooth client, against the simulated light."""

import asyncio
from collections.abc import AsyncGenerator
import datetime as dt
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakNotFoundError
import pytest

from custom_components.aerogarden_ble.stemlight import protocol
from custom_components.aerogarden_ble.stemlight.client import StemLight
from custom_components.aerogarden_ble.stemlight.exceptions import (
    StemLightCommandError,
    StemLightConnectionError,
    StemLightTimeoutError,
)
from custom_components.aerogarden_ble.stemlight.protocol import LightMode, LightStatus

from tests.fake_light import FakeStemLight

NOW = dt.time(22, 5, 41)
DEVICE = BLEDevice("AA:BB:CC:DD:EE:FF", "Stem GrowLight", None)


@pytest.fixture
def light() -> FakeStemLight:
    return FakeStemLight()


@pytest.fixture
async def client(light: FakeStemLight) -> AsyncGenerator[StemLight]:
    with patch(
        "custom_components.aerogarden_ble.stemlight.client.establish_connection",
        light.establish_connection,
    ):
        stem = StemLight(DEVICE, now=lambda: NOW, disconnect_delay=0.05)
        yield stem
        await stem.disconnect()


async def test_update_syncs_clock_after_power_loss(
    client: StemLight, light: FakeStemLight
) -> None:
    """A light whose clock reset gets the time set on connect."""
    status = await client.update()

    assert [w[7] for w in light.writes] == [0x04, 0x05, 0x04]
    assert light.control_writes == [
        protocol.set_clock(protocol.seconds_since_midnight(NOW))
    ]
    assert status.clock_set
    # 22:05 is outside 08:00-18:00, so the schedule switched it off.
    assert not status.light_on
    assert client.state == status


async def test_update_leaves_synced_clock_alone(
    client: StemLight, light: FakeStemLight
) -> None:
    """A set clock within tolerance is not rewritten."""
    light.set_time(dt.time(22, 6, 30))

    await client.update()

    assert light.control_writes == []


async def test_update_fixes_drift(client: StemLight, light: FakeStemLight) -> None:
    """A clock that drifted, for example over a DST change, is reset."""
    light.set_time(dt.time(23, 5, 41))

    await client.update()

    assert len(light.control_writes) == 1


async def test_drift_wraps_midnight(light: FakeStemLight) -> None:
    """Drift is measured the short way round midnight."""
    light.set_time(dt.time(0, 0, 10))
    with patch(
        "custom_components.aerogarden_ble.stemlight.client.establish_connection",
        light.establish_connection,
    ):
        stem = StemLight(DEVICE, now=lambda: dt.time(23, 59, 50))
        await stem.update()
        await stem.disconnect()

    assert light.control_writes == []


async def test_set_brightness(client: StemLight, light: FakeStemLight) -> None:
    """Brightness is written and the returned status comes from the light."""
    light.set_time(NOW)

    status = await client.set_brightness(50)

    assert light.control_writes == [protocol.set_brightness(50)]
    assert status.brightness == 50
    assert status.light_on
    assert status.brightness_override


async def test_invalid_value_does_not_connect(
    client: StemLight, light: FakeStemLight
) -> None:
    """Out-of-range values fail before connecting."""
    with pytest.raises(ValueError):
        await client.set_brightness(101)

    assert light.connect_count == 0


@pytest.mark.parametrize(
    ("call", "frame", "check"),
    [
        (
            lambda c: c.set_default_brightness(80),
            protocol.set_default_brightness(80),
            lambda s: s.default_brightness == 80,
        ),
        (
            lambda c: c.set_light_mode(LightMode.WARM),
            protocol.set_light_mode(LightMode.WARM),
            lambda s: s.light_mode == LightMode.WARM,
        ),
        (
            lambda c: c.set_schedule(dt.time(6, 30), dt.time(22, 30)),
            protocol.set_schedule(dt.time(6, 30), dt.time(22, 30)),
            lambda s: s.light_on_time == (6, 30) and s.light_on,
        ),
        (
            lambda c: c.set_sunrise_sunset_durations(15, 45),
            protocol.set_sunrise_sunset_durations(15, 45),
            lambda s: (s.sunrise_duration, s.sunset_duration) == (15, 45),
        ),
    ],
)
async def test_commands(
    client: StemLight, light: FakeStemLight, call, frame: bytes, check
) -> None:
    """Each setting is written as one control frame."""
    light.set_time(NOW)

    status = await call(client)

    assert light.control_writes == [frame]
    assert check(status)


@pytest.mark.parametrize("enabled", [True, False])
async def test_sunrise_sunset_toggles_only_when_needed(
    client: StemLight, light: FakeStemLight, enabled: bool
) -> None:
    """The toggle is sent only when the state differs."""
    light.set_time(NOW)
    light.sunrise_sunset = not enabled

    status = await client.set_sunrise_sunset_enabled(enabled)
    again = await client.set_sunrise_sunset_enabled(enabled)

    assert light.control_writes == [protocol.toggle_sunrise_sunset()]
    assert status.sunrise_sunset_enabled is enabled
    assert again.sunrise_sunset_enabled is enabled


async def test_sync_clock(client: StemLight, light: FakeStemLight) -> None:
    """sync_clock always writes the clock."""
    light.set_time(NOW)

    await client.sync_clock()

    assert light.control_writes == [
        protocol.set_clock(protocol.seconds_since_midnight(NOW))
    ]


async def test_timeout(client: StemLight, light: FakeStemLight) -> None:
    """A light that stops answering raises and drops the connection."""
    light.respond = False

    with (
        patch(
            "custom_components.aerogarden_ble.stemlight.client.RESPONSE_TIMEOUT", 0.01
        ),
        pytest.raises(StemLightTimeoutError),
    ):
        await client.update()

    assert not client.is_connected


async def test_rejected_command(client: StemLight, light: FakeStemLight) -> None:
    """A negative ack raises."""
    light.set_time(NOW)
    light.ack_payload = b"\x00"

    with pytest.raises(StemLightCommandError):
        await client.set_brightness(10)


async def test_not_found(client: StemLight, light: FakeStemLight) -> None:
    """A light that can't be reached raises a connection error."""
    with (
        patch(
            "custom_components.aerogarden_ble.stemlight.client.establish_connection",
            side_effect=BleakNotFoundError("gone"),
        ),
        pytest.raises(StemLightConnectionError),
    ):
        await client.update()


async def test_reconnects_after_drop(client: StemLight, light: FakeStemLight) -> None:
    """A connection lost mid-request is retried on a new connection."""
    light.set_time(NOW)
    await client.update()
    light.drop_on_next_write = True

    status = await client.set_brightness(30)

    assert status.brightness == 30
    assert light.connect_count == 2


async def test_idle_disconnect(client: StemLight, light: FakeStemLight) -> None:
    """The connection closes after the idle delay and reopens on demand."""
    light.set_time(NOW)
    await client.update()
    assert client.is_connected

    await asyncio.sleep(0.1)

    assert not client.is_connected
    await client.update()
    assert light.connect_count == 2


async def test_reconnect_waits_after_disconnect(light: FakeStemLight) -> None:
    """A new connection waits for the light to recover from the last one."""
    light.set_time(NOW)
    with (
        patch(
            "custom_components.aerogarden_ble.stemlight.client.establish_connection",
            light.establish_connection,
        ),
        patch("custom_components.aerogarden_ble.stemlight.client.RECONNECT_DELAY", 0.2),
    ):
        first = StemLight(DEVICE, now=lambda: NOW)
        await first.update()
        await first.disconnect()

        # A different client for the same light, like setup after the config flow.
        second = StemLight(DEVICE, now=lambda: NOW)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await second.update()
        elapsed = loop.time() - start
        await second.disconnect()

    assert elapsed >= 0.15


async def test_push_updates_state(client: StemLight, light: FakeStemLight) -> None:
    """Unsolicited status updates the state and calls back only on change."""
    light.set_time(NOW)
    seen: list[LightStatus] = []
    unsubscribe = client.register_callback(seen.append)
    await client.update()
    assert len(seen) == 1

    light.push_status()
    await asyncio.sleep(0)
    assert len(seen) == 1

    light.brightness = 40
    light.push_status()
    await asyncio.sleep(0)
    assert [s.brightness for s in seen] == [100, 40]
    assert client.state is not None
    assert client.state.brightness == 40

    unsubscribe()
    light.brightness = 20
    light.push_status()
    await asyncio.sleep(0)
    assert len(seen) == 2


async def test_garbage_notification_ignored(
    client: StemLight, light: FakeStemLight
) -> None:
    """Unparseable notifications don't disturb the client."""
    light.set_time(NOW)
    await client.update()
    assert light.client is not None
    assert light.client.notify_callback is not None

    light.client.notify_callback(None, bytearray(b"\x00\x01"))

    assert client.state is not None
    assert client.last_status_packet is not None
