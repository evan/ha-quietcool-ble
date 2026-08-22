"""Regression tests for saving and activating QuietCool Timer settings.

set_timer() must save the duration/speed WITHOUT starting the fan (SetTime only,
no SetMode), while set_mode_timer() must send SetTime *and* SetMode:Timer.
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch


COMPONENT = Path(__file__).parents[1] / "custom_components" / "quietcool_ble"
custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(COMPONENT.parent)]
quietcool_ble = types.ModuleType("custom_components.quietcool_ble")
quietcool_ble.__path__ = [str(COMPONENT)]
sys.modules.setdefault("custom_components", custom_components)
sys.modules.setdefault("custom_components.quietcool_ble", quietcool_ble)

# api.py imports bleak at module load; stub it so tests run without the dep.
bleak = types.ModuleType("bleak")
bleak.BleakClient = object
sys.modules.setdefault("bleak", bleak)

api = importlib.import_module("custom_components.quietcool_ble.api")


class SetTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_timer_v1_saves_without_starting_fan(self) -> None:
        client = object()
        send = AsyncMock(return_value={"Result": "Success"})
        with patch.object(api, "_send_command", send):
            await api.set_timer(
                client, api.FanSpeed.HIGH, 6, 30, protocol=api.ProtocolVersion.V1
            )
        send.assert_awaited_once_with(
            client,
            {
                "Api": "SetTime",
                "SetHour": 6,
                "SetMinute": 30,
                "SetTime_Range": api.FanSpeed.HIGH,
            },
        )

    async def test_set_timer_v2_saves_without_starting_fan(self) -> None:
        client = object()
        send = AsyncMock(return_value={"A": 7, "F": "TRUE"})
        with patch.object(api, "_send_command", send):
            await api.set_timer(
                client, api.FanSpeed.LOW, 3, 0, protocol=api.ProtocolVersion.V2
            )
        send.assert_awaited_once_with(
            client,
            {"A": api.ApiCode.SET_TIME, "H": 3, "M": 0, "R": api.FanSpeed.LOW},
        )

    async def test_set_timer_rejects_invalid_speed(self) -> None:
        with patch.object(api, "_send_command", AsyncMock()) as send:
            with self.assertRaises(ValueError):
                await api.set_timer(object(), "TURBO", 1, 0)
        send.assert_not_awaited()

    async def test_set_timer_rejects_out_of_range_duration(self) -> None:
        with patch.object(api, "_send_command", AsyncMock()) as send:
            with self.assertRaises(ValueError):
                await api.set_timer(object(), api.FanSpeed.LOW, 24, 0)
        send.assert_not_awaited()


class SetModeTimerTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_mode_timer_v1_saves_then_starts(self) -> None:
        client = object()
        send = AsyncMock(return_value={"Result": "Success"})
        with patch.object(api, "_send_command", send):
            await api.set_mode_timer(
                client, api.FanSpeed.HIGH, 8, 0, protocol=api.ProtocolVersion.V1
            )
        self.assertEqual(
            send.await_args_list,
            [
                call(
                    client,
                    {
                        "Api": "SetTime",
                        "SetHour": 8,
                        "SetMinute": 0,
                        "SetTime_Range": api.FanSpeed.HIGH,
                    },
                ),
                call(client, {"Api": "SetMode", "Mode": api.FanMode.TIMER}),
            ],
        )

    async def test_set_mode_timer_v2_saves_then_starts(self) -> None:
        client = object()
        send = AsyncMock(return_value={"A": 9})
        with patch.object(api, "_send_command", send):
            await api.set_mode_timer(
                client, api.FanSpeed.LOW, 2, 15, protocol=api.ProtocolVersion.V2
            )
        self.assertEqual(
            send.await_args_list,
            [
                call(
                    client,
                    {"A": api.ApiCode.SET_TIME, "H": 2, "M": 15, "R": api.FanSpeed.LOW},
                ),
                call(client, {"A": api.ApiCode.SET_MODE, "M": api.FanMode.TIMER}),
            ],
        )


def _params(*, timer_hour: int, timer_minute: int, timer_range: str = "LOW"):
    """Build a FanParameters with the timer fields under test."""
    return api.FanParameters(
        temp_h=85,
        temp_m=75,
        temp_l=65,
        hum_h=90,
        hum_l=255,
        hum_range="LOW",
        fan_type="TWO",
        timer_hour=timer_hour,
        timer_minute=timer_minute,
        timer_range=timer_range,
    )


class ResolveTimerDurationTests(unittest.TestCase):
    def test_none_falls_back_to_default(self) -> None:
        self.assertEqual(api.resolve_timer_duration(None), (8, 0))

    def test_stored_duration_is_used(self) -> None:
        params = _params(timer_hour=3, timer_minute=30)
        self.assertEqual(api.resolve_timer_duration(params), (3, 30))

    def test_zero_duration_falls_back_to_default(self) -> None:
        # A 0h0m stored timer would be a no-op turn-on; fall back to 8h instead.
        params = _params(timer_hour=0, timer_minute=0)
        self.assertEqual(api.resolve_timer_duration(params), (8, 0))

    def test_partial_zero_is_honored(self) -> None:
        self.assertEqual(
            api.resolve_timer_duration(_params(timer_hour=0, timer_minute=30)),
            (0, 30),
        )
        self.assertEqual(
            api.resolve_timer_duration(_params(timer_hour=2, timer_minute=0)),
            (2, 0),
        )


if __name__ == "__main__":
    unittest.main()
