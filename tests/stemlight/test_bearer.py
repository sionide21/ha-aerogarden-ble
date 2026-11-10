"""Tests for asking BlueZ to connect over LE."""

from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.device import BLEDevice
from dbus_fast import MessageType
import pytest

from custom_components.aerogarden_ble.stemlight.client import _prefer_le_bearer

PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
BLUEZ_DEVICE = BLEDevice("AA:BB:CC:DD:EE:FF", "Stem GrowLight", {"path": PATH})


def _bus(reply: MagicMock | None) -> MagicMock:
    bus = MagicMock()
    bus.call = AsyncMock(return_value=reply)
    factory = MagicMock()
    factory.return_value.connect = AsyncMock(return_value=bus)
    return factory


def _reply(message_type: MessageType, error_name: str | None = None) -> MagicMock:
    reply = MagicMock(message_type=message_type, error_name=error_name, body=[])
    return reply


@pytest.mark.parametrize(
    "device",
    [
        BLEDevice("AA:BB:CC:DD:EE:FF", "Stem GrowLight", None),
        BLEDevice("AA:BB:CC:DD:EE:FF", "Stem GrowLight", {"source": "esphome"}),
    ],
)
async def test_not_bluez(device: BLEDevice) -> None:
    """Proxies and other platforms need nothing."""
    assert await _prefer_le_bearer(device) is None


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (_reply(MessageType.METHOD_RETURN), True),
        # The device object is gone while the light isn't advertising.
        (_reply(MessageType.ERROR, "org.freedesktop.DBus.Error.UnknownMethod"), None),
        (_reply(MessageType.ERROR, "org.freedesktop.DBus.Error.UnknownObject"), None),
        # BlueZ without experimental features.
        (_reply(MessageType.ERROR, "org.freedesktop.DBus.Error.InvalidArgs"), False),
    ],
)
async def test_set_preferred_bearer(reply: MagicMock, expected: bool | None) -> None:
    """The property is set on the device's BlueZ object."""
    factory = _bus(reply)
    with patch("dbus_fast.aio.MessageBus", factory):
        assert await _prefer_le_bearer(BLUEZ_DEVICE) is expected

    message = factory.return_value.connect.return_value.call.call_args.args[0]
    assert message.path == PATH
    assert message.body[1:] == ["PreferredBearer", message.body[2]]
    assert message.body[2].value == "le"


async def test_no_system_bus() -> None:
    """No D-Bus, such as in some containers, is reported as unsupported."""
    factory = MagicMock()
    factory.return_value.connect = AsyncMock(side_effect=FileNotFoundError)
    with patch("dbus_fast.aio.MessageBus", factory):
        assert await _prefer_le_bearer(BLUEZ_DEVICE) is False
