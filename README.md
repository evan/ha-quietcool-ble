# QuietCool BLE — Home Assistant Integration

Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables speed control, and exposes temperature and humidity sensors — all using the stock manufacturer firmware with no hardware modification required.

[![HACS Custom Repository](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/rwarner/ha-quietcool-ble/actions/workflows/validate.yml/badge.svg)](https://github.com/rwarner/ha-quietcool-ble/actions/workflows/validate.yml)

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
2. Add `https://github.com/rwarner/ha-quietcool-ble` with category **Integration**
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

**Enabling debug logs:**

Add this to your `configuration.yaml` to capture full BLE protocol traffic:

```yaml
logger:
  default: warning
  logs:
    custom_components.quietcool_ble: debug
```

Restart HA, then reproduce the problem. Logs appear in **Settings → System → Logs**. Each BLE command and its response are logged at `DEBUG` level, e.g.:

```
QuietCool BLE GetFanInfo → {'Name': 'ATTICFAN_XXXX', 'Model': 'AFG SMT PRO-2.0', ...}
```

If filing a bug report, please include the debug log output from the connection attempt.

## Security

This integration communicates directly with your fan over Bluetooth Low Energy. Be aware of the following:

- **No link-layer encryption.** BLE communication is unencrypted. Anyone within ~10 meters with a BLE sniffer can passively observe commands. This is a firmware limitation that cannot be fixed in the integration.
- **Replay possible.** Captured BLE commands can be replayed by someone within physical BLE range. Practical risk is low but non-zero in shared buildings (apartments, offices).
- **Single pairing credential.** The device stores one credential. Pairing another client (e.g., the QuietCool mobile app) overwrites the stored credential and breaks this integration.

For home use on a private network, the risk profile is similar to any locally-controlled smart home device.

## How It Works

QuietCool's ESP32-based BLE controllers advertise under names starting with `ATTICFAN`. All communication uses a single GATT characteristic with JSON commands over BLE:

```
Service:  000000ff-0000-1000-8000-00805f9b34fb
Char:     0000ff01-0000-1000-8000-00805f9b34fb
```

Two protocol versions exist depending on firmware:

**V1 (firmware < 3.9)** — string command names, full response keys:
```
→ {"Api": "GetWorkState"}
← {"Mode": "Timer", "Range": "HIGH", "Temp_Sample": 1071, "Humidity_Sample": 650, ...}
```

**V2 (firmware ≥ 3.9)** — numeric command codes, single-character response keys, `QQ` prefix:
```
→ {"A": 17}
← QQ{"A": 17, "N": "ATTICFAN_XXXX", "M": "AFG SMT PRO-2.0", "S": "..."}
```

The integration auto-detects the protocol version on first connection. Temperature is `Temp_Sample / 10 = °F` (e.g. `1071` → `107.1 °F`).

### Known Protocol Gaps

The V2 numeric API code for `GetWorkState` has not yet been publicly confirmed. Until it is, **temperature and humidity sensors will be unavailable on firmware ≥ 3.9 devices**. Fan control (on/off, speed) still works because `SetMode` and `SetTime` appear to be accepted in V1 format on V2 firmware. See [Protocol Research](#protocol-research) for where to track this.

## Protocol Research

The BLE protocol was reverse-engineered by the community. Key sources used in building this integration:

| Source | Contribution |
|---|---|
| [emerose/quietcool](https://github.com/emerose/quietcool) | Original V1 protocol reverse-engineering: command names, response keys, `Temp_Sample / 10` formula, `SensorState` field |
| [alex-spyksma/quietcool](https://github.com/alex-spyksma/quietcool/tree/issue/3-cannot-import-main) | Fork confirming `SensorState` in `GetWorkState`, additional commands (`GetVersion`, `GetRemainTime`, `GetParameter`) |
| [u/secretoftheeast on Reddit](https://www.reddit.com/r/homeassistant/comments/1kyv0pn/quietcool_whole_house_fan_home_assistant/) | Discovered firmware 3.9+ V2 protocol: `QQ` prefix, `{"A": 17}` numeric codes, single-character response keys |
| [HA Community thread](https://community.home-assistant.io/t/quietcool-integration/913242) | Community reports and device compatibility |

### Where to Watch for Updates

If you have firmware ≥ 3.9 and want to help unlock temperature/humidity sensors:

- **[emerose/quietcool issues](https://github.com/emerose/quietcool/issues)** — the most active hub for protocol research; watch for PRs adding V2 GetWorkState support
- **[Reddit thread (u/secretoftheeast)](https://www.reddit.com/r/homeassistant/comments/1kyv0pn/quietcool_whole_house_fan_home_assistant/)** — original V2 discovery post; u/secretoftheeast indicated a branch with further findings
- **[HA Community thread](https://community.home-assistant.io/t/quietcool-integration/913242)** — user reports and firmware version notes
- **[This repo's issues](https://github.com/rwarner/ha-quietcool-ble/issues)** — open an issue if you can BLE-sniff your device's `GetWorkState` response on firmware 3.9+

The missing piece is the numeric API code for `GetWorkState` (and `SetMode`/`SetTime` if V1 format stops working on newer firmware). If you have a BLE sniffer (nRF Sniffer, Wireshark + HCI log, or the Android QuietCool app with BLE debugging) and firmware ≥ 3.9, capturing a `GetWorkState` exchange would unblock this.

## Related Projects

- [emerose/quietcool](https://github.com/emerose/quietcool) — Python BLE CLI tool; primary protocol reference
- [alex-spyksma/quietcool](https://github.com/alex-spyksma/quietcool) — fork with additional command documentation
- [awkaplan/quietcool-esphome](https://github.com/awkaplan/quietcool-esphome) — ESPHome firmware replacement (alternative approach that bypasses the stock BLE protocol entirely)
- [stabbylambda/homeassistant-quietcool](https://github.com/stabbylambda/homeassistant-quietcool) — earlier HA integration attempt (cloud-based, not native BLE)
