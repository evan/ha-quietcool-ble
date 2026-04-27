Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables full speed and smart-mode control, and exposes temperature, humidity, and timer sensors — all using the stock manufacturer firmware with no hardware modification required.

## Features

- **Auto-discovery** — HA detects the fan automatically when in Bluetooth range
- **Fan control** — turn on/off, select Low or High speed
- **Smart Mode (TH)** — automatic on/off based on configurable temperature and humidity thresholds
- **Mode selector** — switch between Idle, Timer, and TH smart mode
- **Threshold controls** — set High/Medium/Low temp and High humidity setpoints from HA
- **Temperature sensor** — attic temperature in °F
- **Humidity sensor** — attic humidity in %
- **Timer Remaining sensor** — countdown in seconds when in Timer mode
- **BT Proxy support** — works through ESPHome Bluetooth Proxies for extended range

## Supported Devices

All QuietCool ESP32-based controllers that advertise over BLE with a name beginning with `ATTICFAN`:

- AFG SMT PRO-2.0 Smart Attic Fan ✅ Hardware confirmed
- AFG SMT ES-2.0 / ES-3.0
- AFG SMT NR-A (2022 revision)

## Setup

When your fan is powered on and in BLE range, HA will show a discovery notification. Click Configure, then **hold** the physical **Pair button** on the fan controller until the light flashes, then click Submit.
