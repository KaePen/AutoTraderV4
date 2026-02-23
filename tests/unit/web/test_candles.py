"""ローソク足APIテスト"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient


class TestCandlesEndpoint:
    """GET /api/v1/candles/{symbol}/{timeframe} テスト"""

    def test_get_candles_mt5_connected(
        self, client, mock_engine
    ):
        """MT5接続時にライブデータを返す"""
        df = pd.DataFrame(
            {
                "time": [
                    datetime(
                        2026, 1, 1, 0, 0, tzinfo=timezone.utc
                    ),
                    datetime(
                        2026, 1, 1, 0, 15, tzinfo=timezone.utc
                    ),
                ],
                "open": [150.0, 150.5],
                "high": [150.5, 151.0],
                "low": [149.5, 150.0],
                "close": [150.5, 150.8],
                "volume": [100, 200],
            }
        )
        mock_engine.get_candles = AsyncMock(return_value=df)
        resp = client.get("/api/v1/candles/USDJPY/M15?limit=2")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["open"] == 150.0

    def test_get_candles_with_end_time_mt5(
        self, client, mock_engine
    ):
        """end_time指定時にget_candles_beforeが呼ばれる"""
        df = pd.DataFrame(
            {
                "time": [
                    datetime(
                        2025, 12, 31, 23, 45,
                        tzinfo=timezone.utc,
                    ),
                ],
                "open": [149.0],
                "high": [149.5],
                "low": [148.5],
                "close": [149.2],
                "volume": [50],
            }
        )
        mock_engine.get_candles_before = AsyncMock(
            return_value=df
        )
        end_time = "2026-01-01T00:00:00Z"
        resp = client.get(
            f"/api/v1/candles/USDJPY/M15"
            f"?limit=500&end_time={end_time}"
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        # get_candles_before が呼ばれたことを確認
        mock_engine.get_candles_before.assert_called_once()

    def test_get_candles_invalid_end_time(
        self, client, mock_engine
    ):
        """不正なend_time形式で400エラー"""
        resp = client.get(
            "/api/v1/candles/USDJPY/M15"
            "?end_time=invalid-date"
        )
        assert resp.status_code == 400

    def test_get_candles_end_time_without_tz(
        self, client, mock_engine
    ):
        """タイムゾーンなしend_timeでもUTC扱いで動作"""
        df = pd.DataFrame(
            {
                "time": [
                    datetime(
                        2025, 12, 31, 23, 0,
                        tzinfo=timezone.utc,
                    ),
                ],
                "open": [149.0],
                "high": [149.5],
                "low": [148.5],
                "close": [149.2],
                "volume": [50],
            }
        )
        mock_engine.get_candles_before = AsyncMock(
            return_value=df
        )
        # タイムゾーンなしISO8601
        resp = client.get(
            "/api/v1/candles/USDJPY/M15"
            "?end_time=2026-01-01T00:00:00"
        )
        assert resp.status_code == 200

    def test_get_candles_no_end_time(
        self, client, mock_engine
    ):
        """end_timeなしで通常のget_candlesが呼ばれる"""
        df = pd.DataFrame(
            {
                "time": [
                    datetime(
                        2026, 1, 1, 0, 0, tzinfo=timezone.utc
                    ),
                ],
                "open": [150.0],
                "high": [150.5],
                "low": [149.5],
                "close": [150.5],
                "volume": [100],
            }
        )
        mock_engine.get_candles = AsyncMock(return_value=df)
        resp = client.get("/api/v1/candles/USDJPY/M15")
        assert resp.status_code == 200
        mock_engine.get_candles.assert_called_once()

    def test_get_candles_csv_fallback_with_end_time(
        self, no_engine_client
    ):
        """MT5未接続時のCSVフォールバック（end_time付）"""
        with patch(
            "autotrader.web.services.market_service"
            ".MarketService.get_candles",
            return_value=[],
        ) as mock_csv:
            resp = no_engine_client.get(
                "/api/v1/candles/USDJPY/M15"
                "?end_time=2026-01-01T00:00:00Z"
            )
            assert resp.status_code == 200
            assert resp.json()["data"] == []
            # end_time が渡されたことを確認
            call_kwargs = mock_csv.call_args
            assert call_kwargs.kwargs.get("end_time") is not None


class TestCandleServiceEndTime:
    """CandleService の end_time フィルタテスト"""

    def test_end_time_exclusive_filter(self):
        """end_timeが排他的（<）フィルタであることを確認"""
        from autotrader.web.services.candle_service import (
            CandleService,
        )

        service = CandleService(data_dir="nonexistent")

        # _load_candle_data をモックして直接テスト
        import polars as pl

        mock_df = pl.DataFrame(
            {
                "time": [
                    datetime(
                        2026, 1, 1, 0, 0, tzinfo=timezone.utc
                    ),
                    datetime(
                        2026, 1, 1, 0, 15, tzinfo=timezone.utc
                    ),
                    datetime(
                        2026, 1, 1, 0, 30, tzinfo=timezone.utc
                    ),
                ],
                "open": [150.0, 150.5, 151.0],
                "high": [150.5, 151.0, 151.5],
                "low": [149.5, 150.0, 150.5],
                "close": [150.5, 150.8, 151.2],
            }
        )

        with patch.object(
            service, "_load_candle_data", return_value=mock_df
        ):
            # end_time = 0:15 → 0:00のみ返るはず（0:15は含まない）
            result = service.get_candles(
                "USDJPY",
                "M15",
                limit=100,
                end_time=datetime(
                    2026, 1, 1, 0, 15, tzinfo=timezone.utc
                ),
            )
            assert len(result) == 1
            assert result[0].open == 150.0

    def test_no_end_time_returns_latest(self):
        """end_timeなしで最新limit件を返す"""
        from autotrader.web.services.candle_service import (
            CandleService,
        )

        service = CandleService(data_dir="nonexistent")

        import polars as pl

        from datetime import timedelta

        base = datetime(
            2026, 1, 1, 0, 0, tzinfo=timezone.utc
        )
        mock_df = pl.DataFrame(
            {
                "time": [
                    base + timedelta(minutes=15 * i)
                    for i in range(5)
                ],
                "open": [150.0 + i for i in range(5)],
                "high": [150.5 + i for i in range(5)],
                "low": [149.5 + i for i in range(5)],
                "close": [150.3 + i for i in range(5)],
            }
        )

        with patch.object(
            service, "_load_candle_data", return_value=mock_df
        ):
            result = service.get_candles(
                "USDJPY", "M15", limit=3
            )
            # 最新3件（後ろから）
            assert len(result) == 3
            assert result[0].open == 152.0
