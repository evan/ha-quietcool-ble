Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables Low/High speed control, and exposes temperature and humidity sensors — all using the stock manufacturer firmware with no hardware modification required.

## Features

- **Auto-discovery** — HA detects the fan automatically when in Bluetooth range
- **Fan control** — turn on/off, select Low or High speed
- **Temperature sensor** — attic temperature in °F
- **Humidity sensor** — attic humidity in %
- **BT Proxy support** — works through ESPHome Bluetooth Proxies for extended range

## Supported Devices

All QuietCool ESP32-based controllers that advertise over BLE with a name beginning with `ATTICFAN`:

- AFG SMT PRO-2.0 Smart Attic Fan
- AFG SMT ES-2.0 / ES-3.0
- AFG SMT NR-A (2022 revision)

## Setup

When your fan is powered on and in BLE range, HA will show a discovery notification. Click Configure, then press the physical **Pair button** on the fan controller when prompted.

## Note on Firmware 3.9+

Devices running firmware 3.9 or newer use an updated BLE protocol. Fan control works on all firmware versions. Temperature and humidity sensors require a V2 protocol mapping that is still being reverse-engineered by the community — see the [GitHub repo](https://github.com/rwarner/ha-quietcool-ble) for tracking.
