"""Tests for the AeroGarden Bluetooth entities."""

from collections.abc import Awaitable, Callable
import datetime as dt
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.aerogarden_ble import PLATFORMS
from custom_components.aerogarden_ble.stemlight import LightMode, protocol
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from . import setup_integration

from tests.fake_light import FakeStemLight

PREFIX = "stem_growlight_eeff"
MONOTONIC = "custom_components.aerogarden_ble.coordinator.monotonic_time_coarse"


@pytest.mark.parametrize("platform", PLATFORMS)
async def test_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    fake_light: FakeStemLight,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    platform: Platform,
) -> None:
    """All entities and their states."""
    with patch("custom_components.aerogarden_ble.PLATFORMS", [platform]):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("domain", "service", "entity", "data", "frame"),
    [
        ("light", "turn_off", "light", {}, protocol.set_brightness(0)),
        (
            "light",
            "turn_on",
            "light",
            {"brightness": 128},
            protocol.set_brightness(50),
        ),
        ("light", "turn_on", "light", {"brightness": 1}, protocol.set_brightness(1)),
        (
            "select",
            "select_option",
            "light_mode",
            {"option": "warm"},
            protocol.set_light_mode(LightMode.WARM),
        ),
        (
            "number",
            "set_value",
            "default_brightness",
            {"value": 70},
            protocol.set_default_brightness(70),
        ),
        (
            "number",
            "set_value",
            "sunrise_duration",
            {"value": 15},
            protocol.set_sunrise_sunset_durations(15, 30),
        ),
        (
            "number",
            "set_value",
            "sunset_duration",
            {"value": 45},
            protocol.set_sunrise_sunset_durations(30, 45),
        ),
        (
            "time",
            "set_value",
            "light_on_time",
            {"time": "06:30:00"},
            protocol.set_schedule(dt.time(6, 30), dt.time(18, 0)),
        ),
        (
            "time",
            "set_value",
            "light_off_time",
            {"time": "21:15:00"},
            protocol.set_schedule(dt.time(8, 0), dt.time(21, 15)),
        ),
        (
            "switch",
            "turn_on",
            "sunrise_and_sunset",
            {},
            protocol.toggle_sunrise_sunset(),
        ),
    ],
)
async def test_commands(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    domain: str,
    service: str,
    entity: str,
    data: dict[str, Any],
    frame: bytes,
) -> None:
    """Each control writes one frame and the state follows the light."""
    entity_id = (
        f"{domain}.{PREFIX}" if entity == "light" else f"{domain}.{PREFIX}_{entity}"
    )
    before = len(fake_light.control_writes)

    await hass.services.async_call(
        domain, service, {"entity_id": entity_id, **data}, blocking=True
    )

    assert fake_light.control_writes[before:] == [frame]


async def test_light_states(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Off and on follow the light; turning on uses the default brightness."""
    entity_id = f"light.{PREFIX}"
    fake_light.default_brightness = 80

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
    assert state.attributes["brightness"] == 204
    assert fake_light.control_writes[-1] == protocol.set_brightness(80)

    # Already on: nothing to send.
    before = len(fake_light.writes)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert len(fake_light.writes) == before


async def test_sunrise_switch_off_when_off(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Turning the ramps off when they are off sends no toggle."""
    before = len(fake_light.control_writes)

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": f"switch.{PREFIX}_sunrise_and_sunset"},
        blocking=True,
    )

    assert len(fake_light.control_writes) == before
    assert hass.states.get(f"switch.{PREFIX}_sunrise_and_sunset").state == STATE_OFF


async def test_command_when_light_busy(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Without recent advertisements, commands fail at once instead of timing out."""
    assert fake_light.client is not None
    fake_light.client.drop()

    with (
        patch(MONOTONIC, return_value=1e12),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": f"light.{PREFIX}"}, blocking=True
        )

    assert err.value.translation_key == "light_busy"
    assert fake_light.connect_count == 1


async def test_command_after_own_disconnect(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Right after Home Assistant lets go, the light counts as reachable."""
    assert fake_light.client is not None
    fake_light.client.drop()
    last = init_integration.runtime_data.device.last_disconnected
    assert last is not None

    with patch(MONOTONIC, return_value=last + 5):
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": f"light.{PREFIX}"}, blocking=True
        )

    assert fake_light.connect_count == 2


async def test_command_error(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """A failed command raises a translated error."""
    fake_light.ack_payload = b"\x00"

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": f"light.{PREFIX}"}, blocking=True
        )

    assert err.value.translation_key == "communication_error"


async def test_unknown_light_mode(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """A mode value outside the known modes shows as unknown."""
    fake_light.mode = 0
    fake_light.push_status()
    await hass.async_block_till_done()

    assert hass.states.get(f"select.{PREFIX}_light_mode").state == "unknown"


async def test_unknown_plant_type(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """A plant type newer than this integration shows as unknown."""
    with patch.object(fake_light, "plant_type", 42):
        fake_light.push_status()
        await hass.async_block_till_done()

    assert hass.states.get(f"sensor.{PREFIX}_plant_type").state == "unknown"


async def _press_refresh(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": f"button.{PREFIX}_refresh"}, blocking=True
    )


async def _update_entity(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": f"sensor.{PREFIX}_days_planted"},
        blocking=True,
    )


@pytest.mark.parametrize("refresh", [_press_refresh, _update_entity])
async def test_refresh(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    fake_light: FakeStemLight,
    refresh: Callable[[HomeAssistant], Awaitable[None]],
) -> None:
    """The button and the update entity action read the light right away."""
    fake_light.brightness = 40
    fake_light.days_planted = 295
    before = len(fake_light.writes)

    await refresh(hass)

    assert fake_light.writes[before:] == [protocol.status_query()]
    assert hass.states.get(f"light.{PREFIX}").attributes["brightness"] == 102
    assert hass.states.get(f"sensor.{PREFIX}_days_planted").state == "295"


async def test_refresh_brings_light_back(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """A successful refresh ends an unavailable spell straight away."""
    coordinator = init_integration.runtime_data
    coordinator.last_poll_successful = False
    coordinator.async_update_listeners()
    assert hass.states.get(f"light.{PREFIX}").state == STATE_UNAVAILABLE
    # The button stays usable, since refreshing is how to retry.
    assert hass.states.get(f"button.{PREFIX}_refresh").state != STATE_UNAVAILABLE

    await _press_refresh(hass)

    assert coordinator.last_poll_successful
    assert hass.states.get(f"light.{PREFIX}").state == STATE_ON


async def test_refresh_when_light_busy(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_light: FakeStemLight
) -> None:
    """Refreshing a light that something else holds fails at once."""
    assert fake_light.client is not None
    fake_light.client.drop()

    with (
        patch(MONOTONIC, return_value=1e12),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _press_refresh(hass)

    assert err.value.translation_key == "light_busy"
    assert fake_light.connect_count == 1
