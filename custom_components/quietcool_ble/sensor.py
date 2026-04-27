"""QuietCool BLE sensor entities — temperature and humidity.

Temperature formula (confirmed from emerose/quietcool source):
  temperature_fahrenheit = Temp_Sample / 10
  e.g. Temp_Sample 1071 → 107.1°F (a normal hot attic in summer)
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            QuietCoolTemperatureSensor(coordinator),
            QuietCoolHumiditySensor(coordinator),
        ]
    )


class _QuietCoolSensorBase(CoordinatorEntity[QuietCoolBLECoordinator], SensorEntity):
    """Base class for QuietCool sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: QuietCoolBLECoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.fan_state is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.fan_info.name,
            manufacturer="QuietCool",
            model=self.coordinator.fan_info.model or None,
        )


class QuietCoolTemperatureSensor(_QuietCoolSensorBase):
    """Attic temperature sensor. Value = Temp_Sample / 10 in °F."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Temperature"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temperature")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.temp_fahrenheit


class QuietCoolHumiditySensor(_QuietCoolSensorBase):
    """Attic humidity sensor. Value = Humidity_Sample / 10 in %."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Humidity"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "humidity")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.humidity_percent
