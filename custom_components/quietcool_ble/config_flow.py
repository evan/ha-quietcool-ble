"""Config flow for QuietCool BLE integration.

Flow steps:
  bluetooth    → auto-triggered when ATTICFAN* advertisement seen
  bluetooth_confirm → user confirms the device (confirm-only; PhoneID auto-generated)
  pair         → pair the fan, OR reuse an existing PhoneID to skip pairing
  user         → manual fallback when no auto-discovery is available
  reauth       → re-pair after the PhoneID stops being accepted
"""
from __future__ import annotations

import logging
import re
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import voluptuous as vol
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
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


# Phone IDs vary in format: 16 lowercase hex (OEM/ESPHome convention), UUIDs
# (the QuietCool app), and other alphanumeric strings (never-paired hubs ship
# with a factory id like "1234567dsad8wqw9asasd"). The controller accepts up to
# 100 bytes. Accept alphanumeric + dashes; a wrong-but-valid-looking id simply
# fails login. (Per the OEM BLE protocol docs, CrazyCoder/quietcool-esphome-native.)
_PHONE_ID_RE = re.compile(r"^[0-9a-zA-Z-]{8,100}$")


def _is_valid_phone_id(phone_id: str) -> bool:
    """True if the string looks like a plausible QuietCool PhoneID."""
    return bool(_PHONE_ID_RE.fullmatch(phone_id))


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
        """Pair the fan, or reuse an existing Phone ID to skip pairing.

        Leaving the Phone ID field blank pairs normally (generate a new ID and
        register it). Entering a known Phone ID — e.g. from a previous setup, an
        ESPHome config, or the QuietCool app — skips pairing and just verifies
        with a login, which is the reliable path on firmware 3.9+ where pairing
        a new ID can fail.
        """
        assert self._discovery_info is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            entered_id = (user_input.get(CONF_PHONE_ID) or "").strip()
            if entered_id:
                if not _is_valid_phone_id(entered_id):
                    errors["base"] = "invalid_phone_id"
                else:
                    # Commit the entered ID only on success — otherwise a later
                    # blank submit ("pair a new one") must still regenerate.
                    result = await self._verify_existing_id(entered_id)
                    if result == "success":
                        self._phone_id = entered_id
                        return self._create_entry()
                    errors["base"] = result  # "login_failed" / "cannot_connect"
            else:
                if not self._phone_id:
                    self._phone_id = _generate_phone_id()
                result = await self._attempt_pair()
                if result == "success":
                    return self._create_entry()
                errors["base"] = result  # "pair_failed" / "cannot_connect"

        return self.async_show_form(
            step_id="pair",
            errors=errors,
            data_schema=vol.Schema({vol.Optional(CONF_PHONE_ID): str}),
            description_placeholders={
                "name": self._discovery_info.name,
                "address": self._discovery_info.address,
            },
        )

    async def _verify_existing_id(self, phone_id: str) -> str:
        """Confirm a user-supplied Phone ID by logging in — no pairing.

        Takes the candidate ID as an argument (rather than self._phone_id) so the
        caller only commits it on success. Returns 'success', 'login_failed' (the
        ID isn't registered on the fan), or 'cannot_connect'.
        """
        assert self._discovery_info is not None
        device = self._discovery_info.device
        try:
            async with self._connect(device) as client:
                if not await api.login(client, phone_id):
                    return "login_failed"
                try:
                    self.context["fan_info"] = await api.get_fan_info(client)
                except Exception:
                    _LOGGER.warning(
                        "Could not fetch fan info after login; using defaults"
                    )
                return "success"
        except Exception:
            _LOGGER.exception(
                "Could not connect to verify Phone ID for %s", device.address
            )
            return "cannot_connect"

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
            # Bounded: an unbounded disconnect() on a wedged D-Bus would hang the
            # pairing dialog indefinitely (same hazard as issue #10).
            await api.safe_disconnect(client, device.address)

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

        # Whether any attempt got a definitive login result (True/False). If we
        # only ever hit connection errors, the fan was unreachable → cannot_connect;
        # if we reached a login but nothing persisted → pair_failed.
        reached_login = False

        for send_pair in (api.pair_v1, api.pair_v2):
            # Send the pairing command(s) on one connection. A dropped/reset
            # connection here is expected on some V2 firmware and is NOT fatal —
            # the fresh-connection login below is the real test of persistence.
            pair_result: str | None = None
            try:
                async with self._connect(device) as client:
                    pair_result = await send_pair(client, self._phone_id)
            except Exception:
                _LOGGER.debug(
                    "QuietCool %s: pairing connection ended early (may be an "
                    "expected V2 reset); verifying anyway",
                    device.address,
                )
            if pair_result == "Beyond":
                # The fan's PhoneID memory (50 max) is full — no pairing can
                # persist until it's factory-reset.
                return "memory_full"

            # Confirm persistence with a login on a fresh connection. A transient
            # connection error here is not fatal — fall through to the next method
            # rather than aborting, so a flaky V1 verify can't mask a working V2.
            try:
                async with self._connect(device) as client:
                    persisted = await api.login(client, self._phone_id)
                    reached_login = True
                    if not persisted:
                        continue  # not persisted — try the next method
                    try:
                        self.context["fan_info"] = await api.get_fan_info(client)
                    except Exception:
                        _LOGGER.warning(
                            "Could not fetch fan info after pairing; using defaults"
                        )
                    return "success"
            except Exception:
                _LOGGER.debug(
                    "QuietCool %s: verification connection failed; trying next "
                    "method",
                    device.address,
                )
                continue

        if not reached_login:
            return "cannot_connect"
        _LOGGER.warning(
            "Pairing not persisted for %s after V1 and V2 attempts — the fan "
            "acknowledged pairing but did not store the PhoneID. It may need to "
            "be paired from the QuietCool app.",
            device.address,
        )
        return "pair_failed"

    def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry after successful pairing.

        During reauth the entry already exists — update its PhoneID with the
        freshly paired one and reload, rather than creating a duplicate.
        """
        assert self._discovery_info is not None

        if self.source == SOURCE_REAUTH:
            entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
            assert entry is not None
            # Update the existing entry's PhoneID and reload. Done manually rather
            # than via async_update_reload_and_abort(), which does not exist on the
            # minimum supported HA version (2023.7) — this is what that helper does
            # internally in newer versions.
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_PHONE_ID: self._phone_id}
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(entry.entry_id),
                name=f"quietcool reauth reload {entry.entry_id}",
            )
            return self.async_abort(reason="reauth_successful")

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
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Re-pair after the fan stops accepting our PhoneID."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None

        address = entry.data[CONF_ADDRESS]
        for info in async_discovered_service_info(self.hass):
            if info.address == address:
                self._discovery_info = info
                break

        if self._discovery_info is None:
            return self.async_abort(reason="device_not_found")

        # Reuse the existing PhoneID rather than generating a new one. Controllers
        # store at most 50 PhoneIDs; re-registering the same id re-pairs without
        # consuming another slot, so repeated re-pairs can't fill the table. The
        # user can still type a different id on the pair form.
        self._phone_id = entry.data.get(CONF_PHONE_ID) or _generate_phone_id()
        return await self.async_step_pair()
