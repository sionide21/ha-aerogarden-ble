# AeroGarden Bluetooth

Control an [AeroGarden Stem Grow Light](https://aerogarden.com) from Home
Assistant over Bluetooth, with no cloud and no AeroGarden account.

The integration talks to the light directly over Bluetooth. It connects only
briefly, so the AeroGarden app keeps working alongside it.

## Supported devices

- AeroGarden Stem Grow Light (advertises as `Stem GrowLight`)

Wi-Fi AeroGarden gardens are not supported.

## What you get

| Entity | Type | Notes |
|---|---|---|
| The light | Light | On/off and brightness. Changes last until the schedule's next on or off time, as in the app. |
| Light mode | Select | White, Full spectrum, Warm or Cool. |
| Light on time / Light off time | Time | The daily schedule. |
| Default brightness | Number | The brightness the schedule turns the light on at. |
| Sunrise and sunset | Switch | Fade in and out at the on and off times. |
| Sunrise duration / Sunset duration | Number | Fade lengths, in minutes. |
| Days planted | Sensor | As set up in the app. |
| Plant type | Sensor (diagnostic) | As chosen in the app. |

The light keeps its schedule on its own clock, which resets when it loses
power. Home Assistant sets the clock to its own time zone whenever the clock is
unset or off by more than a minute.

## Installation

1. In Home Assistant, open **Settings > Marketplace**.
2. Search for **AeroGarden Bluetooth** and install it. If it isn't listed yet,
   add `https://github.com/sionide21/ha-aerogarden-ble` as a custom repository.
3. Go to **Settings > Devices & services > Add integration** and pick
   **AeroGarden Bluetooth**. Lights in range are listed straight away, with no
   restart needed. After the next restart, new lights are also discovered
   automatically.

Home Assistant 2026.11 or later and a Bluetooth adapter or proxy are required.

## How it works

The light accepts one Bluetooth connection at a time, and stops advertising
while something is connected. To leave room for the AeroGarden app and for
proxy connection slots, the integration connects, reads or changes what it
needs, and disconnects about ten seconds later. It reads the light every five
minutes, and also just after each scheduled on or off time, so changes made
with the app or the schedule show up shortly afterwards.

## Known limitations

- **The AeroGarden app and Home Assistant take turns.** The light accepts one
  connection at a time. While the app is connected, Home Assistant can't reach
  the light: commands fail straight away with a message saying so, and the
  state isn't updated. The app also stays connected some of the time while
  it's in the background, so close it fully when you're done with it. In the
  same way, the app can't connect for the few seconds Home Assistant is
  talking to the light. (Tested with the Android app.)
- **Local Bluetooth adapters on Linux** need two settings that ESPHome
  Bluetooth proxies don't:
  - **Active scanning.** The light advertises with flags Home Assistant's
    passive scanning on BlueZ filters out, so set the adapter's scanning mode
    to *Active* (**Settings > Devices & services > Bluetooth > Configure**).
  - **BlueZ 5.80 or later with experimental features** (`bluetoothd
    --experimental`). The light's advertisement claims classic Bluetooth
    support, so BlueZ otherwise tries classic Bluetooth and times out. The
    integration asks BlueZ to use Bluetooth LE, and logs a warning if it
    can't.

  [ESPHome Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html)
  are not affected and are the recommended setup.

## Troubleshooting

- Make sure the light is plugged in and that the AeroGarden app is closed,
  not just in the background.
- Download diagnostics from the device page. They include the last raw status
  packet, which helps with protocol issues.
- To see every packet, enable debug logging:

  ```yaml
  logger:
    logs:
      custom_components.aerogarden_ble: debug
  ```

## Removal

1. Go to **Settings > Devices & services > AeroGarden Bluetooth** and delete
   each light.
2. Uninstall **AeroGarden Bluetooth** from the Marketplace.

## Development

The Bluetooth encoder and decoder are in
[`stemlight/protocol.py`](custom_components/aerogarden_ble/stemlight/protocol.py),
and the client in `stemlight/` doesn't depend on Home Assistant.

```shell
python -m venv .venv
.venv/bin/pip install --group dev
.venv/bin/pytest
```

`scripts/probe.py` talks to a light directly with bleak, which helps with
debugging:

```shell
.venv/bin/python scripts/probe.py scan
.venv/bin/python scripts/probe.py status AA:BB:CC:DD:EE:FF --listen 60
```

## Notice

This is an independent third-party integration and is not affiliated, authorized,
endorsed by, or in any way officially connected with AeroGarden or any of its
subsidiaries or affiliates.

## AI disclosure

This integration was written using an AI coding assistant.
