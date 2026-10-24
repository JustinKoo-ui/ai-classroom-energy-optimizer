from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import HTTPException, Request

from app.api.v1.routers.environment_analysis import (
    read_current_occupancy,
    read_environment_analysis,
    read_latest_environment_telemetry,
)
from app.repositories.environment import LatestEnvironmentReading
from app.repositories.occupancy import CurrentOccupancy
from app.services.environment_analysis import build_environment_analysis


class EnvironmentAnalysisTests(unittest.TestCase):
    def test_insufficient_readings_are_reported_without_claiming_a_trend(self) -> None:
        analyzed_at = datetime.now(UTC)
        result = build_environment_analysis(
            classroom_id=uuid4(),
            reading_count=2,
            analyzed_at=analyzed_at,
            latest_observed_at=analyzed_at,
            temperature=(26.0, 26.0, 26.0, 26.0, 25.9),
            humidity=(55.0, 55.0, 55.0, 55.0, 54.9),
            illuminance=(300.0, 300.0, 300.0, 300.0, 299.0),
        )

        self.assertEqual(result.analysis_status, "insufficient_data")
        self.assertEqual(result.temperature_c.trend, "rising")
        self.assertEqual(result.insights[0].code, "insufficient_readings")

    def test_high_average_temperature_produces_advisory_only(self) -> None:
        analyzed_at = datetime.now(UTC)
        result = build_environment_analysis(
            classroom_id=uuid4(),
            reading_count=10,
            analyzed_at=analyzed_at,
            latest_observed_at=analyzed_at - timedelta(seconds=5),
            temperature=(27.0, 27.2, 26.8, 27.5, 26.9),
            humidity=(55.0, 55.0, 54.0, 56.0, 55.0),
            illuminance=(300.0, 300.0, 280.0, 320.0, 301.0),
        )

        self.assertEqual(result.analysis_status, "ready")
        self.assertEqual(result.freshness, "fresh")
        self.assertEqual(result.insights[0].code, "temperature_above_target_band")
        self.assertNotIn("command", result.model_dump())

    def test_oldest_reading_is_marked_stale(self) -> None:
        analyzed_at = datetime.now(UTC)
        result = build_environment_analysis(
            classroom_id=uuid4(),
            reading_count=5,
            analyzed_at=analyzed_at,
            latest_observed_at=analyzed_at - timedelta(seconds=31),
            temperature=(26.0, 26.0, 25.8, 26.2, 25.9),
            humidity=(55.0, 55.0, 54.0, 56.0, 54.9),
            illuminance=(300.0, 300.0, 280.0, 320.0, 301.0),
        )

        self.assertEqual(result.freshness, "stale")

    def test_raw_adc_readings_do_not_trigger_lux_threshold_claims(self) -> None:
        analyzed_at = datetime.now(UTC)
        result = build_environment_analysis(
            classroom_id=uuid4(),
            reading_count=5,
            analyzed_at=analyzed_at,
            latest_observed_at=analyzed_at,
            temperature=(26.0, 26.0, 25.8, 26.2, 25.9),
            humidity=(55.0, 55.0, 54.0, 56.0, 54.9),
            illuminance=(None, None, None, None, None),
        )

        codes = {insight.code for insight in result.insights}
        self.assertIn("illuminance_not_calibrated", codes)
        self.assertNotIn("illuminance_below_target", codes)
        self.assertNotIn("no_threshold_findings", codes)


class EnvironmentAnalysisRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_loopback_client_is_rejected(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": (
                    "/api/v1/classrooms/00000000-0000-0000-0000-000000000001/environment-analysis"
                ),
                "headers": [],
                "client": ("203.0.113.1", 1234),
                "app": SimpleNamespace(state=SimpleNamespace()),
            }
        )

        with self.assertRaises(HTTPException) as raised:
            await read_environment_analysis(uuid4(), request)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_latest_telemetry_returns_raw_adc_without_claiming_lux(self) -> None:
        observed_at = datetime.now(UTC)

        class FakeSession:
            async def __aenter__(self) -> FakeSession:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

        classroom_id = uuid4()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/api/v1/classrooms/{classroom_id}/telemetry/latest",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "app": SimpleNamespace(state=SimpleNamespace(sessions=FakeSession)),
            }
        )
        reading = LatestEnvironmentReading(
            classroom_exists=True,
            temperature_c=26.4,
            relative_humidity_pct=66.0,
            illuminance_lux=None,
            illuminance_adc=2048,
            motion_detected=True,
            observed_at=observed_at,
            sample_interval_ms=5000,
        )

        with patch(
            "app.api.v1.routers.environment_analysis.get_latest_environment_reading",
            new=AsyncMock(return_value=reading),
        ):
            result = await read_latest_environment_telemetry(classroom_id, request)

        self.assertEqual(result.freshness, "fresh")
        self.assertEqual(result.illuminance_adc, 2048)
        self.assertIsNone(result.illuminance_lux)
        self.assertTrue(result.motion_detected)

    async def test_current_occupancy_returns_fresh_count(self) -> None:
        classroom_id = uuid4()
        observed_at = datetime.now(UTC)
        request = self._local_request(classroom_id)
        reading = CurrentOccupancy(True, 2, 0.94, observed_at, "occupied")

        with patch(
            "app.api.v1.routers.environment_analysis.get_current_occupancy",
            new=AsyncMock(return_value=reading),
        ):
            result = await read_current_occupancy(classroom_id, request)

        self.assertEqual(result.status.value, "occupied")
        self.assertEqual(result.count, 2)
        self.assertEqual(result.freshness.value, "fresh")

    async def test_fresh_zero_is_possibly_vacant_not_confirmed(self) -> None:
        classroom_id = uuid4()
        request = self._local_request(classroom_id)
        reading = CurrentOccupancy(
            True,
            0,
            0.91,
            datetime.now(UTC),
            "possibly_vacant",
        )

        with patch(
            "app.api.v1.routers.environment_analysis.get_current_occupancy",
            new=AsyncMock(return_value=reading),
        ):
            result = await read_current_occupancy(classroom_id, request)

        self.assertEqual(result.status.value, "possibly_vacant")
        self.assertEqual(result.count, 0)

    async def test_stale_occupancy_hides_count_and_confidence(self) -> None:
        classroom_id = uuid4()
        observed_at = datetime.now(UTC) - timedelta(seconds=16)
        request = self._local_request(classroom_id)
        reading = CurrentOccupancy(True, 2, 0.94, observed_at, "occupied")

        with patch(
            "app.api.v1.routers.environment_analysis.get_current_occupancy",
            new=AsyncMock(return_value=reading),
        ):
            result = await read_current_occupancy(classroom_id, request)

        self.assertEqual(result.status.value, "unknown")
        self.assertIsNone(result.count)
        self.assertIsNone(result.confidence)
        self.assertEqual(result.observed_at, observed_at)
        self.assertEqual(result.freshness.value, "stale")

    async def test_missing_occupancy_is_unknown_not_zero(self) -> None:
        classroom_id = uuid4()
        request = self._local_request(classroom_id)
        reading = CurrentOccupancy(True, None, None, None, None)

        with patch(
            "app.api.v1.routers.environment_analysis.get_current_occupancy",
            new=AsyncMock(return_value=reading),
        ):
            result = await read_current_occupancy(classroom_id, request)

        self.assertEqual(result.status.value, "unknown")
        self.assertIsNone(result.count)
        self.assertEqual(result.freshness.value, "unknown")

    async def test_unknown_classroom_returns_not_found(self) -> None:
        classroom_id = uuid4()
        request = self._local_request(classroom_id)
        reading = CurrentOccupancy(False, None, None, None, None)

        with patch(
            "app.api.v1.routers.environment_analysis.get_current_occupancy",
            new=AsyncMock(return_value=reading),
        ), self.assertRaises(HTTPException) as raised:
            await read_current_occupancy(classroom_id, request)

        self.assertEqual(raised.exception.status_code, 404)

    @staticmethod
    def _local_request(classroom_id: UUID) -> Request:
        class FakeSession:
            async def __aenter__(self) -> FakeSession:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/api/v1/classrooms/{classroom_id}/occupancy/current",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "app": SimpleNamespace(state=SimpleNamespace(sessions=FakeSession)),
            }
        )

    async def test_local_client_receives_database_analysis(self) -> None:
        analyzed_at = datetime.now(UTC)
        row = {
            "classroom_exists": True,
            "reading_count": 3,
            "latest_observed_at": analyzed_at,
            "temperature_latest": 27.0,
            "temperature_average": 27.0,
            "temperature_minimum": 26.0,
            "temperature_maximum": 28.0,
            "temperature_previous": 26.8,
            "humidity_latest": 60.0,
            "humidity_average": 60.0,
            "humidity_minimum": 59.0,
            "humidity_maximum": 61.0,
            "humidity_previous": 60.0,
            "illuminance_latest": 250.0,
            "illuminance_average": 250.0,
            "illuminance_minimum": 240.0,
            "illuminance_maximum": 260.0,
            "illuminance_previous": 250.0,
        }

        class FakeResult:
            def __init__(self, result_row: dict[str, object]) -> None:
                self.result_row = result_row

            def mappings(self) -> FakeResult:
                return self

            def one(self) -> dict[str, object]:
                return self.result_row

        class FakeSession:
            async def __aenter__(self) -> FakeSession:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(self, *_args: object) -> FakeResult:
                return FakeResult(row)

        classroom_id = uuid4()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/api/v1/classrooms/{classroom_id}/environment-analysis",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "app": SimpleNamespace(state=SimpleNamespace(sessions=FakeSession)),
            }
        )

        result = await read_environment_analysis(classroom_id, request)

        self.assertEqual(result.classroom_id, classroom_id)
        self.assertEqual(result.reading_count, 3)
        self.assertEqual(result.insights[0].code, "temperature_above_target_band")
