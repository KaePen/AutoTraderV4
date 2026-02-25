"""Phase 2a: BacktestFundamentalProvider テスト

イベントLLM CSV読み込み、時間減衰、複数イベント合成、
休日セマンティック分離のテスト。
"""

from __future__ import annotations

import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autotrader.adapters.fundamental.backtest_provider import (
    BacktestFundamentalProvider,
    EventLLMRecord,
    compute_influence,
)


# ==============================================
# 時間減衰モデルのテスト
# ==============================================


class TestComputeInfluence:
    """compute_influence() のテスト"""

    def test_zero_elapsed(self) -> None:
        """経過0時間→影響1.0"""
        assert compute_influence(0.0, 24.0) == pytest.approx(
            1.0
        )

    def test_at_convergence(self) -> None:
        """convergence_hours 到達→影響0.0"""
        assert compute_influence(24.0, 24.0) == 0.0

    def test_beyond_convergence(self) -> None:
        """convergence_hours 超過→影響0.0"""
        assert compute_influence(30.0, 24.0) == 0.0

    def test_negative_elapsed(self) -> None:
        """負の経過（未来イベント）→影響0.0"""
        assert compute_influence(-1.0, 24.0) == 0.0

    def test_zero_convergence(self) -> None:
        """convergence_hours=0→影響0.0"""
        assert compute_influence(1.0, 0.0) == 0.0

    def test_half_convergence(self) -> None:
        """半分経過時の減衰値"""
        infl = compute_influence(12.0, 24.0)
        expected = math.exp(-2.0 * 0.5)
        assert infl == pytest.approx(expected)

    def test_custom_decay_coefficient(self) -> None:
        """カスタム減衰係数"""
        infl = compute_influence(12.0, 24.0, decay_coefficient=1.0)
        expected = math.exp(-1.0 * 0.5)
        assert infl == pytest.approx(expected)


# ==============================================
# EventLLMRecord CSVパースのテスト
# ==============================================


def _make_event_llm_csv(
    rows: list[dict],
) -> Path:
    """テスト用CSVファイルを生成"""
    import csv

    headers = [
        "event_time", "currency", "event_name", "impact",
        "actual", "forecast", "previous",
        "surprise_score", "direction_bias",
        "convergence_hours", "expected_volatility",
        "trade_caution_level", "summary",
    ]
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False,
        encoding="utf-8", newline="",
    )
    writer = csv.DictWriter(tmp, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    tmp.close()
    return Path(tmp.name)


class TestLoadEventLLMCsv:
    """load_event_llm_csv() のテスト"""

    def test_load_basic(self) -> None:
        """基本的なCSV読み込み"""
        csv_path = _make_event_llm_csv([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "Non-Farm Payrolls",
                "impact": "high",
                "actual": "256000",
                "forecast": "180000",
                "previous": "185000",
                "surprise_score": "0.8",
                "direction_bias": "0.7",
                "convergence_hours": "24.0",
                "expected_volatility": "1.5",
                "trade_caution_level": "2",
                "summary": "NFPサプライズ",
            },
        ])
        provider = BacktestFundamentalProvider()
        count = provider.load_event_llm_csv(csv_path, "USDJPY")
        assert count == 1

        records = provider._event_llm_records["USDJPY"]
        assert len(records) == 1
        rec = records[0]
        assert rec.currency == "USD"
        assert rec.event_name == "Non-Farm Payrolls"
        assert rec.impact == "high"
        assert rec.surprise_score == pytest.approx(0.8)
        assert rec.trade_caution_level == 2
        assert rec.is_holiday is False

    def test_holiday_detection(self) -> None:
        """休日イベントの自動判定"""
        csv_path = _make_event_llm_csv([
            {
                "event_time": "2024-07-04T00:00:00+00:00",
                "currency": "USD",
                "event_name": "Independence Day - Bank Holiday",
                "impact": "low",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.0",
                "direction_bias": "0.0",
                "convergence_hours": "24.0",
                "expected_volatility": "0.2",
                "trade_caution_level": "2",
                "summary": "米国独立記念日",
            },
        ])
        provider = BacktestFundamentalProvider()
        provider.load_event_llm_csv(csv_path, "USDJPY")

        rec = provider._event_llm_records["USDJPY"][0]
        assert rec.is_holiday is True

    def test_value_clipping(self) -> None:
        """異常値のクリッピング"""
        csv_path = _make_event_llm_csv([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "Test",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "5.0",
                "direction_bias": "-3.0",
                "convergence_hours": "100.0",
                "expected_volatility": "10.0",
                "trade_caution_level": "5",
                "summary": "test",
            },
        ])
        provider = BacktestFundamentalProvider()
        provider.load_event_llm_csv(csv_path, "USDJPY")

        rec = provider._event_llm_records["USDJPY"][0]
        assert rec.surprise_score == 1.0
        assert rec.direction_bias == -1.0
        assert rec.convergence_hours == 72.0
        assert rec.expected_volatility == 2.0
        assert rec.trade_caution_level == 2

    def test_missing_file(self) -> None:
        """存在しないファイル→0件"""
        provider = BacktestFundamentalProvider()
        count = provider.load_event_llm_csv(
            "/nonexistent.csv", "USDJPY"
        )
        assert count == 0

    def test_multiple_csv_merge(self) -> None:
        """複数CSVのマージとソート"""
        csv1 = _make_event_llm_csv([
            {
                "event_time": "2024-03-01T10:00:00+00:00",
                "currency": "USD", "event_name": "ISM",
                "impact": "medium",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.3",
                "direction_bias": "0.2",
                "convergence_hours": "8.0",
                "expected_volatility": "1.2",
                "trade_caution_level": "1",
                "summary": "ISM",
            },
        ])
        csv2 = _make_event_llm_csv([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD", "event_name": "NFP",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.8",
                "direction_bias": "0.7",
                "convergence_hours": "24.0",
                "expected_volatility": "1.5",
                "trade_caution_level": "2",
                "summary": "NFP",
            },
        ])
        provider = BacktestFundamentalProvider()
        provider.load_event_llm_csv(csv1, "USDJPY")
        provider.load_event_llm_csv(csv2, "USDJPY")

        records = provider._event_llm_records["USDJPY"]
        assert len(records) == 2
        # ソート順: NFP (1月) → ISM (3月)
        assert records[0].event_name == "NFP"
        assert records[1].event_name == "ISM"


# ==============================================
# get_context() 合成アルゴリズムのテスト
# ==============================================


class TestGetContextSynthesis:
    """get_context() イベントLLM合成のテスト"""

    @staticmethod
    def _provider_with_records(
        records: list[dict],
    ) -> BacktestFundamentalProvider:
        """テスト用プロバイダーを生成"""
        csv_path = _make_event_llm_csv(records)
        provider = BacktestFundamentalProvider()
        provider.load_event_llm_csv(csv_path, "USDJPY")
        return provider

    def test_single_high_impact_event(self) -> None:
        """単一HIGHイベント直後のコンテキスト"""
        provider = self._provider_with_records([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "Non-Farm Payrolls",
                "impact": "high",
                "actual": "256000",
                "forecast": "180000",
                "previous": "185000",
                "surprise_score": "0.8",
                "direction_bias": "0.7",
                "convergence_hours": "24.0",
                "expected_volatility": "1.5",
                "trade_caution_level": "2",
                "summary": "NFP",
            },
        ])
        # NFP発表1時間後
        now = datetime(
            2024, 1, 5, 14, 30, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        assert ctx.event_caution_level == 2
        assert ctx.is_holiday is False
        assert ctx.direction_bias == pytest.approx(0.7)
        assert ctx.surprise_score == pytest.approx(0.8)
        assert ctx.volatility_multiplier > 1.0
        assert ctx.active_event_count == 1
        assert ctx.convergence_progress < 1.0

    def test_fully_converged_event(self) -> None:
        """完全に収束したイベント→ニュートラル"""
        provider = self._provider_with_records([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "NFP",
                "impact": "high",
                "actual": "256000",
                "forecast": "180000",
                "previous": "185000",
                "surprise_score": "0.8",
                "direction_bias": "0.7",
                "convergence_hours": "24.0",
                "expected_volatility": "1.5",
                "trade_caution_level": "2",
                "summary": "NFP",
            },
        ])
        # 48時間後（24h convergence 超過）
        now = datetime(
            2024, 1, 7, 13, 30, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        assert ctx.event_caution_level == 0
        assert ctx.direction_bias == 0.0
        assert ctx.volatility_multiplier == 1.0
        assert ctx.active_event_count == 0
        assert ctx.convergence_progress == 1.0

    def test_holiday_event_liquidity(self) -> None:
        """休日イベントの流動性変換"""
        provider = self._provider_with_records([
            {
                "event_time": "2024-07-04T00:00:00+00:00",
                "currency": "USD",
                "event_name": "Independence Day - Bank Holiday",
                "impact": "low",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.0",
                "direction_bias": "0.0",
                "convergence_hours": "24.0",
                "expected_volatility": "0.2",
                "trade_caution_level": "2",
                "summary": "米国独立記念日",
            },
        ])
        # 休日当日の正午（12時間経過）
        now = datetime(
            2024, 7, 4, 12, 0, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        assert ctx.is_holiday is True
        assert ctx.liquidity_factor < 1.0
        # 通常のボラ倍率は休日由来では変わらない
        assert ctx.volatility_multiplier == 1.0
        assert ctx.event_caution_level == 2

    def test_multiple_events_direction_synthesis(self) -> None:
        """複数イベント: 方向性の重み付き合成"""
        provider = self._provider_with_records([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "NFP",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.8",
                "direction_bias": "0.6",
                "convergence_hours": "24.0",
                "expected_volatility": "1.5",
                "trade_caution_level": "2",
                "summary": "NFP strong",
            },
            {
                "event_time": "2024-01-05T15:00:00+00:00",
                "currency": "USD",
                "event_name": "ISM Manufacturing",
                "impact": "medium",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "-0.3",
                "direction_bias": "-0.2",
                "convergence_hours": "8.0",
                "expected_volatility": "1.2",
                "trade_caution_level": "1",
                "summary": "ISM weak",
            },
        ])
        # 両方影響中（NFP 2h後、ISM 30min後）
        now = datetime(
            2024, 1, 5, 15, 30, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        # HIGH(3.0重み) NFP が支配的なので方向は正
        assert ctx.direction_bias > 0.0
        assert ctx.active_event_count == 2
        # ボラは max なので NFP の値
        assert ctx.volatility_multiplier > 1.0
        # caution は max
        assert ctx.event_caution_level == 2

    def test_opposite_direction_events(self) -> None:
        """逆方向イベントの打ち消し"""
        provider = self._provider_with_records([
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "Event A",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.5",
                "direction_bias": "0.5",
                "convergence_hours": "24.0",
                "expected_volatility": "1.3",
                "trade_caution_level": "1",
                "summary": "A",
            },
            {
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "Event B",
                "impact": "high",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "-0.5",
                "direction_bias": "-0.5",
                "convergence_hours": "24.0",
                "expected_volatility": "1.3",
                "trade_caution_level": "1",
                "summary": "B",
            },
        ])
        now = datetime(
            2024, 1, 5, 14, 0, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        # 同じインパクト・同時刻→方向は打ち消し
        assert abs(ctx.direction_bias) < 0.01
        assert abs(ctx.surprise_score) < 0.01

    def test_fallback_without_event_llm(self) -> None:
        """イベントLLMなし→旧ロジックのフォールバック"""
        provider = BacktestFundamentalProvider()
        now = datetime(
            2024, 1, 5, 14, 0, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        # ニュートラルに近い値
        assert ctx.event_caution_level == 0
        assert ctx.liquidity_factor == 1.0
        assert ctx.volatility_multiplier == 1.0

    def test_mixed_holiday_and_normal(self) -> None:
        """休日+通常イベント混在"""
        provider = self._provider_with_records([
            {
                "event_time": "2024-07-04T00:00:00+00:00",
                "currency": "USD",
                "event_name": "Independence Day - Holiday",
                "impact": "low",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.0",
                "direction_bias": "0.0",
                "convergence_hours": "24.0",
                "expected_volatility": "0.2",
                "trade_caution_level": "2",
                "summary": "休日",
            },
            {
                "event_time": "2024-07-04T14:00:00+00:00",
                "currency": "USD",
                "event_name": "Factory Orders",
                "impact": "medium",
                "actual": "", "forecast": "", "previous": "",
                "surprise_score": "0.3",
                "direction_bias": "0.3",
                "convergence_hours": "4.0",
                "expected_volatility": "1.2",
                "trade_caution_level": "1",
                "summary": "受注",
            },
        ])
        now = datetime(
            2024, 7, 4, 15, 0, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        assert ctx.is_holiday is True
        assert ctx.liquidity_factor < 1.0
        # 通常イベントのボラも反映
        assert ctx.volatility_multiplier > 1.0
        assert ctx.active_event_count == 2


# ==============================================
# HardGuard ファンダメンタルチェックのテスト
# ==============================================


class TestHardGuardFundamental:
    """HardGuard.check_fundamental() のテスト"""

    def test_caution_level_blocks(self) -> None:
        """caution_level >= 2 でブロック"""
        from autotrader.constraint.hard_guard import (
            HardGuard,
            HardGuardReason,
        )
        from autotrader.adapters.fundamental.schemas import (
            FundamentalContext,
        )

        guard = HardGuard()
        ctx = FundamentalContext(event_caution_level=2)
        ok, reason, code = guard.check_fundamental(ctx)

        assert ok is False
        assert "超重要指標日" in reason
        assert code == HardGuardReason.FUNDAMENTAL_CAUTION

    def test_caution_level_1_passes(self) -> None:
        """caution_level=1 は通過"""
        from autotrader.constraint.hard_guard import HardGuard
        from autotrader.adapters.fundamental.schemas import (
            FundamentalContext,
        )

        guard = HardGuard()
        ctx = FundamentalContext(event_caution_level=1)
        ok, _, _ = guard.check_fundamental(ctx)
        assert ok is True

    def test_holiday_low_liquidity_blocks(self) -> None:
        """休日+低流動性でブロック"""
        from autotrader.constraint.hard_guard import (
            HardGuard,
            HardGuardReason,
        )
        from autotrader.adapters.fundamental.schemas import (
            FundamentalContext,
        )

        guard = HardGuard()
        ctx = FundamentalContext(
            is_holiday=True,
            liquidity_factor=0.2,
        )
        ok, reason, code = guard.check_fundamental(ctx)

        assert ok is False
        assert "休日低流動性" in reason
        assert code == HardGuardReason.LOW_LIQUIDITY_HOLIDAY

    def test_holiday_normal_liquidity_passes(self) -> None:
        """休日でも流動性が閾値以上なら通過"""
        from autotrader.constraint.hard_guard import HardGuard
        from autotrader.adapters.fundamental.schemas import (
            FundamentalContext,
        )

        guard = HardGuard()
        ctx = FundamentalContext(
            is_holiday=True,
            liquidity_factor=0.5,
        )
        ok, _, _ = guard.check_fundamental(ctx)
        assert ok is True

    def test_check_includes_fundamental(self) -> None:
        """check() にfundamental_ctx を渡すとチェック実行"""
        from autotrader.constraint.hard_guard import HardGuard
        from autotrader.adapters.fundamental.schemas import (
            FundamentalContext,
        )

        guard = HardGuard()
        ctx = FundamentalContext(event_caution_level=2)
        result = guard.check(
            context={},
            is_entry=True,
            fundamental_ctx=ctx,
        )
        assert result.is_allowed is False

    def test_check_without_fundamental_backward_compat(
        self,
    ) -> None:
        """check() にfundamental_ctx なしで後方互換"""
        from autotrader.constraint.hard_guard import HardGuard

        guard = HardGuard()
        # 証拠金等の基本チェックを通すcontextを渡す
        ctx = {"margin_ratio": 200.0}
        result = guard.check(context=ctx, is_entry=True)
        assert result.is_allowed is True


# ==============================================
# PositionSizer ファンダメンタル調整のテスト
# ==============================================


class TestPositionSizerFundamental:
    """PositionSizer のファンダメンタル調整テスト"""

    def test_low_liquidity_reduces_lot(self) -> None:
        """低流動性でロット減少"""
        from autotrader.core.enums import MarketRegime
        from autotrader.core.interfaces.position_sizing import (
            SizingContext,
        )
        from autotrader.decision.unified.position_sizer import (
            PositionSizer,
            PositionSizerConfig,
        )

        sizer = PositionSizer(
            PositionSizerConfig(symbol="USDJPY")
        )

        # 通常のロット
        ctx_normal = SizingContext(
            equity=1_000_000, sl_pips=30.0,
            confidence=0.7, regime=MarketRegime.TREND,
            consecutive_losses=0, current_dd_pct=0.0,
        )
        result_normal = sizer.calculate(ctx_normal)

        # 低流動性
        ctx_low_liq = SizingContext(
            equity=1_000_000, sl_pips=30.0,
            confidence=0.7, regime=MarketRegime.TREND,
            consecutive_losses=0, current_dd_pct=0.0,
            liquidity_factor=0.3,
        )
        result_low_liq = sizer.calculate(ctx_low_liq)

        assert result_low_liq.lot < result_normal.lot

    def test_high_volatility_reduces_lot(self) -> None:
        """高ボラティリティでロット減少"""
        from autotrader.core.enums import MarketRegime
        from autotrader.core.interfaces.position_sizing import (
            SizingContext,
        )
        from autotrader.decision.unified.position_sizer import (
            PositionSizer,
            PositionSizerConfig,
        )

        sizer = PositionSizer(
            PositionSizerConfig(symbol="USDJPY")
        )

        ctx_normal = SizingContext(
            equity=1_000_000, sl_pips=30.0,
            confidence=0.7, regime=MarketRegime.TREND,
            consecutive_losses=0, current_dd_pct=0.0,
        )
        result_normal = sizer.calculate(ctx_normal)

        ctx_high_vol = SizingContext(
            equity=1_000_000, sl_pips=30.0,
            confidence=0.7, regime=MarketRegime.TREND,
            consecutive_losses=0, current_dd_pct=0.0,
            volatility_multiplier=1.8,
        )
        result_high_vol = sizer.calculate(ctx_high_vol)

        assert result_high_vol.lot < result_normal.lot

    def test_default_values_no_change(self) -> None:
        """デフォルト値(1.0)では調整なし"""
        from autotrader.core.enums import MarketRegime
        from autotrader.core.interfaces.position_sizing import (
            SizingContext,
        )
        from autotrader.decision.unified.position_sizer import (
            PositionSizer,
            PositionSizerConfig,
        )

        sizer = PositionSizer(
            PositionSizerConfig(symbol="USDJPY")
        )

        ctx1 = SizingContext(
            equity=1_000_000, sl_pips=30.0,
            confidence=0.7, regime=MarketRegime.TREND,
            consecutive_losses=0, current_dd_pct=0.0,
        )
        ctx2 = SizingContext(
            equity=1_000_000, sl_pips=30.0,
            confidence=0.7, regime=MarketRegime.TREND,
            consecutive_losses=0, current_dd_pct=0.0,
            liquidity_factor=1.0,
            volatility_multiplier=1.0,
        )

        assert sizer.calculate(ctx1).lot == sizer.calculate(
            ctx2
        ).lot
