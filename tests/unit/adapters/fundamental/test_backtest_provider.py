"""BacktestFundamentalProvider テスト"""

from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from autotrader.adapters.fundamental.backtest_provider import (
    BacktestFundamentalProvider,
)
from autotrader.adapters.fundamental.schemas import ImpactLevel


def make_csv(rows: list[dict]) -> Path:
    """テスト用CSVファイルを生成して一時パスを返す"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv",
        delete=False, encoding="utf-8"
    ) as f:
        fieldnames = [
            "event_id", "event_time", "currency",
            "event_name", "impact", "actual", "forecast", "previous",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return Path(f.name)


class TestBacktestFundamentalProvider:
    """BacktestFundamentalProviderのテスト"""

    @pytest.fixture
    def provider(self):
        return BacktestFundamentalProvider(event_guard_minutes=30)

    @pytest.fixture
    def sample_csv(self):
        """サンプルCSVファイル"""
        base_time = datetime(2026, 2, 21, 14, 30, tzinfo=timezone.utc)
        rows = [
            {
                "event_id": "mt5_001",
                "event_time": base_time.isoformat(),
                "currency": "USD",
                "event_name": "Non-Farm Payroll",
                "impact": "high",
                "actual": "256000",
                "forecast": "180000",
                "previous": "185000",
            },
            {
                "event_id": "mt5_002",
                "event_time": (
                    base_time + timedelta(hours=2)
                ).isoformat(),
                "currency": "EUR",
                "event_name": "ECB Rate Decision",
                "impact": "medium",
                "actual": "",
                "forecast": "4.5",
                "previous": "4.5",
            },
        ]
        path = make_csv(rows)
        yield path
        path.unlink(missing_ok=True)

    def test_load_csv_success(self, provider, sample_csv):
        """CSVを正常に読み込める"""
        count = provider.load_csv(sample_csv)
        assert count == 2

    def test_load_csv_not_found(self, provider):
        """存在しないCSVは0件を返す"""
        count = provider.load_csv("/nonexistent/path.csv")
        assert count == 0

    def test_get_context_high_impact_soon(self, provider, sample_csv):
        """30分以内の高インパクト指標を検出"""
        provider.load_csv(sample_csv)
        # NFPの15分前
        now = datetime(2026, 2, 21, 14, 15, tzinfo=timezone.utc)
        ctx = provider.get_context(now, "USDJPY")
        assert ctx.has_high_impact_within_30min is True

    def test_get_context_no_high_impact_far_event(
        self, provider, sample_csv
    ):
        """60分後のイベントはガード対象外"""
        provider.load_csv(sample_csv)
        # NFPの60分前
        now = datetime(2026, 2, 21, 13, 30, tzinfo=timezone.utc)
        ctx = provider.get_context(now, "USDJPY")
        assert ctx.has_high_impact_within_30min is False

    def test_get_context_empty_provider(self, provider):
        """CSVなしはニュートラルコンテキスト"""
        now = datetime.now(timezone.utc)
        ctx = provider.get_context(now, "USDJPY")
        assert ctx.has_high_impact_within_30min is False
        assert ctx.macro_bias_score == 0.0

    def test_get_context_symbol_filter(self, provider, sample_csv):
        """USDJPYではUSD・JPYイベントのみ対象"""
        provider.load_csv(sample_csv)
        now = datetime(2026, 2, 21, 14, 15, tzinfo=timezone.utc)
        # EURGBP（EURとGBP）では上記USD/EURイベントはどちらも関係しない可能性
        ctx_usdjpy = provider.get_context(now, "USDJPY")
        assert ctx_usdjpy.has_high_impact_within_30min is True

    def test_load_csv_invalid_rows_skipped(self, provider):
        """不正な行はスキップされる"""
        rows = [
            {
                "event_id": "ok_001",
                "event_time": datetime.now(timezone.utc).isoformat(),
                "currency": "USD",
                "event_name": "NFP",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
            },
            {
                "event_id": "bad_001",
                "event_time": "INVALID_DATE",  # 不正な日付
                "currency": "USD",
                "event_name": "Bad Event",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
            },
        ]
        path = make_csv(rows)
        try:
            count = provider.load_csv(path)
            assert count == 1  # 正常な1件のみ
        finally:
            path.unlink(missing_ok=True)
