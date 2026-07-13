"""Diagnostics support for QuietCool BLE.

Provides a one-click "Download diagnostics" dump from the device page so pairing,
auth, and protocol issues can be triaged without hand-copying debug logs. The
PhoneID (auth secret), serial, and BLE address are redacted.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_FAN_SERIAL, CONF_PHONE_ID, DOMAIN
from .coordinator import QuietCoolBLECoordinator

# Redacted at any depth: auth secret + device identifiers.
TO_REDACT = {CONF_PHONE_ID, CONF_FAN_SERIAL, "serial", "address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]

    version = coordinator.fan_version
    parameters = coordinator.fan_parameters
    state = coordinator.fan_state

    data: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "source": entry.source,
            "data": dict(entry.data),
        },
        "device": {
            "address": coordinator.address,
            "protocol": coordinator.fan_info.protocol,
            "model": coordinator.fan_info.model,
            "name": coordinator.fan_info.name,
            "serial": coordinator.fan_info.serial,
        },
        "version": dataclasses.asdict(version) if version else None,
        "parameters": dataclasses.asdict(parameters) if parameters else None,
        "state": dataclasses.asdict(state) if state else None,
    }
    return async_redact_data(data, TO_REDACT)
