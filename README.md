# QuietCool BLE — Home Assistant Integration

Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables speed control, and exposes temperature and humidity sensors — all using the stock manufacturer firmware with no hardware modification required.

[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/rwarner/hass-integration-quietcool/actions/workflows/validate.yml/badge.svg)](https://github.com/rwarner/hass-integration-quietcool/actions/workflows/validate.yml)

## Supported Devices

| Model | CFM | Speeds | BLE Name | Status |
|---|---|---|---|---|
| AFG SMT PRO-2.0 Smart Attic Fan | 1945 | Low / High | `ATTICFAN_*` | ✅ Confirmed working |
| AFG SMT ES-2.0 / ES-3.0 | Various | Low / High | `ATTICFAN_*` | ✅ Confirmed by community |
| AFG SMT NR-A (2022 revision) | Various | Low / High | `ATTICFAN_*` | ✅ Confirmed by community |
| Other ESP32-based QuietCool controllers | Various | Unknown | `ATTICFAN_*` | 🔲 Untested |

All supported controllers advertise over BLE with a name beginning with `ATTICFAN`.

## What You Get

- **Fan entity** — turn on/off, select Low or High speed
- **Temperature sensor** — attic temperature in °F (from the built-in SHT3x sensor)
- **Humidity sensor** — attic humidity in %
- **Auto-discovery** — HA detects the fan automatically when in Bluetooth range
- **BT Proxy support** — works through [ESPHome Bluetooth Proxies](https://esphome.io/components/bluetooth_proxy.html) for extended range

## Prerequisites

- Home Assistant 2024.8 or newer
- Bluetooth adapter on your HA host, or an ESPHome BT Proxy on the same network
- QuietCool fan powered on and within Bluetooth range during initial setup

## Installation

### HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/rwarner/hass-integration-quietcool` with category **Integration**
3. Search for **QuietCool BLE** and install it
4. Restart Home Assistant

### Manual

Copy `custom_components/quietcool_ble/` into your HA config's `custom_components/` directory and restart.

## Setup

When your fan is powered on and in BLE range, HA will show a notification:

> **New device discovered: QuietCool Fan**

1. Click **Configure** in the notification (or go to **Settings → Integrations → Add Integration → QuietCool BLE**)
2. Confirm the device name and MAC address shown
3. **Press the physical Pair button** on your QuietCool fan controller
4. Immediately click **Submit** in the HA UI (within a few seconds of pressing the button)
5. Done — the fan, temperature, and humidity entities appear automatically

### Finding the Pair Button

The Pair button is on the wall control unit or the small controller box mounted near the fan motor. It is typically labeled **"Pair"** or has a Bluetooth symbol. On the AFG SMT PRO-2.0, it is on the controller board inside the fan housing.

## Pairing Diagram

```
QuietCool Fan Controller
┌─────────────────────┐
│  [PWR] [LOW] [HIGH] │
│                     │
│      [ PAIR ]  ←────┼── Press this, then submit in HA
└─────────────────────┘
```

## Entities

| Entity | Type | Unit | Notes |
|---|---|---|---|
| Fan | `fan` | — | `turn_on`, `turn_off`, `Low`/`High` preset |
| Temperature | `sensor` | °F | `Temp_Sample / 10`; e.g. `1071` → `107.1°F` |
| Humidity | `sensor` | % | `Humidity_Sample / 10` |

## Automations

**Turn on at Low speed when attic is above 90°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.atticfan_temperature
    above: 90
  action:
    service: fan.turn_on
    target:
      entity_id: fan.atticfan
    data:
      preset_mode: Low
```

**Turn off when temperature drops below 75°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.atticfan_temperature
    below: 75
  action:
    service: fan.turn_off
    target:
      entity_id: fan.atticfan
```

## Troubleshooting

**Fan not discovered:**
- Ensure the fan is powered on
- Check that your HA host has Bluetooth or an ESPHome BT proxy configured
- Try moving a BT proxy closer to the fan

**Pairing failed:**
- Press the Pair button on the fan first, then immediately submit in HA (the pairing window is short)
- Only one device can be paired at a time. If the QuietCool Android app was used recently, it may have claimed the pairing slot. Try again.

**Integration shows "unavailable" after setup:**
- Power cycle the fan controller
- In HA: Settings → Integrations → QuietCool BLE → ⋮ → Reload

**QuietCool app stopped working after setup:**
The fan stores exactly one pairing credential. Pairing with the QuietCool Android app will break this integration until you re-pair (Settings → Integrations → QuietCool BLE → ⋮ → Re-authenticate).

## Security

This integration communicates directly with your fan over Bluetooth Low Energy. Be aware of the following:

- **No link-layer encryption.** BLE communication is unencrypted. Anyone within ~10 meters with a BLE sniffer can passively observe commands. This is a firmware limitation that cannot be fixed in the integration.
- **Replay possible.** Captured BLE commands can be replayed by someone within physical BLE range. Practical risk is low but non-zero in shared buildings (apartments, offices).
- **Single pairing credential.** The device stores one credential. Pairing another client (e.g., the QuietCool mobile app) overwrites the stored credential and breaks this integration.

For home use on a private network, the risk profile is similar to any locally-controlled smart home device.

## How It Works

QuietCool's ESP32-based BLE controllers advertise under names starting with `ATTICFAN`. All communication uses a single GATT characteristic with JSON commands over BLE:

```
Service:   000000ff-0000-1000-8000-00805f9b34fb
Char:      0000ff01-0000-1000-8000-00805f9b34fb
Protocol:  {"Api": "GetWorkState"} → {"Mode": "Timer", "Range": "HIGH", "Temp_Sample": 1071, ...}
```

The BLE protocol was reverse-engineered by [emerose/quietcool](https://github.com/emerose/quietcool). This integration builds on that protocol work with a proper HA config flow, coordinator, and entity architecture.

## Related Projects

- [emerose/quietcool](https://github.com/emerose/quietcool) — Python BLE CLI tool (protocol reference)
- [awkaplan/quietcool-esphome](https://github.com/awkaplan/quietcool-esphome) — ESPHome firmware replacement (alternative approach)
- [HA Community: QuietCool Integration](https://community.home-assistant.io/t/quietcool-integration/913242)
