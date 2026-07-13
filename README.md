# QuietCool BLE — Home Assistant Integration

Native Bluetooth Low Energy integration for QuietCool attic and whole-house fans. Auto-discovers fans, enables full speed and smart-mode control, and exposes temperature, humidity, and timer sensors — all using the stock manufacturer firmware with no hardware modification required.

[![HACS Default](https://img.shields.io/badge/HACS-Default-blue.svg)](https://hacs.xyz)
[![Validate](https://github.com/rwarner/ha-quietcool-ble/actions/workflows/validate.yml/badge.svg)](https://github.com/rwarner/ha-quietcool-ble/actions/workflows/validate.yml)

## Status

**Hardware-confirmed working** on the AFG SMT PRO-2.0 (firmware IT-BLT-ATTICFAN_V3.0). All 10 entities — fan control, smart mode, temperature, humidity, timers, and threshold configuration — verified on real hardware.

## Supported Devices

| Model | CFM | Speeds | BLE Name | Status |
|---|---|---|---|---|
| AFG SMT PRO-2.0 Smart Attic Fan | 1945 | Low / High | `ATTICFAN_*` | ✅ Hardware confirmed (firmware V3.0) |
| AFG SMT ES-3.0 | 2801 | Low / Med† / High | `ATTICFAN_*` | ✅ Hardware confirmed (firmware V4.1) |
| AFG SMT ES-2.0 | Various | Low / High | `ATTICFAN_*` | 🔲 Protocol confirmed, untested |
| AFG SMT NR-A (2022 revision) | Various | Low / High | `ATTICFAN_*` | 🔲 Protocol confirmed, untested |
| Other ESP32-based QuietCool controllers | Various | Unknown | `ATTICFAN_*` | 🔲 Untested |

All supported controllers advertise over BLE with a name beginning with `ATTICFAN`.

> † **Medium speed** is offered automatically on 3-speed fans — the integration shows it only when the firmware reports a 3-speed type (`FanType: THREE`), so 2-speed fans are unaffected. Hardware-confirmed on the AFG SMT ES-3.0 (firmware V4.1) ([#4](https://github.com/rwarner/ha-quietcool-ble/issues/4)).

> **Firmware 3.9+ note:** All features — fan control, smart mode, temperature, humidity, timer, and threshold configuration — work on all supported firmware versions including 3.9+ / V4.x.

## What You Get

**Fan control**
- Turn on / off
- Low and High speed presets (plus Medium on 3-speed fans that report it)

**Smart Mode (TH — Thermostat + Humidity)**
- Automatic on/off based on attic temperature and humidity thresholds
- Full threshold configuration from HA — no app required
- Mode selector: Idle / Timer / TH

**Sensors**
- Attic temperature in °F
- Attic humidity in %
- Timer countdown (seconds remaining)
- Protect temperature (overtemp safety cutoff — diagnostic)

**General**
- Auto-discovery — HA detects the fan automatically when in Bluetooth range
- BT Proxy support — works through [ESPHome Bluetooth Proxies](https://esphome.io/components/bluetooth_proxy.html) for extended range
- Firmware and hardware version shown in device info

## Prerequisites

- Home Assistant 2023.7 or newer
- Bluetooth adapter on your HA host, or an ESPHome BT Proxy on the same network
- QuietCool fan powered on and within Bluetooth range during initial setup

## Installation

### HACS (recommended)

This integration is in the **default HACS store**, so no custom repository is needed.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rwarner&repository=ha-quietcool-ble&category=integration)

1. Open **HACS** in Home Assistant
2. Search for **QuietCool BLE**
3. Click it, then click **Download**
4. Restart Home Assistant

(Or use the **Open in HACS** button above to jump straight to the download page.)

<details>
<summary>Installing via custom repository (older HACS, or before the store updates)</summary>

If **QuietCool BLE** doesn't appear in search yet:

1. Open HACS → ⋮ (top right) → **Custom repositories**
2. Add `https://github.com/rwarner/ha-quietcool-ble` with category **Integration**
3. Search for **QuietCool BLE** and download it
4. Restart Home Assistant

</details>

### Manual

Copy `custom_components/quietcool_ble/` into your HA config's `custom_components/` directory and restart.

## Setup

When your fan is powered on and in BLE range, HA will show a notification:

> **New device discovered: QuietCool Fan**

1. Click **Configure** in the notification (or go to **Settings → Integrations → Add Integration → QuietCool BLE**)
2. Confirm the device name and MAC address shown
3. Trigger pairing mode on the fan controller (see below)
4. Click **Submit** in the HA UI
5. Done — all entities appear automatically

### Triggering Pair Mode

You have two options — use whichever is easier:

**Option A — QuietCool app (easiest, no ladder required):**
Open the QuietCool Smart Control app → tap your device → tap **Pair Mode**. The controller enters pairing mode without you needing to physically reach it. This is the recommended approach if the fan is mounted in an attic or high on a gable.

**Option B — Physical Pair button:**
Hold the Pair button on the wall control unit or controller board until the light flashes. It is typically labeled **"Pair"** or has a Bluetooth symbol. On the AFG SMT PRO-2.0 it is on the controller board inside the fan housing.

## Entities

| Entity | Type | Unit | Notes |
|---|---|---|---|
| Fan | `fan` | — | On/off, `Low` / `High` speed preset (plus `Medium` on 3-speed fans) |
| Mode | `select` | — | `Idle` / `Timer` / `TH` (smart mode) |
| Fan Speed | `sensor` | — | Physical speed: `Off` / `Low` / `Medium` / `High` |
| Temperature | `sensor` | °F | Attic temp: `Temp_Sample / 10` |
| Humidity | `sensor` | % | Attic humidity: direct integer |
| Timer Remaining | `sensor` | s | Countdown when in Timer mode |
| Protect Temperature | `sensor` | °F | Overtemp safety cutoff (diagnostic) |
| High Temp Threshold | `number` | °F | TH mode activates above this |
| Medium Temp Threshold | `number` | °F | 2-speed fans switch LOW→HIGH above this |
| Low Temp Threshold | `number` | °F | TH mode deactivates below this |
| High Humidity Threshold | `number` | % | TH mode activates above this |

## Smart Mode (TH)

TH mode lets the fan controller automatically turn the fan on and off based on attic temperature and humidity. The thresholds are stored on the device and persist across power cycles and HA restarts.

The **Fan Speed** sensor shows whether the blades are actually spinning (`Off` / `Low` / `High`). In TH mode the fan entity shows as "on" (control mode is active), but Fan Speed will read `Off` whenever the current conditions haven't triggered it yet.

Select **TH** from the Mode dropdown to activate it. Adjust the threshold number entities to match your comfort targets — changes take effect immediately without restarting the fan.

Example targets for a typical attic fan:
- High Temp: 85–95°F (fan turns on)
- Low Temp: 65–75°F (fan turns off)
- High Humidity: 80–90%

## Automations

**Turn on at Low speed when attic exceeds 90°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.attic_gable_fan_temperature
    above: 90
  action:
    service: fan.turn_on
    target:
      entity_id: fan.attic_gable_fan
    data:
      preset_mode: Low
```

**Switch to High speed above 100°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.attic_gable_fan_temperature
    above: 100
  action:
    service: fan.turn_on
    target:
      entity_id: fan.attic_gable_fan
    data:
      preset_mode: High
```

**Turn off when temperature drops below 75°F:**
```yaml
automation:
  trigger:
    platform: numeric_state
    entity_id: sensor.attic_gable_fan_temperature
    below: 75
  action:
    service: fan.turn_off
    target:
      entity_id: fan.attic_gable_fan
```

**Activate TH smart mode at sunset:**
```yaml
automation:
  trigger:
    platform: sun
    event: sunset
  action:
    service: select.select_option
    target:
      entity_id: select.attic_gable_fan_mode
    data:
      option: TH
```

## Troubleshooting

**Fan not discovered:**
- Ensure the fan is powered on
- Check that your HA host has Bluetooth or an ESPHome BT proxy configured
- Try moving a BT proxy closer to the fan

**Pairing failed:**
- If using the physical button, **hold** it (don't just tap) until the light flashes, then click Submit in HA
- If the fan is hard to reach, use the QuietCool app instead: tap your device → **Pair Mode**
- Only one device can be paired at a time. If the QuietCool app was used recently it may have claimed the pairing slot — trigger Pair Mode again and retry

**Integration shows "unavailable" after setup:**
- Power cycle the fan controller
- In HA: Settings → Integrations → QuietCool BLE → ⋮ → Reload

**QuietCool app stopped working after setup:**
The fan stores exactly one pairing credential. Pairing with the QuietCool Android app will overwrite it and break this integration until you re-pair (Settings → Integrations → QuietCool BLE → ⋮ → Re-authenticate).

**Threshold changes not sticking:**
Thresholds are written with the `SetTempHumidity` command and confirmed with a `GetParameter` read on the next poll. If the UI shows the new value but the next poll reverts it, open an issue with your debug logs.

**Enabling debug logs:**

```yaml
logger:
  default: warning
  logs:
    custom_components.quietcool_ble: debug
```

Restart HA, then reproduce the problem. Logs appear in **Settings → System → Logs**. Each BLE command and its raw JSON response are logged at `DEBUG` level.

## Security

This integration communicates directly with your fan over Bluetooth Low Energy. Be aware:

- **No link-layer encryption.** BLE communication is unencrypted — a firmware limitation that cannot be fixed in the integration.
- **Single pairing credential.** The device stores one credential. Pairing another client overwrites it and breaks this integration.

For home use the risk profile is similar to any locally-controlled smart home device.

## How It Works

QuietCool's ESP32-based BLE controllers advertise under names starting with `ATTICFAN`. All communication uses a single GATT characteristic with JSON commands:

```
Service:  000000ff-0000-1000-8000-00805f9b34fb
Char:     0000ff01-0000-1000-8000-00805f9b34fb
```

Two protocol versions exist depending on firmware:

**V1 (firmware < 3.9)** — string command names, full response keys:
```json
→ {"Api": "GetWorkState"}
← {"Mode": "TH", "Range": "HIGH", "Temp_Sample": 908, "Humidity_Sample": 23}
```

**V2 (firmware ≥ 3.9)** — numeric command codes, single-character response keys, `QQ` prefix:
```json
→ {"A": 17}
← QQ{"A": 17, "N": "ATTICFAN_XXXX", "M": "...", "S": "..."}
```

The integration auto-detects the protocol version on first connection.

### Smart mode thresholds (V1)

Thresholds are written with `SetTempHumidity`. All six fields are required per poll:

```json
→ {"Api": "SetTempHumidity", "SetTemp_H": 86, "SetTemp_M": 75, "SetTemp_L": 65,
   "SetHum_H": 90, "SetHum_L": 255, "SetHum_Range": "LOW"}
← {"Api": "SetTempHumidity", "Flag": "TRUE"}
```

## Protocol Research

| Source | Contribution |
|---|---|
| [emerose/quietcool](https://github.com/emerose/quietcool) | Original V1 reverse-engineering: command names, response keys, `Temp_Sample / 10` formula |
| [alex-spyksma/quietcool](https://github.com/alex-spyksma/quietcool/tree/issue/3-cannot-import-main) | Additional commands: `GetVersion`, `GetRemainTime`, `GetParameter`, `SetTempHumidity` |
| [u/secretoftheeast on Reddit](https://www.reddit.com/r/homeassistant/comments/1kyv0pn/quietcool_whole_house_fan_home_assistant/) | Discovered firmware 3.9+ V2 protocol: `QQ` prefix, numeric codes, single-character keys |
| [@DillonBrown](https://github.com/DillonBrown) | Full V2 API code mapping from QuietCool Smart Control Android app 2.0.28; hardware-confirmed on V4.1 firmware |
| [HA Community thread](https://community.home-assistant.io/t/quietcool-integration/913242) | Community reports and device compatibility |

## Changelog

### v0.2.11
- Fix: the V2 pair command now sends the PhoneID under the short key `P` (`{"A":14,"P":…}`) instead of `PhoneID`, matching the QuietCool V2 protocol as implemented by `snyamathi/quietcool` (confirmed on V4.1 hardware). Debug logs from a firmware 4.1 fan showed the old form being rejected (`{"A":14,"R":"Fail"}`), which blocked pairing on newer firmware
- Also tolerates the V2 controller resetting the BLE connection in response to Pair (documented behavior on some firmware) — pairing is still confirmed by a login on a fresh connection

### v0.2.10
- Fix: pairing now tries the legacy (V1) pair **and** the V2 pair sequence, confirming **each** attempt with a login on a fresh connection. Previously, if the legacy pair was accepted for the pairing session but not truly persisted, the V2 sequence was never tried — so newly-paired firmware 3.9+ / V4.x fans could still end up permanently unavailable. Existing/working fans are unaffected (they succeed on the first attempt and never reach the V2 path)

### v0.2.9
- Fix: pairing is now confirmed with a login on a **fresh** BLE connection — the same way the coordinator connects on every poll. Previously the check reused the pairing connection, so a fan that accepted the PhoneID only for that session (but didn't persist it) could report success and then go unavailable. Follows up on the 0.2.8 pairing fix

### v0.2.8
- Fix: pairing on firmware 3.9+ / V4.x fans. The controller can acknowledge the legacy pair command without actually registering Home Assistant's PhoneID, leaving every entity permanently unavailable. Pairing now **verifies with a real login**, and if the legacy pair isn't accepted it sends the **V2 pair sequence** (PairMode → Pair). Reported on AFG SMT PRO-2.0 firmware 4.1
- Hardening: the config flow reports pairing success only when login actually works — a non-registering pair now fails clearly instead of creating a dead device
- More verbose pairing logs to aid diagnosis

### v0.2.7
- Medium speed is now **hardware-confirmed** on the AFG SMT ES-3.0 (firmware V4.1): the fan reports `FanType: THREE` and accepts `MEDIUM` as a speed — matching the values shipped in 0.2.6 ([#4](https://github.com/rwarner/ha-quietcool-ble/issues/4))
- Fix: the **Fan Speed** sensor now reports `Medium` on 3-speed fans — previously a 3-speed fan running at medium would have shown `Off`. Completes the medium-speed support added in 0.2.6
- Docs: supported-devices table, feature list, and entities table now reflect Medium speed on 3-speed fans

### v0.2.6
- Feat: Medium speed preset for 3-speed fans (e.g. AFG SMT ES-3.0). Only shown when the firmware reports a 3-speed `FanType`; 2-speed fans are unaffected and still show Low / High only ([#4](https://github.com/rwarner/ha-quietcool-ble/issues/4))
- Add `fan_type` diagnostic attribute to the fan entity, exposing the firmware-reported speed-count token so 3-speed support can be confirmed in the field
- Note: the BLE value for medium (`"MEDIUM"`) and the 3-speed token (`"THREE"`) are best-guesses pending hardware confirmation on a 3-speed unit

### v0.2.5
- Feat: full firmware 3.9+ / V2 protocol support — temperature, humidity, timer, and all threshold sensors now work on V4.x devices (thanks [@DillonBrown](https://github.com/DillonBrown))
- All V2 numeric API codes mapped from QuietCool Smart Control Android app 2.0.28: `GetWorkState`, `GetVersion`, `GetParameter`, `GetRemainTime`, `SetMode`, `SetTime`, `SetTempHumidity`
- Login now correctly parses compact V2 responses (`R`/`P` keys)

### v0.2.4
- Fix: unsolicited BLE notify messages from the device no longer flood the HA error log with `QueueFull` exceptions — excess messages are silently discarded
- Fix: if the ESPHome proxy TCP connection drops during idle disconnect, the coordinator now always cleans up the client reference and schedules a retry — previously this left polling dead until HA restarted

### v0.2.3
- Add "Fan Speed" sensor (`Off` / `Low` / `High`) showing physical running state, independent of control mode — useful in TH mode where the fan cycles automatically
- Fix: transient BLE GATT errors (e.g. ESPHome proxy error 133) no longer appear as ERROR in the HA log — already handled internally with backoff retry

### v0.2.2
- Fix: polling could halt permanently if the device held the BLE connection open long enough for the coordinator's 60s idle-disconnect timer to fire first. The idle disconnect was marked "expected" so no follow-up poll was ever scheduled, silencing all entity updates until HA restarted.

### v0.2.1
- Fix: poll halt on unexpected errors; stuck timer in TH mode; raised minimum HA version

### v0.2.0
- Full entity suite: fan control, smart mode (TH), temperature, humidity, timer, threshold configuration
- Hardware-confirmed BLE protocol on AFG SMT PRO-2.0

### v0.1.0
- Initial release

## Related Projects

- [emerose/quietcool](https://github.com/emerose/quietcool) — Python BLE CLI tool; primary protocol reference
- [alex-spyksma/quietcool](https://github.com/alex-spyksma/quietcool) — fork with additional command documentation
- [awkaplan/quietcool-esphome](https://github.com/awkaplan/quietcool-esphome) — ESPHome firmware replacement (alternative approach, no stock BLE)
- [stabbylambda/homeassistant-quietcool](https://github.com/stabbylambda/homeassistant-quietcool) — earlier HA integration attempt (cloud-based)
