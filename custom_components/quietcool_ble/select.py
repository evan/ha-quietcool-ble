"""QuietCool BLE select entity — operating mode selector.

Modes:
  Idle  — fan off
  Timer — fan runs for a set duration (uses stored parameter defaults)
  TH    — Thermostat+Humidity smart mode; auto-runs based on temp/humidity thresholds
"""
from __future__ import annotations

import dataclasses
import logging

from homeassistant.components.select import SelectEntity
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

# All known operating modes.  The device may return others; we pass them through.
FAN_MODES = [FanMode.IDLE, FanMode.TIMER, FanMode.TH]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([QuietCoolModeSelectEntity(coordinator)])


class QuietCoolModeSelectEntity(
    CoordinatorEntity[QuietCoolBLECoordinator], SelectEntity
):
    """Select entity for choosing the fan's operating mode."""

    _attr_has_entity_name = True
    _attr_name = "Mode"
    _attr_options = FAN_MODES

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_mode"

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
    def current_option(self) -> str | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.mode

    async def async_select_option(self, option: str) -> None:
        protocol = self.coordinator.fan_info.protocol

        if option == FanMode.IDLE:
            await self.coordinator.async_execute(
                lambda client: api.set_mode_idle(client, protocol=protocol)
            )
        elif option == FanMode.TH:
            await self.coordinator.async_execute(
                lambda client: api.set_mode_th(client, protocol=protocol)
            )
        elif option == FanMode.TIMER:
            # Use device's stored timer defaults; fall back to 8h LOW if not yet fetched
            params = self.coordinator.fan_parameters
            hours = params.timer_hour if params else 8
            minutes = params.timer_minute if params else 0
            speed = params.timer_range if params else FanSpeed.LOW
            await self.coordinator.async_execute(
                lambda client: api.set_mode_timer(
                    client, speed, hours, minutes, protocol=protocol
                )
            )
        else:
            _LOGGER.warning("QuietCool: unknown mode selected: %r", option)
            return

        # Optimistic update so UI reflects the change immediately
        if self.coordinator.fan_state is not None:
            self.coordinator.fan_state = dataclasses.replace(
                self.coordinator.fan_state, mode=option
            )
        self.async_write_ha_state()
