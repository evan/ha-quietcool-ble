"""QuietCool BLE number entities — smart mode temperature and humidity thresholds.

These thresholds control TH (Thermostat+Humidity) smart mode:
  GetTemp_H  — fan activates (HIGH speed on 2-speed fans) above this
  GetTemp_M  — fan switches from LOW to HIGH speed above this (2-speed fans)
  GetTemp_L  — fan deactivates below this
  GetHum_H   — fan activates when humidity rises above this

All values are read from GetParameter and written via SetTempHumidity.
SetTempHumidity requires all six fields (H/M/L for temp, H/L/Range for hum);
unchanged fields are passed through from the current coordinator.fan_parameters.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import api
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            QuietCoolTempHighNumber(coordinator),
            QuietCoolTempMedNumber(coordinator),
            QuietCoolTempLowNumber(coordinator),
            QuietCoolHumHighNumber(coordinator),
        ]
    )


class _QuietCoolThresholdBase(
    CoordinatorEntity[QuietCoolBLECoordinator], NumberEntity
):
    """Base class for smart-mode threshold number entities."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: QuietCoolBLECoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"

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
        return self.coordinator.fan_parameters is not None

    def _write_thresholds(self, **overrides: Any) -> Any:
        """Build a coroutine that calls SetTempHumidity with all six fields.

        Callers pass only the field(s) they are changing; the rest come from
        the current fan_parameters so unchanged values are preserved.
        """
        params = self.coordinator.fan_parameters
        assert params is not None
        protocol = self.coordinator.fan_info.protocol
        merged = {
            "temp_h": params.temp_h,
            "temp_m": params.temp_m,
            "temp_l": params.temp_l,
            "hum_h": params.hum_h,
            "hum_l": params.hum_l,
            "hum_range": params.hum_range,
        }
        merged.update(overrides)
        return lambda client: api.set_temp_humidity(client, protocol=protocol, **merged)


class QuietCoolTempHighNumber(_QuietCoolThresholdBase):
    """High temperature threshold for TH smart mode (fan turns on above this)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 50
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_name = "High Temp Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temp_h")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.temp_h)

    async def async_set_native_value(self, value: float) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        new_val = int(value)
        await self.coordinator.async_execute(self._write_thresholds(temp_h=new_val))
        self.coordinator.fan_parameters = dataclasses.replace(params, temp_h=new_val)
        self.async_write_ha_state()


class QuietCoolTempMedNumber(_QuietCoolThresholdBase):
    """Medium temperature threshold for TH smart mode (2-speed: LOW→HIGH above this)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 50
    _attr_native_max_value = 110
    _attr_native_step = 1
    _attr_name = "Medium Temp Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temp_m")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.temp_m)

    async def async_set_native_value(self, value: float) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        new_val = int(value)
        await self.coordinator.async_execute(self._write_thresholds(temp_m=new_val))
        self.coordinator.fan_parameters = dataclasses.replace(params, temp_m=new_val)
        self.async_write_ha_state()


class QuietCoolTempLowNumber(_QuietCoolThresholdBase):
    """Low temperature threshold for TH smart mode (fan turns off below this)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 40
    _attr_native_max_value = 90
    _attr_native_step = 1
    _attr_name = "Low Temp Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temp_l")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.temp_l)

    async def async_set_native_value(self, value: float) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        new_val = int(value)
        await self.coordinator.async_execute(self._write_thresholds(temp_l=new_val))
        self.coordinator.fan_parameters = dataclasses.replace(params, temp_l=new_val)
        self.async_write_ha_state()


class QuietCoolHumHighNumber(_QuietCoolThresholdBase):
    """High humidity threshold for TH smart mode (fan turns on above this)."""

    _attr_device_class = NumberDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_name = "High Humidity Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "hum_h")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.hum_h)

    async def async_set_native_value(self, value: float) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        new_val = int(value)
        await self.coordinator.async_execute(self._write_thresholds(hum_h=new_val))
        self.coordinator.fan_parameters = dataclasses.replace(params, hum_h=new_val)
        self.async_write_ha_state()
