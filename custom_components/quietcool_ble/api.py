"""QuietCool BLE protocol implementation — dual-protocol support.

All communication uses a single GATT characteristic (bidirectional):
  Service:  000000ff-0000-1000-8000-00805f9b34fb
  Char:     0000ff01-0000-1000-8000-00805f9b34fb

TWO PROTOCOL VERSIONS EXIST based on firmware version:

  V1 (firmware < 3.9, e.g. V2.x):
    Commands: {"Api": "GetFanInfo"}
    Responses: {"Name": "...", "Model": "...", "SerialNum": "..."}

  V2 (firmware >= 3.9):
    Commands: {"A": 17}  (numeric API codes)
    Responses: QQ{"A": 17, "N": "...", "M": "...", "S": "..."}
    The "QQ" prefix must be stripped before JSON parsing.
    Response keys are single characters mapped to field names.

Protocol auto-detected on first GetFanInfo call: if the response contains
"N" (short key) rather than "Name" (long key), V2 is used for all subsequent
commands on that connection.

Temperature: Temp_Sample / 10 = °F (V1 key) or T / 10 = °F (V2 key — unconfirmed).
Source: emerose/quietcool, reddit.com/r/homeassistant/comments/1kyv0pn (u/secretoftheeast)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from bleak import BleakClient

from .const import CHAR_UUID, COMMAND_TIMEOUT, MAX_RECV_BUFFER

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------

class ProtocolVersion(StrEnum):
    V1 = "v1"  # firmware < 3.9: string command names, full response keys
    V2 = "v2"  # firmware >= 3.9: numeric command codes, single-char response keys


# ---------------------------------------------------------------------------
# V2 numeric API codes (reverse-engineered by u/secretoftheeast, Reddit 2025)
# Only GetFanInfo (17) is confirmed. Others marked UNKNOWN and will fall back
# to V1 format until hardware testing confirms the codes.
# ---------------------------------------------------------------------------

class ApiCode(IntEnum):
    GET_FAN_INFO = 17      # confirmed: {"A": 17} → {"N", "M", "S", "G"}
    # The following are unconfirmed — firmware testing needed:
    # GET_WORK_STATE = ?
    # GET_PARAMETER = ?
    # SET_MODE = ?
    # SET_TIME = ?
    # LOGIN = ?
    # PAIR = ?


# V2 response key → semantic name mapping (confirmed from u/secretoftheeast)
_V2_FAN_INFO_KEYS = {
    "N": "Name",
    "M": "Model",
    "S": "SerialNum",
    "G": "GuideSetup",
    "A": "_api_code",
}

# V2 GetWorkState key mapping (unconfirmed — placeholders based on likely pattern)
# Will be populated as hardware testing confirms the mapping.
_V2_WORK_STATE_KEYS: dict[str, str] = {
    # "X": "Mode",        # unknown key
    # "Y": "Range",       # unknown key
    # "T": "Temp_Sample", # unknown key
    # "H": "Humidity_Sample", # unknown key
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class FanMode(StrEnum):
    IDLE = "Idle"
    TIMER = "Timer"


class FanSpeed(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class FanState:
    mode: str
    range: str | None
    temp_fahrenheit: float | None   # Temp_Sample / 10; None if sensor absent/error
    humidity_percent: float | None  # Humidity_Sample / 10; None if sensor absent/error
    sensor_state: str | None = None  # "Normal", "Error", absent on older firmware
    protocol: str = ProtocolVersion.V1


@dataclass(frozen=True, slots=True)
class FanInfo:
    name: str
    model: str
    serial: str
    protocol: str = ProtocolVersion.V1  # detected during get_fan_info()


class QuietCoolError(Exception):
    """Base error for QuietCool BLE protocol errors."""


class AuthenticationError(QuietCoolError):
    """Raised when login is rejected (wrong or unregistered PhoneID)."""


class UnsupportedProtocolError(QuietCoolError):
    """Raised when the device uses V2 protocol for a command not yet mapped."""


# ---------------------------------------------------------------------------
# Public API — protocol-transparent
# ---------------------------------------------------------------------------

async def login(client: BleakClient, phone_id: str) -> bool:
    """Send Login. Returns True if authenticated, False if pairing needed.

    Uses V1 format — u/secretoftheeast reports Login still works in V1 format
    on firmware 3.9 devices. If this proves wrong, we'll need the V2 code.
    """
    resp = await _send_command(client, {"Api": "Login", "PhoneID": phone_id})
    if resp.get("Result") == "Success":
        return True
    if "PairState" in resp:
        return False
    _LOGGER.warning("Unexpected login response: %s", resp)
    return False


async def pair(client: BleakClient, phone_id: str) -> bool:
    """Send Pair. Device must be in pairing mode (physical button pressed).

    Uses V1 format — reported to still work on firmware 3.9.
    """
    resp = await _send_command(client, {"Api": "Pair", "PhoneID": phone_id})
    return resp.get("Result") == "Success"


async def get_fan_info(client: BleakClient) -> FanInfo:
    """Fetch device identification. Auto-detects V1 vs V2 protocol.

    V2 (firmware 3.9+): sends {"A": 17}, parses single-char response keys.
    V1 (firmware < 3.9): sends {"Api": "GetFanInfo"}, parses full-name keys.

    Returns FanInfo with .protocol indicating which version was detected.
    """
    # Try V2 first (firmware 3.9+) — if response has single-char keys, it's V2
    try:
        resp_v2 = await _send_command(client, {"A": ApiCode.GET_FAN_INFO})
        if "N" in resp_v2:
            _LOGGER.debug("Detected protocol V2 (firmware 3.9+) from GetFanInfo response")
            return FanInfo(
                name=str(resp_v2.get("N", "QuietCool Fan"))[:64].strip(),
                model=str(resp_v2.get("M", ""))[:64].strip(),
                serial=str(resp_v2.get("S", ""))[:64].strip(),
                protocol=ProtocolVersion.V2,
            )
        # V2 command returned V1-style keys (shouldn't happen but handle it)
        if "Name" in resp_v2:
            return FanInfo(
                name=str(resp_v2.get("Name", "QuietCool Fan"))[:64].strip(),
                model=str(resp_v2.get("Model", ""))[:64].strip(),
                serial=str(resp_v2.get("SerialNum", ""))[:64].strip(),
                protocol=ProtocolVersion.V1,
            )
    except (TimeoutError, QuietCoolError):
        _LOGGER.debug("V2 GetFanInfo timed out; falling back to V1")

    # Fall back to V1 protocol
    resp_v1 = await _send_command(client, {"Api": "GetFanInfo"})
    return FanInfo(
        name=str(resp_v1.get("Name", "QuietCool Fan"))[:64].strip(),
        model=str(resp_v1.get("Model", ""))[:64].strip(),
        serial=str(resp_v1.get("SerialNum", ""))[:64].strip(),
        protocol=ProtocolVersion.V1,
    )


async def get_work_state(client: BleakClient, protocol: str = ProtocolVersion.V1) -> FanState:
    """Poll current fan operating state including temperature and humidity.

    Pass the protocol version detected during get_fan_info() to use the
    correct wire format.

    NOTE: V2 GetWorkState numeric code is not yet confirmed. On V2 devices
    this will attempt V1 format (which the firmware 3.9 device reports returns
    QQ{} — empty). Until the V2 code is reverse-engineered, V2 devices will
    not have working sensor data. See GitHub issues for tracking.
    """
    if protocol == ProtocolVersion.V2:
        # V2 GetWorkState code is unknown — log a diagnostic warning
        _LOGGER.warning(
            "Device uses firmware 3.9+ (V2 protocol). GetWorkState numeric API "
            "code is not yet confirmed. Sensor data will be unavailable until "
            "the V2 command mapping is reverse-engineered. "
            "See: https://github.com/rwarner/hass-integration-quietcool/issues"
        )
        return FanState(
            mode=FanMode.IDLE,
            range=None,
            temp_fahrenheit=None,
            humidity_percent=None,
            protocol=ProtocolVersion.V2,
        )

    resp = await _send_command(client, {"Api": "GetWorkState"})
    raw_temp = resp.get("Temp_Sample")
    raw_hum = resp.get("Humidity_Sample")
    return FanState(
        mode=resp.get("Mode", FanMode.IDLE),
        range=resp.get("Range"),
        temp_fahrenheit=(
            raw_temp / 10
            if isinstance(raw_temp, (int, float)) and 0 <= raw_temp <= 2000
            else None
        ),
        humidity_percent=(
            raw_hum / 10
            if isinstance(raw_hum, (int, float)) and 0 <= raw_hum <= 1000
            else None
        ),
        sensor_state=resp.get("SensorState"),
        protocol=ProtocolVersion.V1,
    )


async def set_mode_idle(client: BleakClient, protocol: str = ProtocolVersion.V1) -> None:
    """Turn the fan off (Idle mode)."""
    if protocol == ProtocolVersion.V2:
        # V2 SetMode code unknown — attempt V1 format (may work per u/secretoftheeast)
        _LOGGER.debug("V2 device: attempting SetMode Idle with V1 format")
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.IDLE})


async def set_mode_timer(
    client: BleakClient,
    speed: str,
    hours: int = 8,
    minutes: int = 0,
    protocol: str = ProtocolVersion.V1,
) -> None:
    """Turn the fan on at the given speed for the given duration."""
    if speed not in (FanSpeed.HIGH, FanSpeed.LOW):
        raise ValueError(f"Invalid speed {speed!r}; must be 'HIGH' or 'LOW'")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid timer duration: {hours}h {minutes}m")
    if protocol == ProtocolVersion.V2:
        _LOGGER.debug("V2 device: attempting SetTime/SetMode with V1 format")
    await _send_command(
        client,
        {
            "Api": "SetTime",
            "SetHour": hours,
            "SetMinute": minutes,
            "SetTime_Range": speed,
        },
    )
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.TIMER})


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------

def _strip_qq_prefix(data: bytes) -> bytes:
    """Strip the 'QQ' prefix added by firmware 3.9+ before JSON content.

    Firmware 3.9+ prepends b'QQ' to all notify responses. This must be
    removed before JSON parsing. The prefix is not part of the JSON document.
    """
    if data.startswith(b"QQ"):
        return data[2:]
    return data


async def _send_command(client: BleakClient, payload: dict) -> dict:
    """Send a JSON command and await the device's notify response.

    start_notify is registered BEFORE the write to guarantee we cannot miss
    the response in the window between write-returning and notify-registration.

    Handles the firmware 3.9+ "QQ" prefix by stripping it before JSON parsing.
    """
    response_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)
    recv_buffer = bytearray()

    def handle_notify(_: object, data: bytearray) -> None:
        nonlocal recv_buffer
        recv_buffer += data
        if len(recv_buffer) > MAX_RECV_BUFFER:
            _LOGGER.warning(
                "QuietCool BLE notify buffer overflow (%d bytes); resetting",
                len(recv_buffer),
            )
            recv_buffer = bytearray()
            return
        # Strip firmware 3.9+ "QQ" prefix before attempting JSON parse
        candidate = _strip_qq_prefix(bytes(recv_buffer))
        try:
            msg = json.loads(candidate.decode("utf-8"))
            recv_buffer = bytearray()
            response_queue.put_nowait(msg)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # incomplete chunk — keep accumulating

    # Register notify BEFORE writing
    await client.start_notify(CHAR_UUID, handle_notify)
    try:
        raw = json.dumps(payload).encode("utf-8")
        char = client.services.get_characteristic(CHAR_UUID)
        chunk_size = char.max_write_without_response_size
        for i in range(0, len(raw), chunk_size):
            await client.write_gatt_char(
                CHAR_UUID, raw[i : i + chunk_size], response=True
            )
        resp = await asyncio.wait_for(response_queue.get(), timeout=COMMAND_TIMEOUT)
        cmd_label = payload.get("Api") or f"A={payload.get('A')}"
        _LOGGER.debug("QuietCool BLE %s → %s", cmd_label, resp)
        return resp
    except asyncio.TimeoutError as err:
        cmd_label = payload.get("Api") or f"A={payload.get('A')}"
        raise TimeoutError(
            f"No BLE response to '{cmd_label}' within {COMMAND_TIMEOUT}s"
        ) from err
    finally:
        await client.stop_notify(CHAR_UUID)
