---
title: QuietCool BLE Native Home Assistant Integration
type: feat
status: active
date: 2026-04-26
deepened: 2026-04-26
---

# QuietCool BLE Native Home Assistant Integration

## Enhancement Summary

**Deepened on:** 2026-04-26
**Agents used:** kieran-python-reviewer, security-sentinel, architecture-strategist, performance-oracle, framework-docs-researcher, best-practices-researcher (BLE patterns + temp calibration), julik-frontend-races-reviewer, code-simplicity-reviewer

### Key Improvements Discovered

1. **Temperature scaling is confirmed** — `Temp_Sample / 10 = °F` (e.g., `1071` → 107.1°F, a normal hot attic). This was marked "unknown" in the original plan; emerose's source code confirms the formula.
2. **Critical upstream code must NOT be ported** — `emerose/quietcool`'s disconnect callback does `for task in asyncio.all_tasks(): task.cancel()`, which would crash all of Home Assistant. This must be rewritten.
3. **Fan entities must NEVER open their own BLE connections** — the plan's `fan.py` pattern of calling `establish_connection()` directly creates an unmitigated race with the coordinator poll. All BLE operations must route through a single coordinator-level `asyncio.Lock`.
4. **led_ble dual-lock pattern is the right architecture** — HA's official reference for active command/response BLE integrations uses dual locks (`_connect_lock` + `_operation_lock`) with a 120-second idle-disconnect timer, not connect-per-operation.
5. **`asyncio.Semaphore` is the wrong primitive** — replace with `asyncio.Queue` for response correlation + `asyncio.Lock` for serialization. `start_notify` must precede write.
6. **Significant simplifications identified** — `models.py` is unnecessary (merge into `api.py`), `api.py` class is premature abstraction (use module functions), auto-generate PhoneID always, fetch `GetFanInfo` once at setup only.
7. **Missing required HA features** — `FanEntityFeature.TURN_ON | TURN_OFF` mandatory since HA 2024.8; without them `hassfest` validation fails.

### Critical Issues That Change Implementation

| Issue | Severity | Impact |
|---|---|---|
| Dual `establish_connection` race (poll + entity command) | Critical | Fan commands corrupt or timeout during polls |
| Port of `all_tasks().cancel()` from upstream | Critical | Would crash entire HA instance on BLE disconnect |
| `asyncio.Semaphore` wrong for notify correlation | Critical | Response buffer corruption on rapid responses |
| `_needs_poll` wrong parameter name + missing guards | Critical | Polling fails during startup/shutdown/proxies |
| `is_on` crashes when `fan_state is None` | Critical | AttributeError on first poll before any data |
| No timeout on BLE response awaiting | High | Indefinite deadlock on unresponsive device |

---

## Overview

Build a native Home Assistant custom integration (`custom_components/quietcool_ble`) that auto-discovers QuietCool attic fans over Bluetooth Low Energy, enables fan speed control, and exposes temperature and humidity sensors — all without modifying device firmware or requiring any cloud connection.

No existing integration achieves this. The closest prior art is `emerose/quietcool`, a standalone Python BLE library that fully reverse-engineers the protocol but has no HA wiring. This plan uses that library as its **protocol specification** but rewrites the client implementation to be HA-safe (the upstream client contains patterns that would crash HA — see Critical Porting Note below).

---

## Problem Statement

QuietCool BLE-enabled fans (whole house and attic gable models with `ATTICFAN*` advertisement names) ship with an ESP32 controller running proprietary firmware that speaks a JSON-over-BLE-GATT protocol. Three workarounds exist today, each with serious drawbacks:

| Approach | Status | Drawback |
|---|---|---|
| ESPHome firmware flash | Working | Voids warranty; requires FTDI programmer; irreversible |
| Hardware relay interlock (Shelly Pro 3) | Working | No temperature sensor data; complex wiring |
| WiFi hub integration (stabbylambda) | Archived 2022 | Targets discontinued WiFi hub product; does not work with BLE controllers |
| Native BLE integration | ❌ Does not exist | **This is what we're building** |

---

## Proposed Solution

A HACS-compatible custom integration (`quietcool_ble`) with:

1. **BLE auto-discovery** — HA sees `ATTICFAN*` advertisement → triggers config flow automatically
2. **Config flow** — user confirms device, PhoneID is auto-generated, physical pairing button flow
3. **Fan entity** — on/off + preset speed modes (Low / High)
4. **Sensor entities** — temperature (°F = `Temp_Sample / 10`) and humidity from `GetWorkState` polling
5. **Active BLE coordinator** — led_ble dual-lock pattern with idle-timeout disconnect

### Critical Porting Note

`emerose/quietcool`'s `device.py` disconnect callback contains:
```python
for task in asyncio.all_tasks(): task.cancel()
```
**Do not port this line.** It cancels every asyncio task in the HA event loop — other integrations, WebSocket handlers, everything. The HA integration must replace this with targeted cancellation of only the coordinator's own `_poll_task`.

---

## Technical Approach

### BLE Protocol Specification (Fully Confirmed)

Source: `emerose/quietcool` v0.1.2 + `awkaplan/quietcool-esphome` YAML (independently confirmed).

**BLE identifiers:**
- Service UUID: `000000ff-0000-1000-8000-00805f9b34fb`
- Characteristic UUID: `0000ff01-0000-1000-8000-00805f9b34fb`
- Advertisement name prefix: `ATTICFAN` (filter: `name.startswith("ATTICFAN")`)

**Transport:**
- Single GATT characteristic, bidirectional: write to send, notify to receive
- JSON-over-BLE, UTF-8 encoded
- Sender chunks payload by `max_write_without_response_size`; receiver accumulates until `json.loads()` succeeds
- `start_notify` must be registered **before** the write command is sent (not after)
- No binary framing; no length prefix

**Authentication (application-layer, no BLE bonding needed):**
```json
{"Api": "Login", "PhoneID": "a1b2c1d2a2b1c2d1"}
// Response if registered: {"Result": "Success"}
// Response if not yet paired: {"PairState": "No"}

// First-time pairing (user presses physical Pair button first)
{"Api": "Pair", "PhoneID": "a1b2c1d2a2b1c2d1"}
```

`PhoneID` is a user-chosen 16-character hex string (always auto-generated by this integration). The device stores exactly one `PhoneID`.

**Command reference:**
```
GetFanInfo    → {Name, Model, SerialNum}
GetParameter  → {Mode, FanType, temp/humidity thresholds}
GetWorkState  → {Mode, Range, SensorState, Temp_Sample, Humidity_Sample}
GetVersion    → {Version, ProtectTemp, HW_Version}
SetMode       → {Mode: "Idle"|"Timer"|"TH"}
SetTime       → {SetHour, SetMinute, SetTime_Range: "HIGH"|"LOW"}
```

**Turn on:** `SetTime` (duration + speed) then `SetMode "Timer"`
**Turn off:** `SetMode "Idle"`
**Speed change:** `SetTime` with new `SetTime_Range`, then re-apply `SetMode "Timer"`

**Temperature scaling (CONFIRMED):**
- Formula: `temperature_fahrenheit = Temp_Sample / 10`
- Example: `Temp_Sample: 1071` → `107.1°F` (normal hot attic in summer)
- Example: `Temp_Sample: 720` → `72.0°F` (room temperature)
- Source: `emerose/quietcool/api.py` `WorkState.from_response()` explicitly does `temperature = response["Temp_Sample"] / 10`
- The firmware reports in tenths of °F (US-market product); SHT3x raw ADC values are pre-converted by the ESP32 firmware before BLE transmission
- Convert to Celsius: `(fahrenheit - 32) * 5 / 9`

**Remaining unknowns (require hardware testing):**
- Whether `"MEDIUM"` is a valid `SetTime_Range` value on 3-speed models
- Whether `"TH"` thermostat mode is accessible over BLE (present in Android app; not in `emerose` enum)

### File Structure (Simplified)

Research revealed `models.py` is unnecessary and the `api.py` class is premature abstraction. Simplified structure:

```
custom_components/
└── quietcool_ble/
    ├── __init__.py          # async_setup_entry, async_unload_entry
    ├── manifest.json        # domain, bluetooth matchers, dependencies
    ├── config_flow.py       # async_step_bluetooth, async_step_bluetooth_confirm, pairing
    ├── coordinator.py       # ActiveBluetoothDataUpdateCoordinator subclass
    ├── const.py             # DOMAIN, UUIDs, PLATFORMS, POLL_INTERVAL
    ├── api.py               # Module-level functions + FanState/FanInfo dataclasses
    ├── fan.py               # QuietCoolFanEntity
    ├── sensor.py            # TemperatureSensor, HumiditySensor entities
    ├── strings.json         # Config flow UI strings
    └── translations/
        └── en.json
```

**Repo root:**
```
hacs.json                    # HACS metadata (only "name" required)
README.md
.github/workflows/validate.yml  # Required for HACS validation
```

**Eliminated files vs. original plan:**
- `models.py` — merged into `api.py` (dataclasses live next to the code that creates them)

### manifest.json

```json
{
  "domain": "quietcool_ble",
  "name": "QuietCool BLE",
  "config_flow": true,
  "documentation": "https://github.com/user/hass-integration-quietcool",
  "dependencies": ["bluetooth_adapters"],
  "requirements": ["bleak-retry-connector>=3.0.0", "bluetooth-data-tools>=1.0.0"],
  "bluetooth": [
    {"local_name": "ATTICFAN*", "connectable": true}
  ],
  "codeowners": ["@user"],
  "iot_class": "local_polling",
  "version": "0.1.0"
}
```

**Research Insights:**
- Only ONE bluetooth matcher needed. Both `local_name` AND `service_uuid` matchers together can trigger two config flow invocations for the same device. Start with `local_name: ATTICFAN*` only.
- `"ATTICFAN*"` is a valid wildcard — HA's `bluetooth/match.py` requires the first 3 characters be literal (they are: `ATT`), wildcards are valid from character 4+.
- `iot_class: "local_polling"` is correct for an active-connection, polling device.

### api.py — Module-Level Functions + Dataclasses

Convert from class to module-level functions. `FanState` and `FanInfo` dataclasses live here (not in a separate `models.py`).

```python
# api.py
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from bleak import BleakClient

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID: Final = "000000ff-0000-1000-8000-00805f9b34fb"
CHAR_UUID: Final = "0000ff01-0000-1000-8000-00805f9b34fb"
COMMAND_TIMEOUT: Final = 10.0   # seconds per BLE round-trip
MAX_RECV_BUFFER: Final = 1024   # bytes; real responses are <200 bytes

class FanMode(StrEnum):
    IDLE = "Idle"
    TIMER = "Timer"

class FanSpeed(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"

@dataclass(frozen=True, slots=True)
class FanState:
    mode: str                  # FanMode value
    range: str | None          # FanSpeed value or None when Idle
    temp_fahrenheit: float | None  # Temp_Sample / 10; None if sensor error
    humidity_percent: float | None

@dataclass(frozen=True, slots=True)
class FanInfo:
    name: str
    model: str
    serial: str

async def login(client: BleakClient, phone_id: str) -> bool:
    """Send Login; return True if authenticated. Raises on timeout."""
    resp = await _send_command(client, {"Api": "Login", "PhoneID": phone_id})
    return resp.get("Result") == "Success"

async def pair(client: BleakClient, phone_id: str) -> bool:
    """Send Pair command. Device must already be in pairing mode."""
    resp = await _send_command(client, {"Api": "Pair", "PhoneID": phone_id})
    return resp.get("Result") == "Success"

async def get_work_state(client: BleakClient) -> FanState:
    resp = await _send_command(client, {"Api": "GetWorkState"})
    raw_temp = resp.get("Temp_Sample")
    raw_hum = resp.get("Humidity_Sample")
    return FanState(
        mode=resp.get("Mode", FanMode.IDLE),
        range=resp.get("Range"),
        temp_fahrenheit=raw_temp / 10 if isinstance(raw_temp, (int, float)) and 0 <= raw_temp <= 2000 else None,
        humidity_percent=raw_hum / 10 if isinstance(raw_hum, (int, float)) and 0 <= raw_hum <= 1000 else None,
    )

async def get_fan_info(client: BleakClient) -> FanInfo:
    resp = await _send_command(client, {"Api": "GetFanInfo"})
    return FanInfo(
        name=resp.get("Name", "QuietCool Fan")[:64].strip(),
        model=resp.get("Model", "")[:64].strip(),
        serial=resp.get("SerialNum", "")[:64].strip(),
    )

async def set_mode_idle(client: BleakClient) -> None:
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.IDLE})

async def set_mode_timer(client: BleakClient, speed: str, hours: int = 8, minutes: int = 0) -> None:
    if speed not in (FanSpeed.HIGH, FanSpeed.LOW):
        raise ValueError(f"Invalid speed: {speed!r}. Must be 'HIGH' or 'LOW'")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid timer duration: {hours}h {minutes}m")
    await _send_command(client, {"Api": "SetTime", "SetHour": hours, "SetMinute": minutes, "SetTime_Range": speed})
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.TIMER})

async def _send_command(client: BleakClient, payload: dict) -> dict:
    """Send JSON command and await response via notify. Thread-safe via caller's lock."""
    response_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    recv_buffer = bytearray()

    def handle_notify(_: object, data: bytearray) -> None:
        nonlocal recv_buffer
        recv_buffer += data
        if len(recv_buffer) > MAX_RECV_BUFFER:
            _LOGGER.warning("BLE notify buffer overflow; resetting")
            recv_buffer = bytearray()
            return
        try:
            msg = json.loads(recv_buffer.decode("utf-8"))
            recv_buffer = bytearray()
            response_queue.put_nowait(msg)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # incomplete chunk, keep accumulating

    # start_notify MUST precede the write
    await client.start_notify(CHAR_UUID, handle_notify)
    try:
        raw = json.dumps(payload).encode("utf-8")
        char = client.services.get_characteristic(CHAR_UUID)
        chunk_size = char.max_write_without_response_size
        for i in range(0, len(raw), chunk_size):
            await client.write_gatt_char(CHAR_UUID, raw[i : i + chunk_size], response=False)
        return await asyncio.wait_for(response_queue.get(), timeout=COMMAND_TIMEOUT)
    except asyncio.TimeoutError as err:
        raise TimeoutError(f"No response to {payload.get('Api')} within {COMMAND_TIMEOUT}s") from err
    finally:
        await client.stop_notify(CHAR_UUID)
```

**Research Insights:**
- `start_notify` before write: if the device responds in the window between write returning and notify being registered, the response is lost and `get_response()` blocks forever. Always subscribe first.
- `asyncio.Queue(maxsize=1)` for response correlation is cleaner than `asyncio.Semaphore`. Handles rapid successive responses correctly by discarding the surplus (they won't happen in practice).
- `MAX_RECV_BUFFER = 1024` bytes prevents unbounded growth from malformed responses. Real responses are all <200 bytes.
- `stop_notify` in `finally` prevents stale callbacks from firing on the next command.
- Field sanitization in `FanInfo` (`.strip()[:64]`) defends against device-sourced strings in log messages.
- Validate `temp_raw` range (`0–2000`) before dividing: `2000 / 10 = 200°F`, far above any plausible attic temperature, so values outside this range indicate sensor error.

### coordinator.py — led_ble Dual-Lock Pattern

Replace the single `asyncio.Semaphore` in `api.py` with the `led_ble` dual-lock + idle-disconnect-timer pattern at the coordinator level.

```python
# coordinator.py
from __future__ import annotations
import asyncio
import logging
from collections.abc import Callable, Coroutine
from time import monotonic
from typing import Any

from bleak import BleakClient
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothScanningMode, BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import api
from .api import FanInfo, FanState
from .const import DOMAIN, POLL_INTERVAL_SECONDS, KEEP_ALIVE_SECONDS

_LOGGER = logging.getLogger(__name__)


class QuietCoolBLECoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Coordinator for QuietCool BLE fan. Owns all BLE connections."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        address: str,
        phone_id: str,
        fan_info: FanInfo,
    ) -> None:
        super().__init__(
            hass,
            logger,
            address=address,
            mode=BluetoothScanningMode.ACTIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll,
            connectable=True,
        )
        self.phone_id = phone_id
        self.fan_state: FanState | None = None
        self.fan_info: FanInfo = fan_info  # fetched once during setup, not on every poll

        self._connect_lock = asyncio.Lock()   # serializes establish_connection
        self._operation_lock = asyncio.Lock() # serializes GATT writes within a connection
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self._idle_timer_handle: asyncio.TimerHandle | None = None
        self._poll_task: asyncio.Task | None = None
        self._consecutive_failures = 0

    @callback
    def _needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        return (
            self.hass.state == CoreState.running
            and (
                seconds_since_last_poll is None
                or seconds_since_last_poll > POLL_INTERVAL_SECONDS
            )
            and bool(
                bluetooth.async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        )

    async def _async_poll(self, service_info: BluetoothServiceInfoBleak) -> None:
        self._poll_task = asyncio.current_task()
        try:
            await self.async_execute(self._poll_operation)
            self._consecutive_failures = 0
        except Exception:
            self._consecutive_failures += 1
            raise
        finally:
            self._poll_task = None

    async def _poll_operation(self, client: BleakClient) -> None:
        self.fan_state = await api.get_work_state(client)

    async def async_execute(
        self, operation: Callable[[BleakClient], Coroutine[Any, Any, None]]
    ) -> None:
        """Single entry point for ALL BLE operations. Prevents concurrent connections."""
        client = await self._ensure_connected()
        async with self._operation_lock:
            try:
                await operation(client)
            except TimeoutError as err:
                raise UpdateFailed(str(err)) from err

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        """Return the active connection, opening one if needed. Thread-safe."""
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                self._reset_idle_timer()
                return self._client

            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                raise UpdateFailed(f"No connectable BLE device found for {self.address}")

            self._expected_disconnect = False
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.address,
                    disconnected_callback=self._handle_disconnect,
                    max_attempts=2,  # fast-fail; coordinator backoff handles retries
                )
            except Exception as err:
                raise UpdateFailed(f"Could not connect to {self.address}: {err}") from err

            try:
                authenticated = await api.login(client, self.phone_id)
                if not authenticated:
                    await client.disconnect()
                    raise UpdateFailed(
                        "Login rejected — PhoneID mismatch or device was reset. "
                        "Re-pairing required."
                    )
            except Exception:
                await client.disconnect()
                raise

            self._client = client
            self._reset_idle_timer()
            return client

    def _handle_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected BLE disconnect. Does NOT cancel other asyncio tasks."""
        if self._expected_disconnect:
            return
        _LOGGER.warning("QuietCool %s: Unexpected BLE disconnect", self.address)
        self._client = None
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
            self._idle_timer_handle = None
        # Cancel only our own poll task, not all HA tasks
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    def _reset_idle_timer(self) -> None:
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
        self._idle_timer_handle = self.hass.loop.call_later(
            KEEP_ALIVE_SECONDS, self._schedule_idle_disconnect
        )

    def _schedule_idle_disconnect(self) -> None:
        self.hass.async_create_task(self._async_idle_disconnect())

    async def _async_idle_disconnect(self) -> None:
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                self._expected_disconnect = True
                await self._client.disconnect()
                self._client = None

    async def async_stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._async_idle_disconnect()
        await super().async_stop()
```

**Research Insights:**
- **`_needs_poll` parameter is `seconds_since_last_poll: float | None`** (not `last_poll`). HA passes elapsed seconds directly. The `@callback` decorator is required — this is a synchronous HA callback.
- **`CoreState.running` guard** prevents polling during HA startup/shutdown. Without it, entities go "unavailable" on boot before the BLE stack is ready.
- **Connectable check in `_needs_poll`** prevents `_async_poll` from being called when no connectable adapter/proxy can reach the device.
- **`max_attempts=2`** (not 4) — worst case 20 seconds vs 40. Add coordinator-level backoff instead: after 3 consecutive failures, `_needs_poll` interval doubles (up to 5 min), preventing rapid hammering of an offline device.
- **`KEEP_ALIVE_SECONDS = 60`** — connection stays alive 60 seconds after last use. The `led_ble` reference uses 120s; 60s is more conservative for shared BLE adapter slots.
- **`_handle_disconnect` does NOT call `asyncio.all_tasks().cancel()`** — this is the critical difference from the upstream `emerose` library.
- **`fan_info` fetched once at setup** (passed into `__init__`), not on every poll. Simplifies `_async_poll` to a single `GetWorkState` call.
- **`ActiveBluetoothDataUpdateCoordinator[None]`** — the generic type is `None` because this coordinator stores state directly on `self`, not via the coordinator's data return mechanism.

### config_flow.py

```python
# config_flow.py
from __future__ import annotations
import secrets
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from . import api
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator

CONF_PHONE_ID = "phone_id"


def _generate_phone_id() -> str:
    return secrets.token_hex(8)  # 8 bytes = 16 hex chars; cryptographically random


class QuietCoolBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _discovery_info: BluetoothServiceInfoBleak | None = None
    _phone_id: str = ""

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._phone_id = _generate_phone_id()
            return await self.async_step_pair()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery_info.name,
                "address": self._discovery_info.address,
            },
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt user to press physical Pair button, then attempt BLE Pair."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Attempt pairing
            # ... establish connection, call api.pair(), create entry on success
            ...
        return self.async_show_form(
            step_id="pair",
            errors=errors,
            description_placeholders={"name": self._discovery_info.name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup fallback when auto-discovery is not available."""
        discovered = [
            info
            for info in async_discovered_service_info(self.hass)
            if info.name and info.name.startswith("ATTICFAN")
        ]
        # Show selection form from discovered devices...
        ...
```

**Research Insights:**
- **PhoneID is always auto-generated** — remove the optional text input entirely. No user has a valid reason to type a 16-char hex string. Use `secrets.token_hex(8)` (CSPRNG, not `random`).
- **`_set_confirm_only()`** — shows a yes/no confirmation with no editable fields. Correct pattern for a headless BLE device.
- **Store `_phone_id` on `self`** between steps — HA config flow steps cannot take positional arguments; the step method signature is always `(self, user_input: dict | None = None) -> ConfigFlowResult`.
- **`context["title_placeholders"]`** — the HA UI uses this to show a meaningful title in the config flow modal (e.g., "ATTICFAN_12345").
- **`async_step_user` fallback** — required for when the user adds the integration manually before any advertisement is seen.

### fan.py

```python
# fan.py
from __future__ import annotations
import dataclasses
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import FanMode, FanSpeed
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator

PRESET_LOW = "Low"
PRESET_HIGH = "High"

_BLE_SPEED = {PRESET_LOW: FanSpeed.LOW, PRESET_HIGH: FanSpeed.HIGH}


class QuietCoolFanEntity(CoordinatorEntity[QuietCoolBLECoordinator], FanEntity):
    _attr_has_entity_name = True
    _attr_name = None  # primary entity; device name is the entity name
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [PRESET_LOW, PRESET_HIGH]

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.address

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.fan_info.name,
            manufacturer="QuietCool",
            model=self.coordinator.fan_info.model or None,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.fan_state is not None

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.mode != FanMode.IDLE

    @property
    def preset_mode(self) -> str | None:
        if self.coordinator.fan_state is None:
            return None
        speed = self.coordinator.fan_state.range
        return {FanSpeed.LOW: PRESET_LOW, FanSpeed.HIGH: PRESET_HIGH}.get(speed)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        speed = _BLE_SPEED.get(preset_mode or PRESET_LOW, FanSpeed.LOW)
        await self.coordinator.async_execute(
            lambda client: self.coordinator.api_turn_on(client, speed)
        )
        # Optimistic state update — no re-poll needed
        if self.coordinator.fan_state is not None:
            self.coordinator.fan_state = dataclasses.replace(
                self.coordinator.fan_state, mode=FanMode.TIMER, range=speed
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_execute(
            lambda client: api.set_mode_idle(client)
        )
        if self.coordinator.fan_state is not None:
            self.coordinator.fan_state = dataclasses.replace(
                self.coordinator.fan_state, mode=FanMode.IDLE, range=None
            )
        self.async_write_ha_state()
```

**Research Insights:**
- **`FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF`** — mandatory since HA 2024.8. Without these, `hassfest` validation fails and HA does not register turn on/off services for the entity.
- **Fan entities NEVER call `establish_connection` directly** — all BLE operations go through `coordinator.async_execute()`. This is the fix for the dual-connection race condition.
- **Optimistic state update** — after a successful command, `fan_state` is updated immediately with `dataclasses.replace()` and `async_write_ha_state()` is called. The user sees instant feedback; the next normal poll confirms actual device state. Do NOT call `coordinator.async_request_refresh()` after commands — this opens a redundant connection right after the command connection just closed.
- **`available` override** — guards `is_on` and `preset_mode` from `AttributeError` when `fan_state is None` before first poll.
- **`CoordinatorEntity[QuietCoolBLECoordinator]`** — generic type parameter makes `self.coordinator` properly typed throughout, eliminating attribute access type errors.
- **`_attr_unique_id = coordinator.address`** — required for HA entity registry persistence across restarts.

### sensor.py

```python
# sensor.py
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature, PERCENTAGE
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator


class QuietCoolTemperatureSensor(CoordinatorEntity[QuietCoolBLECoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_name = "Temperature"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_temperature"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.fan_state is not None

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.temp_fahrenheit  # already scaled: Temp_Sample / 10


class QuietCoolHumiditySensor(CoordinatorEntity[QuietCoolBLECoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_name = "Humidity"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_humidity"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.fan_state is not None

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.humidity_percent
```

**Research Insights:**
- **Temperature formula is confirmed** — `Temp_Sample / 10 = °F`. No need to ship as `EntityCategory.DIAGNOSTIC` with unknown units. The scaling is validated from `emerose/quietcool` source.
- **`SensorStateClass.MEASUREMENT`** — required for HA's long-term statistics to track this sensor. Without it, Grafana/energy dashboard integrations won't work.
- **Unique ID per sensor** — `f"{address}_temperature"` and `f"{address}_humidity"` ensures each sensor persists independently in the entity registry.

### const.py

```python
# const.py
from typing import Final

DOMAIN: Final = "quietcool_ble"
PLATFORMS: Final = ["fan", "sensor"]
POLL_INTERVAL_SECONDS: Final = 10   # 10s for responsive fan state; temp/humidity same call
KEEP_ALIVE_SECONDS: Final = 60      # idle BLE connection timeout before voluntary disconnect
MAX_CONSECUTIVE_FAILURES_BEFORE_BACKOFF: Final = 3
```

**Research Insights:**
- **Polling at 10s not 30s** — `GetWorkState` returns both fan state AND sensors in one call. There's no added BLE cost to polling faster. 10s makes fan entity state changes much more responsive when controlled by the physical remote or Android app.
- **BLE connection slot management** — if more than 2 fans are configured, add a hass-level `asyncio.Semaphore(2)` in `hass.data[DOMAIN]` to cap concurrent connections across all coordinator instances, leaving adapter capacity for other integrations.

### Implementation Phases

#### Phase 1: Protocol Library (api.py)

- [ ] Write `FanState` and `FanInfo` frozen dataclasses in `api.py`
- [ ] Write `FanMode` and `FanSpeed` StrEnum classes
- [ ] Implement `_send_command()` with `asyncio.Queue` response correlation, `MAX_RECV_BUFFER` limit, `COMMAND_TIMEOUT`
- [ ] Implement `login()`, `pair()`, `get_work_state()`, `get_fan_info()`
- [ ] Implement `set_mode_idle()` and `set_mode_timer()` with input validation
- [ ] Verify `start_notify` always precedes write in `_send_command`
- [ ] Add unit tests against mock BleakClient (inject mock notify callbacks)
- [ ] Verify temperature: `Temp_Sample / 10 = °F` with real device (`720` → `72.0°F`)

#### Phase 2: Coordinator + __init__.py

- [ ] Implement `QuietCoolBLECoordinator` with dual-lock (`_connect_lock` + `_operation_lock`)
- [ ] Implement `_ensure_connected()` with idle-disconnect timer (`KEEP_ALIVE_SECONDS = 60`)
- [ ] Implement `_handle_disconnect()` — cancel only `_poll_task`, NOT `asyncio.all_tasks()`
- [ ] Implement `_needs_poll()` with `@callback`, `CoreState.running` guard, connectable check, correct `seconds_since_last_poll` parameter name
- [ ] Implement `async_execute()` as single entry point for all entity commands
- [ ] Implement `async_stop()` that cancels `_poll_task` and closes connection
- [ ] Wire `__init__.py`: fetch `GetFanInfo` once in `async_setup_entry`, pass to coordinator
- [ ] Write `manifest.json` with single `ATTICFAN*` BLE matcher

#### Phase 3: Config Flow

- [ ] Implement `async_step_bluetooth` with `async_set_unique_id` + `_abort_if_unique_id_configured`
- [ ] Implement `async_step_bluetooth_confirm` with `_set_confirm_only()` (no PhoneID field)
- [ ] Implement `async_step_pair` that prompts Pair button press and calls `api.pair()`
- [ ] Implement `async_step_user` fallback for manual setup
- [ ] Wire `_generate_phone_id()` using `secrets.token_hex(8)`
- [ ] Handle `PairState: No` response → persistent notification prompting re-pair
- [ ] Write `strings.json` and `translations/en.json`

#### Phase 4: Fan + Sensor Entities

- [ ] Implement `QuietCoolFanEntity` with all three `FanEntityFeature` flags
- [ ] Implement `async_turn_on/off` routing through `coordinator.async_execute()` (never direct BLE)
- [ ] Implement optimistic state updates with `dataclasses.replace()` + `async_write_ha_state()`
- [ ] Implement `available` override guarding against `fan_state is None`
- [ ] Implement `QuietCoolTemperatureSensor` and `QuietCoolHumiditySensor`
- [ ] Verify entity device registry (name, model from `fan_info` stored in `entry.data`)

#### Phase 5: HACS Packaging + Polish

- [ ] Create `hacs.json` (only `name` required)
- [ ] Add `.github/workflows/validate.yml` with `hacs/action@main` step (required for HACS store)
- [ ] Write `README.md` with: setup instructions, pairing UX photos/diagram, supported models table, security disclosure (no BLE encryption, replay possible), PhoneID slot limitation warning
- [ ] Run `hassfest` validation locally
- [ ] Address 3-speed `MEDIUM` if hardware testing confirms protocol support

---

## Alternative Approaches Considered

| Approach | Why Rejected |
|---|---|
| **ESPHome firmware flash** | Voids warranty; requires FTDI hardware; irreversible |
| **Relay interlock (Shelly Pro 3)** | No temperature sensor; adds external hardware; doesn't use BLE |
| **WiFi hub (stabbylambda)** | Targets discontinued hardware; archived 2022 |
| **Cloud API** | QuietCool has no published cloud API for BLE controllers |
| **Persistent always-on BLE connection** | Permanently consumes 1 adapter slot per fan; led_ble's idle-timeout approach is the right compromise |
| **Connect-per-operation** | Each poll cycle incurs 200–600ms connection overhead; rapidly followed commands (turn on + poll) make a second connection that may fail on the still-disconnecting device |

---

## System-Wide Impact

### Interaction Graph

Config flow → creates `ConfigEntry` (with `address`, `phone_id`, `fan_info` in `entry.data`) → `async_setup_entry` fetches `GetFanInfo` once → creates `QuietCoolBLECoordinator` → `coordinator.async_start()` registers with HA Bluetooth manager → on advertisement: `_needs_poll()` (checks `CoreState.running` + connectable) → if true: `_async_poll()` → `async_execute()` → `_ensure_connected()` acquires `_connect_lock` → `establish_connection()` → `api.login()` → `api.get_work_state()` → `fan_state` updated → entities notified via `async_write_ha_state()`.

Fan entity `async_turn_on()` → `coordinator.async_execute()` (acquires `_connect_lock` then `_operation_lock`) → `_ensure_connected()` (reuses open connection or reconnects) → `api.set_mode_timer()` → optimistic `fan_state` update → `async_write_ha_state()`. No redundant post-command poll.

### Error & Failure Propagation

- **BLE connection failure** → `establish_connection` retries 2 times → raises `Exception` → `_ensure_connected` raises `UpdateFailed` → coordinator marks entities "unavailable" → `_consecutive_failures` increments → after 3 failures, `_needs_poll` uses exponential backoff (up to 5 min) to stop hammering offline device
- **Login failure (wrong PhoneID)** → `api.login()` returns `False` → `_ensure_connected` raises `UpdateFailed("Login rejected")` → entities go "unavailable" → persistent notification: "QuietCool re-pairing required"
- **BLE response timeout** → `asyncio.wait_for` raises `TimeoutError` → `api._send_command` raises → `async_execute` catches as `UpdateFailed` → entities go "unavailable"
- **Unexpected disconnect mid-operation** → `_handle_disconnect` fires (from bleak callback thread) → sets `_client = None`, cancels `_poll_task` → next `_ensure_connected` call opens fresh connection
- **Concurrent command + poll** → both acquire `_connect_lock` → second waits → first completes and releases → second checks `self._client.is_connected` → reuses or reconnects → no double-connection

### State Lifecycle Risks

- **PhoneID mismatch after device reset or Android app pairing:** Device stores one PhoneID. If the Android app pairs, it overwrites the stored ID. Integration detects `PairState: No` on login → raises `UpdateFailed` → fires persistent notification → stops polling (backoff) until user re-pairs via config entry reconfiguration.
- **Idle-timer disconnect races:** The idle timer fires from `hass.loop.call_later`. If a new operation starts within the same event loop iteration as the timer fires, `_connect_lock` ensures only one of them wins the connection slot. The idle disconnect checks `is_connected` under the lock before disconnecting.
- **`fan_state` before first poll:** All entity properties guard on `fan_state is not None` via `available` override. No crash on startup.
- **Partial `GetFanInfo` unavailability:** `fan_info` is fetched once during `async_setup_entry`. If that fails, setup fails and the config entry is not created — fail fast rather than polling forever.

### API Surface Parity

- `fan.py` uses `FanEntityFeature.PRESET_MODE` (not percentage) — matches the BLE "HIGH"/"LOW" discrete modes exactly. Percentage would require synthetic mapping.
- If 3-speed models are confirmed, add `FanSpeed.MEDIUM` to `StrEnum` and `PRESET_MEDIUM` to `_attr_preset_modes` in both `api.py` and `fan.py` simultaneously.

### Integration Test Scenarios

1. **Happy path auto-discovery:** HA detects `ATTICFAN_12345` → config flow confirm → pairing step → user presses physical button → `api.pair()` succeeds → entry created → fan entity + 2 sensor entities appear → turn on Low → verify optimistic state → wait for next poll → confirm `GetWorkState` response matches
2. **Manual setup (no advertisement):** User adds integration manually → `async_step_user` shows list of discovered `ATTICFAN*` devices → selects one → same pairing flow
3. **Device goes out of range:** No advertisements → `_needs_poll` returns `False` (connectable check) → polling stops → entities stay in last-known state → after `UNAVAILABLE_TIMEOUT` HA marks unavailable → device comes back in range → advertisement seen → polling resumes → entities recover
4. **Android app pairing overwrites PhoneID:** Fan used from Android app → HA `login()` returns `{"PairState": "No"}` → `_ensure_connected` raises `UpdateFailed` → persistent notification: "Re-pairing required" → user taps notification → `async_step_reauth` flow → presses physical Pair button → new PhoneID generated and registered
5. **BLE proxy scenario:** Fan too far from HA host → ESPHome BT proxy forwards advertisement → `_needs_poll` connectable check passes (proxy is connectable) → `_ensure_connected` uses `async_ble_device_from_address(..., connectable=True)` which returns the proxy-backed device → `establish_connection` connects through proxy → full operation completes (slower, ~500ms, but functional)

---

## Acceptance Criteria

### Functional

- [ ] Auto-discovery notification appears in HA when any `ATTICFAN*` BLE device is visible
- [ ] Config flow completes without the user needing to know BLE UUIDs or write YAML
- [ ] Fan entity supports `turn_on`, `turn_off`, `low` and `high` preset modes
- [ ] Fan entity reflects commanded state immediately (optimistic update, not waiting for poll)
- [ ] Temperature sensor reports `Temp_Sample / 10` in °F with `SensorDeviceClass.TEMPERATURE`
- [ ] Humidity sensor reports value in `%` with `SensorDeviceClass.HUMIDITY`
- [ ] Integration survives HA restart without requiring re-pairing
- [ ] Integration works via HA Bluetooth proxy as well as local adapter
- [ ] All entities report "unavailable" when fan is powered off or out of range
- [ ] `hassfest` validation passes
- [ ] `hacs/action` validation passes

### Non-Functional

- [ ] Polling interval: 10 seconds
- [ ] Fan state response to command: ≤ 2 seconds (optimistic) + ≤ 10 seconds (confirmed via poll)
- [ ] BLE connection attempt timeout: ≤ 10 seconds total per attempt
- [ ] No blocking calls in the event loop
- [ ] No global state — all state scoped to `ConfigEntry`
- [ ] PhoneID never logged at any log level

### Security

- [ ] PhoneID generated with `secrets.token_hex(8)` (CSPRNG)
- [ ] PhoneID not present in any log message, diagnostic dump, or persistent notification
- [ ] `manifest.json` uses only the `local_name: ATTICFAN*` matcher (not loose service UUID matcher)
- [ ] README includes security disclosure: no BLE link-layer encryption, replay attacks physically possible within BLE range

### Quality Gates

- [ ] Integration tested against real QuietCool hardware
- [ ] Temperature: `Temp_Sample / 10` verified against reference thermometer (expect ±1°F)
- [ ] README documents Pair button UX clearly with photos or diagram
- [ ] 72-hour continuous run with no manual intervention

---

## Success Metrics

- User installs and configures in under 5 minutes
- Fan entity state reflects commands within 2 seconds
- Temperature sensor matches reference thermometer within ±1°F
- Integration survives 72-hour continuous run with no manual intervention
- Zero `asyncio.all_tasks()` calls in the codebase (grep check)

---

## Dependencies & Prerequisites

| Dependency | Version | Purpose |
|---|---|---|
| `bleak-retry-connector` | ≥ 3.0.0 | Reliable BLE connections with dual-lock support |
| `bluetooth-data-tools` | ≥ 1.0.0 | HA Bluetooth utilities |
| `bluetooth-adapters` | (HA built-in) | Adapter enumeration |
| Home Assistant | ≥ 2024.8 | `FanEntityFeature.TURN_ON/OFF` available |

**Hardware required for development:**
- QuietCool fan with BLE controller (ATTICFAN* advertisement)
- Reference thermometer to verify temperature formula (`Temp_Sample / 10 = °F`)
- Bluetooth adapter on HA host (or ESPHome BT proxy)

**No firmware modification required or recommended.**

---

## Risk Analysis & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Android app overwrites single PhoneID slot | Medium | Medium | Detect `PairState: No`, fire persistent notification, add reauth flow |
| QuietCool changes BLE protocol in firmware update | Low | High | Document tested firmware versions; pin in README |
| BLE adapter connection slot exhaustion (3+ fans) | Medium | Medium | Per-domain `asyncio.Semaphore(2)` in `hass.data` |
| BT proxy latency makes `COMMAND_TIMEOUT = 10s` too tight | Low | Low | Make `COMMAND_TIMEOUT` a const; increase to 15s if proxy reports needed |
| 3-speed `MEDIUM` BLE value unconfirmed | Medium | Low | Ship 2-speed; add `MEDIUM` once hardware-confirmed |
| `emerose/quietcool` license incompatibility | Low | Medium | Verify MIT/Apache; integration is a rewrite from protocol spec, not a copy |
| BLE replay attack within physical range | Low | Low | Firmware constraint; disclose in README; physical access required |

---

## Security Disclosure (README Required)

The following must be prominently documented in `README.md`:

1. **No link-layer encryption:** BLE communication is unencrypted. Any device within ~10m can passively capture the PhoneID and all commands with a BLE sniffer. This is a firmware limitation; the integration cannot fix it.
2. **Replay attacks:** Captured commands can be replayed by a nearby attacker. Practical risk is low (requires physical proximity) but non-zero in multi-unit buildings.
3. **Single PhoneID slot:** The device stores one credential. Pairing the QuietCool Android app will break this integration until re-paired.
4. **PhoneID security:** Treat it like a password — do not share HA config directory with untrusted processes.

---

## Future Considerations

- **Reauth flow** — handle `PairState: No` via `async_step_reauth` in config flow (generates new PhoneID + re-pairs)
- **Timer/schedule mode** — expose `SetHour`/`SetMinute` as a `number` entity for configurable run duration
- **Thermostat mode (`TH`)** — expose as a `climate` entity if BLE-accessible (unconfirmed)
- **3-speed support** — add `FanSpeed.MEDIUM` once hardware testing confirms `"MEDIUM"` is valid in `SetTime_Range`
- **Diagnostic sensors** — firmware version, protect temp threshold from `GetVersion`
- **Multi-adapter staggering** — stagger `_needs_poll` offset by config entry index to prevent all fans polling simultaneously and exhausting connection slots

---

## Sources & References

### Protocol Reverse Engineering (Authoritative Sources)

- [emerose/quietcool](https://github.com/emerose/quietcool) — Python BLE client library; protocol source; **do not port `disconnect callback` — rewrite it**
- [awkaplan/quietcool-esphome](https://github.com/awkaplan/quietcool-esphome) — ESPHome YAML; independently confirms BLE UUIDs and JSON formats
- [blog.overt.org — QuietCool AFG SMT PRO-2.0 with ESPHome](https://blog.overt.org/2025/11/03/quietcool-afg-smt-pro-2-0-attic-fan-with-home-assistant-via-esphome/) — Hardware GPIO map

### HA Bluetooth Integration Architecture

- [HA Developer Docs: Bluetooth Fetching Data](https://developers.home-assistant.io/docs/core/bluetooth/bluetooth_fetching_data/) — Coordinator selection; `_needs_poll` API
- [HA Developer Docs: Fan Entity](https://developers.home-assistant.io/docs/core/entity/fan/) — `FanEntityFeature` flags
- [HA Developer Docs: Integration Manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/) — `bluetooth` matcher wildcard rules
- [HA Core: `bluetooth/match.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/bluetooth/match.py) — Wildcard rule: first 3 chars must be literal
- [HA Core: `active_update_coordinator.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/bluetooth/active_update_coordinator.py) — `ActiveBluetoothDataUpdateCoordinator[_T]` API
- [HA Core: `fan/__init__.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/fan/__init__.py) — `TURN_ON`/`TURN_OFF` mandatory since 2024.8

### BLE Connection Pattern Reference

- [Bluetooth-Devices/led-ble](https://github.com/Bluetooth-Devices/led-ble) — **Primary reference for dual-lock + idle-disconnect-timer pattern**
- [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector) — `establish_connection` signature; `BleakClientWithServiceCache`
- [habluetooth](https://github.com/Bluetooth-Devices/habluetooth) — `BluetoothServiceInfoBleak` fields

### HACS

- [HACS Publishing Guide](https://www.hacs.xyz/docs/publish/integration/) — submission requirements
- [hacs/action GitHub Action](https://www.hacs.xyz/docs/publish/action/) — validation workflow

### Existing HA Integration Attempts

- [stabbylambda/homeassistant-quietcool](https://github.com/stabbylambda/homeassistant-quietcool) — Archived 2022; WiFi hub only; **not applicable**
- [HA Community: QuietCool Integration](https://community.home-assistant.io/t/quietcool-integration/913242) — Active thread; announce v0.1.0 here
