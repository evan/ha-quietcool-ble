"""QuietCool BLE coordinator — owns all BLE connections for one fan device.

Architecture: led_ble dual-lock pattern.
  _connect_lock   — serializes establish_connection calls
  _operation_lock — serializes GATT writes within an open connection

All BLE operations (poll AND entity commands) go through async_execute().
Fan entities NEVER open their own BleakClient connections.

Critical: _handle_disconnect does NOT call asyncio.all_tasks().cancel().
The upstream emerose/quietcool library does this; porting that line would
crash the entire HA event loop on every BLE disconnect.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from bleak import BleakClient
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import api
from .api import FanInfo, FanState
from .const import (
    KEEP_ALIVE_SECONDS,
    MAX_CONNECT_ATTEMPTS,
    POLL_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

_BleOperation: TypeAlias = Callable[[BleakClient], Awaitable[None]]


class QuietCoolBLECoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Manages all BLE communication for a single QuietCool fan device."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        address: str,
        phone_id: str,
        fan_info: FanInfo,
    ) -> None:
        super().__init__(
            hass,
            logger,
            address=address,
            mode=BluetoothScanningMode.ACTIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll,
            connectable=True,
        )
        self.phone_id = phone_id
        self.fan_info: FanInfo = fan_info
        self.fan_state: FanState | None = None

        # Dual-lock pattern (led_ble reference architecture)
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._expected_disconnect = False
        self._idle_timer_handle: asyncio.TimerHandle | None = None
        self._poll_task: asyncio.Task[Any] | None = None
        self._consecutive_failures = 0

    # ------------------------------------------------------------------
    # Poll scheduling
    # ------------------------------------------------------------------

    @callback
    def _needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Return True when it's time to connect and read device state."""
        return (
            self.hass.state == CoreState.running
            and (
                seconds_since_last_poll is None
                or seconds_since_last_poll > self._poll_interval()
            )
            and bool(
                bluetooth.async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        )

    def _poll_interval(self) -> float:
        """Return poll interval with exponential backoff after consecutive failures."""
        if self._consecutive_failures == 0:
            return POLL_INTERVAL_SECONDS
        backoff = POLL_INTERVAL_SECONDS * (2**self._consecutive_failures)
        return min(backoff, 300.0)  # cap at 5 minutes

    async def _async_poll(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Perform one poll cycle: connect, login, GetWorkState, disconnect (or keep-alive)."""
        self._poll_task = asyncio.current_task()
        try:
            await self.async_execute(self._poll_operation)
            self._consecutive_failures = 0
        except UpdateFailed:
            self._consecutive_failures += 1
            raise
        finally:
            self._poll_task = None

    async def _poll_operation(self, client: BleakClient) -> None:
        self.fan_state = await api.get_work_state(client, protocol=self.fan_info.protocol)

    # ------------------------------------------------------------------
    # Command routing — single entry point for ALL BLE operations
    # ------------------------------------------------------------------

    async def async_execute(self, operation: _BleOperation) -> None:
        """Execute a BLE operation under the connection and operation locks.

        This is the ONLY place that calls _ensure_connected. Fan entities
        must route all commands here; they must never open BleakClient
        connections directly.
        """
        try:
            client = await self._ensure_connected()
            async with self._operation_lock:
                await operation(client)
        except TimeoutError as err:
            raise UpdateFailed(str(err)) from err
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"BLE operation failed: {err}") from err

    # ------------------------------------------------------------------
    # Connection lifecycle — led_ble dual-lock pattern
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> BleakClientWithServiceCache:
        """Return the active BLE connection, opening one if needed.

        Uses double-check locking: fast path if already connected,
        slow path (acquire lock, reconnect) if disconnected.
        """
        # Fast path: already connected — reset idle timer and return
        if self._client is not None and self._client.is_connected:
            self._reset_idle_timer()
            return self._client

        async with self._connect_lock:
            # Double-check after acquiring lock
            if self._client is not None and self._client.is_connected:
                self._reset_idle_timer()
                return self._client

            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if device is None:
                raise UpdateFailed(
                    f"No connectable BLE adapter can reach {self.address}"
                )

            self._expected_disconnect = False
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    self.address,
                    disconnected_callback=self._handle_disconnect,
                    max_attempts=MAX_CONNECT_ATTEMPTS,
                )
            except Exception as err:
                raise UpdateFailed(
                    f"Could not connect to QuietCool {self.address}: {err}"
                ) from err

            # Authenticate immediately after connecting
            try:
                authenticated = await api.login(client, self.phone_id)
            except Exception as err:
                await client.disconnect()
                raise UpdateFailed(f"BLE login error: {err}") from err

            if not authenticated:
                await client.disconnect()
                raise UpdateFailed(
                    "QuietCool login rejected — PhoneID mismatch or device was "
                    "reset. Re-pairing required."
                )

            self._client = client
            self._reset_idle_timer()
            _LOGGER.debug("Connected to QuietCool %s", self.address)
            return client

    def _handle_disconnect(self, client: BleakClient) -> None:
        """Handle unexpected BLE disconnect.

        IMPORTANT: Does NOT cancel asyncio.all_tasks(). The upstream
        emerose/quietcool library does this, which would crash HA. We cancel
        only our own _poll_task.
        """
        if self._expected_disconnect:
            return
        _LOGGER.warning("QuietCool %s: unexpected BLE disconnect", self.address)
        self._client = None
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
            self._idle_timer_handle = None
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    def _reset_idle_timer(self) -> None:
        """Reset the idle-disconnect timer to KEEP_ALIVE_SECONDS from now."""
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
        self._idle_timer_handle = self.hass.loop.call_later(
            KEEP_ALIVE_SECONDS, self._schedule_idle_disconnect
        )

    def _schedule_idle_disconnect(self) -> None:
        self.hass.async_create_task(self._async_idle_disconnect())

    async def _async_idle_disconnect(self) -> None:
        """Voluntarily close an idle BLE connection to free the adapter slot."""
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                self._expected_disconnect = True
                await self._client.disconnect()
                self._client = None
                _LOGGER.debug(
                    "QuietCool %s: idle disconnect (%.0fs timeout)",
                    self.address,
                    KEEP_ALIVE_SECONDS,
                )

    async def async_stop(self) -> None:
        """Cancel in-flight poll and close any open BLE connection."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._idle_timer_handle:
            self._idle_timer_handle.cancel()
        await self._async_idle_disconnect()
        await super().async_stop()
