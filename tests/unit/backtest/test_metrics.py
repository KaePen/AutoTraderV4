"""メトリクス計算のユニットテスト"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from autotrader.backtest.metrics import (
    MetricsCalculator,
    BacktestMetrics,
)
from autotrader.core.entities import Trade
from autotrader.core.enums import SignalType, ExitReason


class TestBacktestMetrics:
    """BacktestMetrics のテスト"""

    def test_default_values(self) -> None:
        """デフォルト値の確認"""
        metrics = BacktestMetrics()
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0
        assert metrics.profit_factor == 0.0

    def test_to_dict(self) -> None:
        """辞書変換のテスト"""
        metrics = BacktestMetrics(
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            win_rate=0.6,
            net_profit=50000.0,
        )

        result = metrics.to_dict()

        assert result["total_trades"] == 10
        assert result["win_rate"] == 0.6
        assert result["net_profit"] == 50000.0


class TestMetricsCalculator:
    """MetricsCalculator のテスト"""

    @pytest.fixture
    def calculator(self) -> MetricsCalculator:
        """計算機を作成"""
        return MetricsCalculator(initial_balance=1_000_000.0)

    @pytest.fixture
    def sample_trades(self) -> list[Trade]:
        """サンプルトレードリスト"""
        base_time = datetime(2023, 6, 1, 10, 0)

        return [
            # 勝ちトレード1: +10,000円
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=140.00,
                exit_price=141.00,
                profit_loss=10000.0,
                profit_loss_pips=100.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time,
                closed_at=base_time + timedelta(hours=1),
            ),
            # 負けトレード1: -5,000円
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=141.00,
                exit_price=140.50,
                profit_loss=-5000.0,
                profit_loss_pips=-50.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=2),
                closed_at=base_time + timedelta(hours=3),
            ),
            # 勝ちトレード2: +8,000円
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.SELL,
                volume=0.1,
                entry_price=140.50,
                exit_price=139.70,
                profit_loss=8000.0,
                profit_loss_pips=80.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time + timedelta(hours=4),
                closed_at=base_time + timedelta(hours=5),
            ),
            # 負けトレード2: -3,000円
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.SELL,
                volume=0.1,
                entry_price=139.70,
                exit_price=140.00,
                profit_loss=-3000.0,
                profit_loss_pips=-30.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=6),
                closed_at=base_time + timedelta(hours=7),
            ),
        ]

    def test_calculate_empty_trades(
        self, calculator: MetricsCalculator
    ) -> None:
        """空のトレードリスト"""
        metrics = calculator.calculate([])
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0.0

    def test_calculate_basic_stats(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """基本統計の計算"""
        metrics = calculator.calculate(sample_trades)

        assert metrics.total_trades == 4
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 2
        assert metrics.win_rate == 0.5

    def test_calculate_profit_loss(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """損益計算"""
        metrics = calculator.calculate(sample_trades)

        # 総利益: 10,000 + 8,000 = 18,000
        assert metrics.total_profit == 18000.0
        # 総損失: 5,000 + 3,000 = 8,000
        assert metrics.total_loss == 8000.0
        # 純利益: 18,000 - 8,000 = 10,000
        assert metrics.net_profit == 10000.0

    def test_calculate_profit_factor(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """プロフィットファクター"""
        metrics = calculator.calculate(sample_trades)

        # PF = 18,000 / 8,000 = 2.25
        assert metrics.profit_factor == pytest.approx(2.25, abs=0.01)

    def test_calculate_averages(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """平均値計算"""
        metrics = calculator.calculate(sample_trades)

        # 平均利益: 18,000 / 2 = 9,000
        assert metrics.avg_win == pytest.approx(9000.0, abs=1.0)
        # 平均損失: 8,000 / 2 = 4,000
        assert metrics.avg_loss == pytest.approx(4000.0, abs=1.0)
        # 平均損益/トレード: 10,000 / 4 = 2,500
        assert metrics.avg_profit_per_trade == pytest.approx(2500.0, abs=1.0)

    def test_calculate_risk_reward(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """リスクリワードレシオ"""
        metrics = calculator.calculate(sample_trades)

        # RR = 9,000 / 4,000 = 2.25
        assert metrics.risk_reward_ratio == pytest.approx(2.25, abs=0.01)

    def test_calculate_expectancy(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """期待値計算"""
        metrics = calculator.calculate(sample_trades)

        # 期待値 = 0.5 * 9,000 - 0.5 * 4,000 = 2,500
        assert metrics.expectancy == pytest.approx(2500.0, abs=1.0)

    def test_calculate_consecutive(
        self, calculator: MetricsCalculator
    ) -> None:
        """連勝・連敗計算"""
        base_time = datetime(2023, 6, 1, 10, 0)

        trades = [
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=140.00,
                exit_price=141.00,
                profit_loss=1000.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time,
                closed_at=base_time + timedelta(hours=1),
            ),
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=141.00,
                exit_price=142.00,
                profit_loss=1000.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time + timedelta(hours=2),
                closed_at=base_time + timedelta(hours=3),
            ),
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=142.00,
                exit_price=143.00,
                profit_loss=1000.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time + timedelta(hours=4),
                closed_at=base_time + timedelta(hours=5),
            ),
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=143.00,
                exit_price=142.00,
                profit_loss=-1000.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=6),
                closed_at=base_time + timedelta(hours=7),
            ),
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=142.00,
                exit_price=141.00,
                profit_loss=-1000.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=8),
                closed_at=base_time + timedelta(hours=9),
            ),
        ]

        metrics = calculator.calculate(trades)

        assert metrics.max_consecutive_wins == 3
        assert metrics.max_consecutive_losses == 2

    def test_calculate_avg_duration(
        self,
        calculator: MetricsCalculator,
        sample_trades: list[Trade],
    ) -> None:
        """平均保有時間計算"""
        metrics = calculator.calculate(sample_trades)

        # 全て1時間 = 60分
        assert metrics.avg_trade_duration == pytest.approx(60.0, abs=1.0)

    def test_calculate_drawdown(
        self, calculator: MetricsCalculator
    ) -> None:
        """ドローダウン計算"""
        base_time = datetime(2023, 6, 1, 10, 0)

        trades = [
            # +50,000 (1,050,000)
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=140.00,
                exit_price=145.00,
                profit_loss=50000.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time,
                closed_at=base_time + timedelta(hours=1),
            ),
            # -30,000 (1,020,000) -> DD = 30,000
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=145.00,
                exit_price=142.00,
                profit_loss=-30000.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=2),
                closed_at=base_time + timedelta(hours=3),
            ),
            # -20,000 (1,000,000) -> DD = 50,000
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=142.00,
                exit_price=140.00,
                profit_loss=-20000.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=4),
                closed_at=base_time + timedelta(hours=5),
            ),
        ]

        metrics = calculator.calculate(trades)

        # 最大DD: ピーク1,050,000から1,000,000まで = 50,000
        assert metrics.max_drawdown == pytest.approx(50000.0, abs=100.0)
        # 最大DD%: 50,000 / 1,050,000 ≈ 4.76%
        assert metrics.max_drawdown_pct == pytest.approx(0.0476, abs=0.01)

    def test_calculate_recovery_factor(
        self, calculator: MetricsCalculator
    ) -> None:
        """リカバリーファクター計算"""
        base_time = datetime(2023, 6, 1, 10, 0)

        trades = [
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=140.00,
                exit_price=145.00,
                profit_loss=50000.0,
                exit_reason=ExitReason.TAKE_PROFIT,
                opened_at=base_time,
                closed_at=base_time + timedelta(hours=1),
            ),
            Trade(
                trade_id=str(uuid4()),
                symbol="USDJPY",
                signal_type=SignalType.BUY,
                volume=0.1,
                entry_price=145.00,
                exit_price=143.00,
                profit_loss=-20000.0,
                exit_reason=ExitReason.STOP_LOSS,
                opened_at=base_time + timedelta(hours=2),
                closed_at=base_time + timedelta(hours=3),
            ),
        ]

        metrics = calculator.calculate(trades)

        # 純利益: 30,000, 最大DD: 20,000
        # リカバリーファクター = 30,000 / 20,000 = 1.5
        assert metrics.recovery_factor == pytest.approx(1.5, abs=0.1)


class TestSharpeRatio:
    """シャープレシオ計算のテスト"""

    @pytest.fixture
    def calculator(self) -> MetricsCalculator:
        """計算機"""
        return MetricsCalculator(
            initial_balance=1_000_000.0,
            risk_free_rate=0.02,  # 2%
        )

    def test_sharpe_with_equity_history(
        self, calculator: MetricsCalculator
    ) -> None:
        """エクイティ履歴からシャープレシオ計算"""
        equity_history = {
            "2023-06-01": 1_000_000.0,
            "2023-06-02": 1_010_000.0,  # +1%
            "2023-06-03": 1_005_000.0,  # -0.5%
            "2023-06-04": 1_020_000.0,  # +1.5%
            "2023-06-05": 1_015_000.0,  # -0.5%
            "2023-06-06": 1_030_000.0,  # +1.5%
        }

        metrics = calculator.calculate([], equity_history)

        assert metrics.daily_returns is not None
        # 6日分のデータから6つのリターンが計算される
        # (initial -> day1, day1 -> day2, ..., day5 -> day6)
        assert len(metrics.daily_returns) == 6
        assert metrics.sharpe_ratio is not None

    def test_sharpe_no_history(
        self, calculator: MetricsCalculator
    ) -> None:
        """エクイティ履歴なしの場合"""
        metrics = calculator.calculate([])
        assert metrics.sharpe_ratio is None


class TestSummaryReport:
    """サマリーレポート生成のテスト"""

    def test_generate_summary(self) -> None:
        """サマリー生成"""
        calculator = MetricsCalculator()
        metrics = BacktestMetrics(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=0.6,
            profit_factor=2.0,
            total_profit=1_000_000.0,
            total_loss=500_000.0,
            net_profit=500_000.0,
            max_drawdown=100_000.0,
            max_drawdown_pct=0.1,
            sharpe_ratio=1.5,
        )

        summary = calculator.generate_summary(metrics)

        assert "バックテスト結果サマリー" in summary
        assert "総トレード数: 100" in summary
        assert "勝率: 60.00%" in summary
        assert "プロフィットファクター: 2.00" in summary
        assert "シャープレシオ: 1.50" in summary
