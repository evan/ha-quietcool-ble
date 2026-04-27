"""QuietCool BLE Home Assistant Integration.

Communicates with QuietCool attic fans over Bluetooth Low Energy using the
stock manufacturer firmware — no firmware modification required.

GetFanInfo is fetched once during setup and stored in ConfigEntry.data.
The coordinator only polls GetWorkState on each 10-second cycle.
"""
from __future__ import annotations

import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from . import api
from .api import FanInfo
from .const import (
    CONF_FAN_MODEL,
    CONF_FAN_NAME,
    CONF_FAN_SERIAL,
    CONF_PHONE_ID,
    DOMAIN,
    MAX_CONNECT_ATTEMPTS,
    PLATFORMS,
)
from .coordinator import QuietCoolBLECoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up QuietCool BLE from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    phone_id: str = entry.data[CONF_PHONE_ID]

    # Build FanInfo from stored config entry data (fetched once during config flow)
    fan_info = FanInfo(
        name=entry.data.get(CONF_FAN_NAME) or entry.title,
        model=entry.data.get(CONF_FAN_MODEL, ""),
        serial=entry.data.get(CONF_FAN_SERIAL, ""),
    )

    # Verify device is reachable before completing setup
    device = async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        raise ConfigEntryNotReady(
            f"QuietCool {address} is not currently in BLE range. "
            "Ensure the fan is powered on and within Bluetooth range of HA."
        )

    # Verify credentials by connecting briefly
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            address,
            max_attempts=MAX_CONNECT_ATTEMPTS,
        )
        try:
            authenticated = await api.login(client, phone_id)
            if not authenticated:
                # Re-trigger the reauth flow
                entry.async_start_reauth(hass)
                return False
        finally:
            await client.disconnect()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Could not connect to QuietCool {address}: {err}"
        ) from err

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
