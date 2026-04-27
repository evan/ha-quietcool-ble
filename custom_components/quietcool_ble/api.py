"""QuietCool BLE protocol implementation.

All communication uses a single GATT characteristic (bidirectional):
  Service:  000000ff-0000-1000-8000-00805f9b34fb
  Char:     0000ff01-0000-1000-8000-00805f9b34fb

Protocol: JSON-over-BLE, UTF-8, chunked by MTU size.
Authentication: {"Api": "Login", "PhoneID": "<16-hex-chars>"} on every connection.
Temperature: Temp_Sample / 10 = °F (confirmed from emerose/quietcool source).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import StrEnum

from bleak import BleakClient

from .const import CHAR_UUID, COMMAND_TIMEOUT, MAX_RECV_BUFFER

_LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class FanInfo:
    name: str
    model: str
    serial: str


class QuietCoolError(Exception):
    """Base error for QuietCool BLE protocol errors."""


class AuthenticationError(QuietCoolError):
    """Raised when login is rejected (wrong or unregistered PhoneID)."""


async def login(client: BleakClient, phone_id: str) -> bool:
    """Send Login command. Returns True if authenticated, False if pairing needed."""
    resp = await _send_command(client, {"Api": "Login", "PhoneID": phone_id})
    if resp.get("Result") == "Success":
        return True
    if "PairState" in resp:
        return False
    _LOGGER.warning("Unexpected login response: %s", resp)
    return False


async def pair(client: BleakClient, phone_id: str) -> bool:
    """Send Pair command. Device must be in pairing mode (physical button pressed)."""
    resp = await _send_command(client, {"Api": "Pair", "PhoneID": phone_id})
    return resp.get("Result") == "Success"


async def get_work_state(client: BleakClient) -> FanState:
    """Poll current fan operating state including temperature and humidity."""
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
    )


async def get_fan_info(client: BleakClient) -> FanInfo:
    """Fetch device identification (name, model, serial number)."""
    resp = await _send_command(client, {"Api": "GetFanInfo"})
    return FanInfo(
        name=str(resp.get("Name", "QuietCool Fan"))[:64].strip(),
        model=str(resp.get("Model", ""))[:64].strip(),
        serial=str(resp.get("SerialNum", ""))[:64].strip(),
    )


async def set_mode_idle(client: BleakClient) -> None:
    """Turn the fan off (Idle mode)."""
    await _send_command(client, {"Api": "SetMode", "Mode": FanMode.IDLE})


async def set_mode_timer(
    client: BleakClient,
    speed: str,
    hours: int = 8,
    minutes: int = 0,
) -> None:
    """Turn the fan on at the given speed for the given duration."""
    if speed not in (FanSpeed.HIGH, FanSpeed.LOW):
        raise ValueError(f"Invalid speed {speed!r}; must be 'HIGH' or 'LOW'")
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid timer duration: {hours}h {minutes}m")
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


async def _send_command(client: BleakClient, payload: dict) -> dict:
    """Send a JSON command and await the device's notify response.

    start_notify is registered before the write to avoid missing the response
    in the window between write-returning and notify-registration.
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
        try:
            msg = json.loads(recv_buffer.decode("utf-8"))
            recv_buffer = bytearray()
            response_queue.put_nowait(msg)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # incomplete chunk — keep accumulating

    # Register notify BEFORE writing so we cannot miss the response
    await client.start_notify(CHAR_UUID, handle_notify)
    try:
        raw = json.dumps(payload).encode("utf-8")
        char = client.services.get_characteristic(CHAR_UUID)
        chunk_size = char.max_write_without_response_size
        for i in range(0, len(raw), chunk_size):
            await client.write_gatt_char(
                CHAR_UUID, raw[i : i + chunk_size], response=False
            )
        return await asyncio.wait_for(response_queue.get(), timeout=COMMAND_TIMEOUT)
    except asyncio.TimeoutError as err:
        raise TimeoutError(
            f"No BLE response to '{payload.get('Api')}' within {COMMAND_TIMEOUT}s"
        ) from err
    finally:
        await client.stop_notify(CHAR_UUID)
