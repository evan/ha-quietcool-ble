"""Constants for the QuietCool BLE integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "quietcool_ble"
PLATFORMS: Final = ["fan", "sensor", "select", "number"]

# BLE protocol identifiers
SERVICE_UUID: Final = "000000ff-0000-1000-8000-00805f9b34fb"
CHAR_UUID: Final = "0000ff01-0000-1000-8000-00805f9b34fb"
BLE_NAME_PREFIX: Final = "ATTICFAN"

# Polling and connection
POLL_INTERVAL_SECONDS: Final = 10
KEEP_ALIVE_SECONDS: Final = 60   # idle BLE connection timeout before voluntary disconnect
COMMAND_TIMEOUT: Final = 10.0    # seconds per BLE round-trip
MAX_RECV_BUFFER: Final = 1024    # bytes; all real responses are <200 bytes
MAX_CONNECT_ATTEMPTS: Final = 2  # per establish_connection call; fast-fail, coordinator backsoff

# Config entry data keys
CONF_PHONE_ID: Final = "phone_id"
CONF_FAN_NAME: Final = "fan_name"
CONF_FAN_MODEL: Final = "fan_model"
CONF_FAN_SERIAL: Final = "fan_serial"
CONF_PROTOCOL: Final = "protocol"
