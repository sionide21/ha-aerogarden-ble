"""Tests for polling the light."""

from collections.abc import Generator
from dataclasses import replace
import datetime as dt
import logging
from unittest.mock import patch

from bluetooth_data_tools import monotonic_time_coarse
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.components.bluetooth import (
    inject_bluetooth_service_info,
    patch_all_discovered_devices,
)

from custom_components.aerogarden_ble.coordinator import crossed_boundary
from custom_components.aerogarden_ble.stemlight import LightStatus
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import ADDRESS, STEM_SERVICE_INFO, make_service_info

from tests.fake_light import FakeStemLight

LIGHT = "light.stem_growlight_eeff"


@pytest.fixture(autouse=True)
def light_in_range() -> Generator[None]:
    """Keep the light in the scanner's list across time jumps."""
    with patch_all_discovered_devices([STEM_SERVICE_INFO.device]):
        yield


STATUS = LightStatus(
    plant_type=0,
    clock_seconds=0,
    flags=0x04,
    light_on_time=(8, 0),
    light_off_time=(18, 0),
    on_duration_minutes=600,
    days_planted=1,
    brightness=100,
    sunrise_duration=30,
    sunset_duration=30,
    light_mode=2,
    default_brightness=100,
    clock_set=True,
    extra=b"",
)


def _at(hour: int, minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(
        2026, 11, 9, hour, minute, second, tzinfo=dt_util.get_default_time_zone()
    )


@pytest.mark.parametrize(
    ("status", "since", "now", "expected"),
    [
        (STATUS, _at(7, 59), _at(8, 1), True),
        (STATUS, _at(8, 1), _at(8, 4), False),
        (STATUS, _at(17, 58), _at(18, 0, 30), True),
        # The boundary is acted on a few seconds late.
        (STATUS, _at(7, 59), _at(8, 0, 5), False),
        # Ramp ends only count when the ramps are enabled.
        (STATUS, _at(8, 25), _at(8, 31), False),
        (replace(STATUS, flags=0x24), _at(8, 25), _at(8, 31), True),
        (replace(STATUS, flags=0x24), _at(17, 25), _at(17, 31), True),
        # Across midnight.
        (
            replace(STATUS, light_on_time=(0, 0), light_off_time=(23, 59)),
            _at(23, 59, 30) - dt.timedelta(days=1),
            _at(0, 1),
            True,
        ),
    ],
)
def test_crossed_boundary(
    status: LightStatus, since: dt.datetime, now: dt.datetime, expected: bool
) -> None:
    """Polls follow the light's own schedule changes."""
    assert crossed_boundary(status, since, now) is expected


async def _advertise_and_wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: dt.timedelta
) -> None:
    """Move time on while the light keeps advertising, as it does."""
    freezer.tick(delta)
    # The light's advertisement never changes, so the bluetooth manager
    # doesn't pass these on to the coordinator; only their time is kept.
    inject_bluetooth_service_info(
        hass,
        make_service_info(
            "Stem GrowLight",
            ADDRESS,
            STEM_SERVICE_INFO.manufacturer_data,
            # The manager judges staleness by the real monotonic clock.
            time=monotonic_time_coarse(),
        ),
    )
    async_fire_time_changed(hass)
    # Polls run as background tasks.
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_polls_every_five_minutes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Changes made outside Home Assistant show up on the next poll."""
    fake_light.brightness = 40

    await _advertise_and_wait(hass, frozen_time, dt.timedelta(minutes=1))
    assert hass.states.get(LIGHT).attributes["brightness"] == 255

    await _advertise_and_wait(hass, frozen_time, dt.timedelta(minutes=5))
    assert hass.states.get(LIGHT).attributes["brightness"] == 102


async def test_no_poll_while_light_busy(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """While the light is silent, such as when the app has it, no poll is tried."""
    assert fake_light.client is not None
    fake_light.client.drop()

    frozen_time.tick(dt.timedelta(minutes=6))
    with patch(
        "custom_components.aerogarden_ble.coordinator.monotonic_time_coarse",
        return_value=1e12,
    ):
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert fake_light.connect_count == 1


async def test_polls_at_schedule_boundary(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """The light switching itself off is picked up straight away."""
    # Setup ran at 12:00; schedule the off time for 12:02.
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": "time.stem_growlight_eeff_light_off_time", "time": "12:02:00"},
        blocking=True,
    )
    assert hass.states.get(LIGHT).state == STATE_ON

    fake_light.light_on = False
    fake_light.brightness = 0
    await _advertise_and_wait(hass, frozen_time, dt.timedelta(minutes=2, seconds=15))

    assert hass.states.get(LIGHT).state == STATE_OFF


async def test_unavailable_and_back(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    frozen_time: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed poll marks entities unavailable and logs once."""
    caplog.set_level(logging.INFO)
    assert fake_light.client is not None
    fake_light.client.drop()

    with patch(
        "custom_components.aerogarden_ble.stemlight.client.establish_connection",
        side_effect=TimeoutError,
    ):
        await _advertise_and_wait(hass, frozen_time, dt.timedelta(minutes=5))
        assert hass.states.get(LIGHT).state == STATE_UNAVAILABLE

        await _advertise_and_wait(hass, frozen_time, dt.timedelta(minutes=1))
        assert caplog.text.count("is unavailable") == 1

    await _advertise_and_wait(hass, frozen_time, dt.timedelta(minutes=1))
    assert hass.states.get(LIGHT).state == STATE_ON
    assert caplog.text.count("is back online") == 1
