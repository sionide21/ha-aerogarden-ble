#!/usr/bin/env python3
"""Talk to an AeroGarden Stem Grow Light directly, without Home Assistant.

A debugging tool that prints every packet. Close the AeroGarden app first: the
light accepts only one connection at a time.

    probe.py scan [--all] [--timeout S]
    probe.py status ADDRESS [--listen S]
    probe.py set ADDRESS brightness 50
    probe.py set ADDRESS schedule 07:00 23:00
    probe.py raw ADDRESS "5a 19 00 00 ..."
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields
import datetime as dt
from pathlib import Path
import sys

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak_retry_connector import establish_connection

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent / "custom_components" / "aerogarden_ble"
    ),
)
from stemlight import protocol
from stemlight.protocol import LightMode, LightStatus, PlantType

LD2410_MANUFACTURER_ID = 256


def _stamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _hms(seconds: int) -> str:
    return str(dt.timedelta(seconds=seconds))


def _describe(status: LightStatus) -> str:
    lines = []
    for field in fields(status):
        value = getattr(status, field.name)
        if field.name == "clock_seconds":
            value = f"{value} ({_hms(value)}, local now {dt.datetime.now():%H:%M:%S})"
        elif field.name == "plant_type":
            value = f"{value} ({_enum_name(PlantType, value)})"
        elif field.name == "light_mode":
            value = f"{value} ({_enum_name(LightMode, value)})"
        elif field.name == "flags":
            value = (
                f"0x{value:02x} (light_on={status.light_on} "
                f"brightness_override={status.brightness_override} "
                f"sunrise_sunset={status.sunrise_sunset_enabled})"
            )
        elif field.name == "extra":
            value = value.hex(" ")
        elif isinstance(value, tuple):
            value = f"{value[0]:02d}:{value[1]:02d}"
        lines.append(f"    {field.name:24} {value}")
    return "\n".join(lines)


def _enum_name(enum: type[LightMode] | type[PlantType], value: int) -> str:
    try:
        return enum(value).name
    except ValueError:
        return "?"


def _on_notify(_char: BleakGATTCharacteristic, data: bytearray) -> None:
    print(f"[{_stamp()}] notify  len={len(data)} raw={data.hex(' ')}")
    plain = protocol.decrypt_notification(data)
    if plain is None:
        print("    (not a framed packet)")
        return
    print(f"    decrypted len={len(plain)} {plain.hex(' ')}")
    notification = protocol.parse_notification(data)
    if notification is None:
        print("    (not a valid packet)")
        return
    try:
        type_name = protocol.PacketType(notification.packet_type).name
    except ValueError:
        type_name = "unknown"
    print(f"    type=0x{notification.packet_type:02x} ({type_name})")
    if notification.status:
        print(_describe(notification.status))


async def scan(args: argparse.Namespace) -> None:
    """List advertising devices, by default only those with the Stem service."""
    print(f"Scanning for {args.timeout} s...")
    found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
    for device, adv in sorted(found.values(), key=lambda item: -item[1].rssi):
        uuids = [uuid.lower() for uuid in adv.service_uuids]
        if not args.all and protocol.ADVERTISED_SERVICE_UUID not in uuids:
            continue
        print(f"{device.address}  rssi={adv.rssi}  name={adv.local_name!r}")
        print(f"    service_uuids: {uuids}")
        for company, data in adv.manufacturer_data.items():
            note = " (LD2410 radar?)" if company == LD2410_MANUFACTURER_ID else ""
            print(f"    manufacturer 0x{company:04x}: {data.hex(' ')}{note}")
        for uuid, data in adv.service_data.items():
            print(f"    service_data {uuid}: {data.hex(' ')}")
        if adv.tx_power is not None:
            print(f"    tx_power: {adv.tx_power}")


async def _prefer_le(device: BLEDevice) -> None:
    """Stop BlueZ from paging the light over BR/EDR.

    The light's advertising flags claim BR/EDR support, so BlueZ picks the
    classic bearer and times out. PreferredBearer needs bluetoothd -E.
    """
    details = device.details
    if not isinstance(details, dict) or "path" not in details:
        return
    from dbus_fast import BusType, Message, MessageType, Variant  # noqa: PLC0415
    from dbus_fast.aio import MessageBus  # noqa: PLC0415

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=details["path"],
                interface="org.freedesktop.DBus.Properties",
                member="Set",
                signature="ssv",
                body=["org.bluez.Device1", "PreferredBearer", Variant("s", "le")],
            )
        )
    finally:
        bus.disconnect()
    if reply is not None and reply.message_type == MessageType.ERROR:
        print(f"    PreferredBearer=le failed: {reply.error_name} {reply.body}")
    else:
        print("    PreferredBearer=le set")


async def _connect(address: str) -> BleakClient:
    print(f"[{_stamp()}] looking for {address}")
    latest: dict[str, BLEDevice] = {}

    def _seen(device: BLEDevice, _adv: AdvertisementData) -> None:
        if device.address.upper() == address.upper():
            latest["device"] = device

    # Keep scanning while connecting, like Home Assistant does; BlueZ forgets
    # the device as soon as scanning stops, which breaks every retry.
    async with BleakScanner(_seen):
        for _ in range(150):
            if "device" in latest:
                break
            await asyncio.sleep(0.1)
        else:
            sys.exit(f"{address} not found; is the phone app connected to it?")
        device = latest["device"]
        await _prefer_le(device)
        client = await establish_connection(
            BleakClient,
            device,
            device.name or address,
            max_attempts=6,
            ble_device_callback=lambda: latest["device"],
        )
    print(f"[{_stamp()}] connected")
    try:
        if acquire_mtu := getattr(client._backend, "_acquire_mtu", None):
            await acquire_mtu()
        print(f"    mtu_size={client.mtu_size}")
    except Exception as err:  # noqa: BLE001
        print(f"    mtu_size unavailable: {err}")
    for service in client.services:
        print(f"    service {service.uuid}")
        for char in service.characteristics:
            print(f"        char {char.uuid} {','.join(char.properties)}")
    await client.start_notify(protocol.NOTIFY_CHAR_UUID, _on_notify)
    print(f"[{_stamp()}] subscribed to notifications")
    return client


async def _write(client: BleakClient, frame: bytes, label: str) -> None:
    print(f"[{_stamp()}] write   {label}: {frame.hex(' ')}")
    # ae3b only supports write-without-response.
    await client.write_gatt_char(protocol.WRITE_CHAR_UUID, frame, response=False)


async def status(args: argparse.Namespace) -> None:
    """Query the status once and keep listening for pushes."""
    client = await _connect(args.address)
    try:
        await _write(client, protocol.status_query(), "status query")
        print(f"[{_stamp()}] listening for {args.listen} s")
        await asyncio.sleep(args.listen)
    finally:
        await client.disconnect()


def _parse_hhmm(value: str) -> dt.time:
    return dt.datetime.strptime(value, "%H:%M").time()


def _build_command(args: argparse.Namespace) -> tuple[str, bytes]:
    return _build(args.command, args.values)


def _build(command: str, values: list[str]) -> tuple[str, bytes]:
    match command:
        case "brightness":
            return f"brightness {values[0]}", protocol.set_brightness(int(values[0]))
        case "default":
            return (
                f"default brightness {values[0]}",
                protocol.set_default_brightness(int(values[0])),
            )
        case "mode":
            mode = LightMode[values[0].upper()]
            return f"mode {mode.name}", protocol.set_light_mode(mode)
        case "schedule":
            on, off = _parse_hhmm(values[0]), _parse_hhmm(values[1])
            return f"schedule {on}-{off}", protocol.set_schedule(on, off)
        case "durations":
            sunrise, sunset = int(values[0]), int(values[1])
            return (
                f"sunrise/sunset durations {sunrise}/{sunset} min",
                protocol.set_sunrise_sunset_durations(sunrise, sunset),
            )
        case "toggle-sunrise":
            return "toggle sunrise/sunset", protocol.toggle_sunrise_sunset()
        case "clock":
            now = (
                dt.datetime.strptime(values[0], "%H:%M:%S").time()
                if values
                else dt.datetime.now().time()
            )
            seconds = protocol.seconds_since_midnight(now)
            return f"clock {_hms(seconds)}", protocol.set_clock(seconds)
    raise SystemExit(f"unknown command {command}")


async def sequence(args: argparse.Namespace) -> None:
    """Send several commands over one connection, querying after each."""
    steps = []
    for step in args.steps:
        command, *values = step.split()
        steps.append(_build(command, values))
    client = await _connect(args.address)
    try:
        await _write(client, protocol.status_query(), "status query (before)")
        await asyncio.sleep(1.5)
        for label, frame in steps:
            await _write(client, frame, label)
            await asyncio.sleep(1.5)
            await _write(
                client, protocol.status_query(), f"status query (after {label})"
            )
            await asyncio.sleep(1.5)
    finally:
        await client.disconnect()


async def set_value(args: argparse.Namespace) -> None:
    """Send one command, bracketed by status queries."""
    label, frame = _build_command(args)
    client = await _connect(args.address)
    try:
        await _write(client, protocol.status_query(), "status query (before)")
        await asyncio.sleep(2)
        await _write(client, frame, label)
        await asyncio.sleep(2)
        await _write(client, protocol.status_query(), "status query (after)")
        await asyncio.sleep(args.listen)
    finally:
        await client.disconnect()


async def raw(args: argparse.Namespace) -> None:
    """Write arbitrary bytes."""
    frame = bytes.fromhex(args.hex)
    client = await _connect(args.address)
    try:
        await _write(client, frame, "raw")
        await asyncio.sleep(args.listen)
    finally:
        await client.disconnect()


def main() -> None:
    """Parse arguments and run a command."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="action", required=True)

    p_scan = sub.add_parser("scan", help="list advertising Stem lights")
    p_scan.add_argument("--all", action="store_true", help="show every device")
    p_scan.add_argument("--timeout", type=float, default=10)
    p_scan.set_defaults(func=scan)

    p_status = sub.add_parser("status", help="query status and listen")
    p_status.add_argument("address")
    p_status.add_argument("--listen", type=float, default=5)
    p_status.set_defaults(func=status)

    p_set = sub.add_parser("set", help="send one command")
    p_set.add_argument("address")
    p_set.add_argument(
        "command",
        choices=[
            "brightness",
            "default",
            "mode",
            "schedule",
            "durations",
            "toggle-sunrise",
            "clock",
        ],
    )
    p_set.add_argument("values", nargs="*")
    p_set.add_argument("--listen", type=float, default=3)
    p_set.set_defaults(func=set_value)

    p_seq = sub.add_parser("seq", help='run steps like "brightness 50" in order')
    p_seq.add_argument("address")
    p_seq.add_argument("steps", nargs="+")
    p_seq.set_defaults(func=sequence)

    p_raw = sub.add_parser("raw", help="write raw hex bytes")
    p_raw.add_argument("address")
    p_raw.add_argument("hex")
    p_raw.add_argument("--listen", type=float, default=5)
    p_raw.set_defaults(func=raw)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
