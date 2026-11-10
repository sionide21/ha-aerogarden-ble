"""Tests for the AeroGarden Bluetooth config flow."""

from unittest.mock import patch

from bleak_retry_connector import BleakNotFoundError
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.bluetooth import inject_bluetooth_service_info

from custom_components.aerogarden_ble.const import DOMAIN
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import ADDRESS, OTHER_JIELI_SERVICE_INFO, STEM_SERVICE_INFO

from tests.fake_light import FakeStemLight

CONNECT = "custom_components.aerogarden_ble.stemlight.client.establish_connection"


@pytest.fixture(autouse=True)
def mock_setup_entry(hass: HomeAssistant):
    """Don't set up the entry the flow creates."""
    with patch("custom_components.aerogarden_ble.async_setup_entry", return_value=None):
        yield


async def test_bluetooth_discovery(
    hass: HomeAssistant, fake_light: FakeStemLight
) -> None:
    """A discovered light is confirmed, tested and added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=STEM_SERVICE_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": "Stem GrowLight (EEFF)"}
    # Nothing connects before the user confirms.
    assert fake_light.connect_count == 0

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Stem GrowLight (EEFF)"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"
    # The test connection also set the light's clock.
    assert fake_light.clock_set
    assert not fake_light.client.is_connected


async def test_bluetooth_discovery_not_supported(hass: HomeAssistant) -> None:
    """Other devices with the same service UUID are rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=OTHER_JIELI_SERVICE_INFO
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A configured light is not offered again."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=STEM_SERVICE_INFO
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(BleakNotFoundError("gone"), "cannot_connect"), (RuntimeError, "unknown")],
)
async def test_bluetooth_confirm_errors(
    hass: HomeAssistant,
    fake_light: FakeStemLight,
    side_effect: Exception,
    error: str,
) -> None:
    """Connection problems are shown and the user can retry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=STEM_SERVICE_INFO
    )
    with patch(CONNECT, side_effect=side_effect):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step(hass: HomeAssistant, fake_light: FakeStemLight) -> None:
    """The user picks from lights in range; other devices are not listed."""
    inject_bluetooth_service_info(hass, STEM_SERVICE_INFO)
    inject_bluetooth_service_info(hass, OTHER_JIELI_SERVICE_INFO)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    schema = result["data_schema"].schema[CONF_ADDRESS]
    assert schema.container == {ADDRESS: "Stem GrowLight (EEFF)"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Stem GrowLight (EEFF)"
    assert result["data"] == {CONF_ADDRESS: ADDRESS}
    assert result["result"].unique_id == "aa:bb:cc:dd:ee:ff"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [(BleakNotFoundError("gone"), "cannot_connect"), (RuntimeError, "unknown")],
)
async def test_user_step_errors(
    hass: HomeAssistant,
    fake_light: FakeStemLight,
    side_effect: Exception,
    error: str,
) -> None:
    """Connection problems in the user step are shown and can be retried."""
    inject_bluetooth_service_info(hass, STEM_SERVICE_INFO)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(CONNECT, side_effect=side_effect):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_no_devices(hass: HomeAssistant) -> None:
    """Without a light in range the flow aborts."""
    inject_bluetooth_service_info(hass, OTHER_JIELI_SERVICE_INFO)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_skips_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A configured light is not offered again."""
    mock_config_entry.add_to_hass(hass)
    inject_bluetooth_service_info(hass, STEM_SERVICE_INFO)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_step_replaces_discovery(
    hass: HomeAssistant, fake_light: FakeStemLight
) -> None:
    """Picking a light in the user step finishes a pending discovery."""
    inject_bluetooth_service_info(hass, STEM_SERVICE_INFO)
    await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=STEM_SERVICE_INFO
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
