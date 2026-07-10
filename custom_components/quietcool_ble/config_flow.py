"""Config flow for QuietCool BLE integration.

Flow steps:
  bluetooth    → auto-triggered when ATTICFAN* advertisement seen
  bluetooth_confirm → user confirms the device (confirm-only; PhoneID auto-generated)
  pair         → user presses physical Pair button; BLE Pair command is sent
  user         → manual fallback when no auto-discovery is available
  reauth       → re-pair after PhoneID eviction (Android app conflict)
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from . import api
from .api import ProtocolVersion
from .const import (
    BLE_NAME_PREFIX,
    CONF_FAN_MODEL,
    CONF_FAN_NAME,
    CONF_FAN_SERIAL,
    CONF_PHONE_ID,
    CONF_PROTOCOL,
    DOMAIN,
    MAX_CONNECT_ATTEMPTS,
)

_LOGGER = logging.getLogger(__name__)


def _generate_phone_id() -> str:
    """Generate a cryptographically random 16-char hex PhoneID."""
    return secrets.token_hex(8)


class QuietCoolBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Bluetooth discovery and pairing for a QuietCool fan."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._phone_id: str = ""

    # ------------------------------------------------------------------
    # Auto-discovery path (triggered by manifest bluetooth matcher)
    # ------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Called automatically when an ATTICFAN* advertisement is seen."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name,
            "address": discovery_info.address,
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask user to confirm the discovered device before pairing."""
        assert self._discovery_info is not None

        if user_input is not None:
            self._phone_id = _generate_phone_id()
            return await self.async_step_pair()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery_info.name,
                "address": self._discovery_info.address,
            },
        )

    # ------------------------------------------------------------------
    # Pairing step (both auto-discovery and manual paths)
    # ------------------------------------------------------------------

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt user to press physical Pair button, then attempt BLE pairing."""
        assert self._discovery_info is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            result = await self._attempt_pair()
            if result == "success":
                return self._create_entry()
            errors["base"] = result  # "pair_failed" or "cannot_connect"

        return self.async_show_form(
            step_id="pair",
            errors=errors,
            description_placeholders={
                "name": self._discovery_info.name,
                "address": self._discovery_info.address,
            },
        )

    @asynccontextmanager
    async def _connect(
        self, device: BLEDevice
    ) -> AsyncIterator[BleakClientWithServiceCache]:
        """Open a fresh BLE connection and always disconnect on exit."""
        client = await establish_connection(
            BleakClientWithServiceCache,
            device,
            device.address,
            max_attempts=MAX_CONNECT_ATTEMPTS,
        )
        try:
            yield client
        finally:
            await client.disconnect()

    async def _attempt_pair(self) -> str:
        """Pair the device, confirming each attempt on a fresh connection.

        Returns 'success' or an error key. Tries the legacy V1 pair, then the V2
        sequence; each is confirmed by a login on a NEW connection — the exact
        path the coordinator uses on every poll. This means:
          * a pairing that is only accepted for the pairing session (but not
            persisted) is never mistaken for success, and
          * V2 is always attempted when V1 does not actually persist.
        """
        assert self._discovery_info is not None
        device = self._discovery_info.device

        for send_pair in (api.pair_v1, api.pair_v2):
            try:
                # Send the pairing command(s) on one connection...
                async with self._connect(device) as client:
                    await send_pair(client, self._phone_id)
                # ...then confirm persistence with a login on a fresh connection.
                async with self._connect(device) as client:
                    if not await api.login(client, self._phone_id):
                        continue  # not persisted — try the next method
                    try:
                        self.context["fan_info"] = await api.get_fan_info(client)
                    except Exception:
                        _LOGGER.warning(
                            "Could not fetch fan info after pairing; using defaults"
                        )
                    return "success"
            except Exception:
                _LOGGER.exception("Pairing attempt failed for %s", device.address)
                return "cannot_connect"

        _LOGGER.warning(
            "Pairing not persisted for %s after V1 and V2 attempts — the fan "
            "acknowledged pairing but did not store the PhoneID. It may need to "
            "be paired from the QuietCool app.",
            device.address,
        )
        return "pair_failed"

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry after successful pairing."""
        assert self._discovery_info is not None
        fan_info = self.context.get("fan_info")

        return self.async_create_entry(
            title=self._discovery_info.name,
            data={
                CONF_ADDRESS: self._discovery_info.address,
                CONF_PHONE_ID: self._phone_id,
                CONF_FAN_NAME: fan_info.name if fan_info else self._discovery_info.name,
                CONF_FAN_MODEL: fan_info.model if fan_info else "",
                CONF_FAN_SERIAL: fan_info.serial if fan_info else "",
                CONF_PROTOCOL: fan_info.protocol if fan_info else ProtocolVersion.V1,
            },
        )

    # ------------------------------------------------------------------
    # Manual setup fallback (user adds integration without auto-discovery)
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup: show list of discovered ATTICFAN* devices."""
        discovered_devices = {
            info.address: f"{info.name} ({info.address})"
            for info in async_discovered_service_info(self.hass)
            if info.name and info.name.startswith(BLE_NAME_PREFIX)
        }

        if not discovered_devices:
            return self.async_abort(reason="no_devices_found")

        import voluptuous as vol

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            # Find the matching discovery info
            for info in async_discovered_service_info(self.hass):
                if info.address == address:
                    await self.async_set_unique_id(address)
                    self._abort_if_unique_id_configured()
                    self._discovery_info = info
                    self._phone_id = _generate_phone_id()
                    return await self.async_step_pair()
            return self.async_abort(reason="device_not_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(discovered_devices)}
            ),
        )

    # ------------------------------------------------------------------
    # Re-authentication flow (PhoneID eviction recovery)
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Re-pair after PhoneID was overwritten by another client."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        address = entry.data[CONF_ADDRESS]
        for info in async_discovered_service_info(self.hass):
            if info.address == address:
                self._discovery_info = info
                break

        if self._discovery_info is None:
            return self.async_abort(reason="device_not_found")

        self._phone_id = _generate_phone_id()
        return await self.async_step_pair()
