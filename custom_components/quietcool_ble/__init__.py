"""QuietCool BLE Home Assistant Integration.

Communicates with QuietCool attic fans over Bluetooth Low Energy using the
stock manufacturer firmware — no firmware modification required.

GetFanInfo is fetched once during setup and stored in ConfigEntry.data.
The coordinator polls GetWorkState every POLL_INTERVAL_SECONDS seconds.
"""
from __future__ import annotations

import logging

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import FanInfo, ProtocolVersion
from .const import (
    CONF_FAN_MODEL,
    CONF_FAN_NAME,
    CONF_FAN_SERIAL,
    CONF_PHONE_ID,
    CONF_PROTOCOL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import QuietCoolBLECoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up QuietCool BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    phone_id: str = entry.data[CONF_PHONE_ID]

    # Build FanInfo from stored config entry data (fetched once during config flow).
    # GetFanInfo sometimes returns bare numbers ("1") for Name/Model; treat those
    # as meaningless and fall back to the BLE advertisement name (entry.title).
    def _meaningful(s: str) -> bool:
        return bool(s) and not s.strip().isdigit() and len(s.strip()) > 1

    raw_name = entry.data.get(CONF_FAN_NAME, "")
    raw_model = entry.data.get(CONF_FAN_MODEL, "")
    fan_info = FanInfo(
        name=raw_name if _meaningful(raw_name) else entry.title,
        model=raw_model if _meaningful(raw_model) else "",
        serial=entry.data.get(CONF_FAN_SERIAL, ""),
        protocol=entry.data.get(CONF_PROTOCOL, ProtocolVersion.V1),
    )

    # Verify device is in BLE range before completing setup.
    # We intentionally do NOT connect here — connecting then disconnecting
    # causes many BLE devices to pause advertising, which prevents the
    # coordinator from receiving the advertisement that triggers its first poll.
    # Login/auth is handled inside the coordinator's _ensure_connected().
    if async_ble_device_from_address(hass, address, connectable=True) is None:
        raise ConfigEntryNotReady(
            f"QuietCool {address} is not currently in BLE range. "
            "Ensure the fan is powered on and within Bluetooth range of HA."
        )

    coordinator = QuietCoolBLECoordinator(
        hass=hass,
        logger=_LOGGER,
        address=address,
        phone_id=phone_id,
        fan_info=fan_info,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(coordinator.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a QuietCool BLE config entry."""
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
