"""QuietCool BLE fan entity.

Commands route through coordinator.async_execute() — never open BleakClient directly.
Optimistic state updates give immediate UI feedback without waiting for the next poll.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import api
from .api import FanMode, FanSpeed
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator

_LOGGER = logging.getLogger(__name__)

PRESET_LOW = "Low"
PRESET_MEDIUM = "Medium"
PRESET_HIGH = "High"

_PRESET_TO_BLE: dict[str, str] = {
    PRESET_LOW: FanSpeed.LOW,
    PRESET_MEDIUM: FanSpeed.MEDIUM,
    PRESET_HIGH: FanSpeed.HIGH,
}

# FanType tokens (reported by the firmware) known to expose a third, medium speed.
# Deliberately an allowlist, not a denylist: any fan that does NOT explicitly
# report one of these — 2-speed fans ("TWO"), unknown/garbage values, and older
# firmware that omits FanType (parameters None) — falls through to Low/High only
# and can never be shown or sent MEDIUM.
_THREE_SPEED_FAN_TYPES: frozenset[str] = frozenset({"THREE"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([QuietCoolFanEntity(coordinator)])


class QuietCoolFanEntity(CoordinatorEntity[QuietCoolBLECoordinator], FanEntity):
    """Represents a QuietCool BLE-controlled attic fan."""

    _attr_has_entity_name = True
    _attr_name = None  # Primary entity; device name is the entity name
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.address

    @property
    def device_info(self) -> DeviceInfo:
        version = self.coordinator.fan_version
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.fan_info.name,
            manufacturer="QuietCool",
            model=self.coordinator.fan_info.model or None,
            sw_version=version.firmware if version else None,
            hw_version=version.hw_version if version else None,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.fan_state is not None

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.mode != FanMode.IDLE

    @property
    def preset_modes(self) -> list[str]:
        """Speed presets this fan exposes.

        Medium is offered only when the firmware explicitly reports a known
        3-speed FanType. A missing or unknown FanType — including failed
        parameter reads (fan_parameters is None) and older firmware that omits
        the field — falls back to Low/High, so a 2-speed fan can never be shown
        or sent MEDIUM.
        """
        params = self.coordinator.fan_parameters
        if params is not None and params.fan_type in _THREE_SPEED_FAN_TYPES:
            return [PRESET_LOW, PRESET_MEDIUM, PRESET_HIGH]
        return [PRESET_LOW, PRESET_HIGH]

    @property
    def preset_mode(self) -> str | None:
        if self.coordinator.fan_state is None:
            return None
        speed = self.coordinator.fan_state.range
        if speed == FanSpeed.HIGH:
            return PRESET_HIGH
        if speed == FanSpeed.MEDIUM:
            return PRESET_MEDIUM
        if speed == FanSpeed.LOW:
            return PRESET_LOW
        return None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        speed = _PRESET_TO_BLE.get(preset_mode or PRESET_LOW, FanSpeed.LOW)
        protocol = self.coordinator.fan_info.protocol
        await self.coordinator.async_execute(
            lambda client: api.set_mode_timer(client, speed, protocol=protocol)
        )
        # Optimistic update — show new state immediately without waiting for next poll
        if self.coordinator.fan_state is not None:
            self.coordinator.fan_state = dataclasses.replace(
                self.coordinator.fan_state, mode=FanMode.TIMER, range=speed
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        protocol = self.coordinator.fan_info.protocol
        await self.coordinator.async_execute(
            lambda client: api.set_mode_idle(client, protocol=protocol)
        )
        if self.coordinator.fan_state is not None:
            self.coordinator.fan_state = dataclasses.replace(
                self.coordinator.fan_state, mode=FanMode.IDLE, range=None
            )
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set fan speed preset (Low / High)."""
        if self.is_on:
            await self.async_turn_on(preset_mode=preset_mode)
        # If fan is off, just update the pending preset without turning on

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.coordinator.fan_state is None:
            return None
        attrs: dict[str, Any] = {
            "ble_mode": self.coordinator.fan_state.mode,
            "ble_range": self.coordinator.fan_state.range,
        }
        if self.coordinator.fan_state.sensor_state is not None:
            attrs["sensor_state"] = self.coordinator.fan_state.sensor_state
        # Diagnostic: surfaces the firmware-reported speed-count token (e.g. "TWO"/"THREE")
        # so 3-speed support can be confirmed in the field. Drives the Medium preset gate.
        if self.coordinator.fan_parameters is not None:
            attrs["fan_type"] = self.coordinator.fan_parameters.fan_type
        return attrs
