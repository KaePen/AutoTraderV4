"""UnifiedTradeBotのユニットテスト"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from autotrader.core.enums import SignalType
from autotrader.decision.unified import (
    BotState,
    RiskManager,
    UnifiedTradeBot,
)
from autotrader.decision.unified.config import RiskConfig, UnifiedBotConfig


class TestBotState:
    """BotStateのテスト"""

    def test_initial_state(self) -> None:
        """初期状態テスト"""
        state = BotState()
        assert state.equity == 1_000_000.0
        assert state.consecutive_losses == 0
        assert state.consecutive_wins == 0
        assert state.current_dd_pct == 0.0

    def test_update_pnl_profit(self) -> None:
        """利益更新テスト"""
        state = BotState()
        state.update_pnl(10000.0)
        assert state.equity == 1_010_000.0
        assert state.consecutive_wins == 1
        assert state.consecutive_losses == 0
        assert state.peak_equity == 1_010_000.0

    def test_update_pnl_loss(self) -> None:
        """損失更新テスト"""
        state = BotState()
        state.update_pnl(-10000.0)
        assert state.equity == 990_000.0
        assert state.consecutive_wins == 0
        assert state.consecutive_losses == 1
        assert state.current_dd_pct > 0

    def test_update_pnl_multiple(self) -> None:
        """連続損益更新テスト"""
        state = BotState()
        state.update_pnl(5000.0)
        state.update_pnl(5000.0)
        assert state.consecutive_wins == 2
        state.update_pnl(-3000.0)
        assert state.consecutive_wins == 0
        assert state.consecutive_losses == 1

    def test_reset_daily(self) -> None:
        """日次リセットテスト"""
        state = BotState()
        state.daily_pnl = 5000.0
        state.daily_trades = 10
        state.reset_daily()
        assert state.daily_pnl == 0.0
        assert state.daily_trades == 0

    def test_drawdown_calculation(self) -> None:
        """ドローダウン計算テスト"""
        state = BotState()
        state.update_pnl(100000.0)  # peak = 1.1M
        state.update_pnl(-50000.0)  # equity = 1.05M
        # DD = (1.1M - 1.05M) / 1.1M ≈ 0.045
        assert state.current_dd_pct > 0
        assert state.current_dd_pct < 0.1


class TestRiskManager:
    """RiskManagerのテスト"""

    def test_initial_state(self) -> None:
        """初期状態テスト"""
        rm = RiskManager()
        assert rm._daily_pnl == 0.0
        assert rm._daily_trades == 0

    def test_can_trade_normal(self) -> None:
        """通常状態でトレード可能"""
        rm = RiskManager()
        can_trade, reason = rm.can_trade(datetime.now())
        assert can_trade is True
        assert reason == ""

    def test_can_trade_daily_loss_limit(self) -> None:
        """日次損失制限テスト"""
        config = RiskConfig(max_daily_loss_pct=0.02)
        rm = RiskManager(config)
        rm.update_pnl(-0.03)  # 3%損失
        can_trade, reason = rm.can_trade(datetime.now())
        assert can_trade is False
        assert "日次損失" in reason

    def test_can_trade_cooldown(self) -> None:
        """クールダウンテスト"""
        config = RiskConfig(cooldown_minutes=5)
        rm = RiskManager(config)
        now = datetime.now()
        rm.record_trade(now)
        can_trade, reason = rm.can_trade(now)
        assert can_trade is False
        assert "クールダウン" in reason

    def test_reset_daily(self) -> None:
        """日次リセットテスト"""
        rm = RiskManager()
        rm.update_pnl(-0.01)
        rm._daily_trades = 5
        rm.reset_daily(datetime.now())
        assert rm._daily_pnl == 0.0
        assert rm._daily_trades == 0

    def test_record_trade(self) -> None:
        """トレード記録テスト"""
        rm = RiskManager()
        now = datetime.now()
        rm.record_trade(now)
        assert rm._last_trade_time == now
        assert rm._daily_trades == 1


class TestUnifiedTradeBot:
    """UnifiedTradeBotのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.bot = UnifiedTradeBot()

    def test_init(self) -> None:
        """初期化テスト"""
        assert self.bot.config is not None
        assert len(self.bot.evaluators) > 0
        assert self.bot.state is not None

    def test_init_with_config(self) -> None:
        """カスタム設定での初期化"""
        config = UnifiedBotConfig(
            timeframes=["M5", "M15", "H1"],
        )
        bot = UnifiedTradeBot(config)
        assert len(bot.evaluators) == 3

    def test_set_market_data(self) -> None:
        """市場データ設定テスト"""
        data = {
            "M5": pd.DataFrame({
                "time": pd.date_range("2023-01-01", periods=10, freq="5min"),
                "close": [150.0] * 10,
            }),
            "M15": pd.DataFrame({
                "time": pd.date_range("2023-01-01", periods=10, freq="15min"),
                "close": [150.0] * 10,
            }),
        }
        self.bot.set_market_data(data)
        assert len(self.bot._market_data) == 2

    def test_generate_signal_hold(self) -> None:
        """シグナル生成テスト（HOLD）"""
        # 市場データなしではHOLD
        current_time = pd.Timestamp("2023-01-01 10:00:00")
        signal = self.bot.generate_signal(current_time)
        assert signal.direction == SignalType.HOLD

    def test_on_trade_executed(self) -> None:
        """トレード実行コールバックテスト"""
        now = datetime.now()
        self.bot.on_trade_executed(now, pnl=1000.0)
        assert self.bot.state.equity == 1_001_000.0

    def test_on_trade_executed_no_pnl(self) -> None:
        """トレード実行（PnLなし）"""
        now = datetime.now()
        self.bot.on_trade_executed(now)
        assert self.bot.state.equity == 1_000_000.0

    def test_default_timeframes(self) -> None:
        """デフォルト時間足テスト"""
        assert "M5" in self.bot.timeframes
        assert "M15" in self.bot.timeframes

    def test_get_higher_timeframes(self) -> None:
        """上位時間足取得テスト"""
        higher = self.bot._get_higher_timeframes("M5")
        assert "M1" not in higher
        assert "H1" in higher or "M15" in higher

    def test_hold_signal(self) -> None:
        """HOLDシグナル生成テスト"""
        signal = self.bot._hold_signal("テスト理由")
        assert signal.direction == SignalType.HOLD
        assert "テスト理由" in signal.rationale


class TestUnifiedBotConfig:
    """UnifiedBotConfigのテスト"""

    def test_default_config(self) -> None:
        """デフォルト設定テスト"""
        config = UnifiedBotConfig()
        assert config.enable_position_sizing is True

    def test_custom_timeframes(self) -> None:
        """カスタム時間足設定"""
        config = UnifiedBotConfig(
            timeframes=["M1", "M5"],
        )
        assert config.timeframes == ["M1", "M5"]

    def test_get_evaluator_config(self) -> None:
        """評価器設定取得"""
        config = UnifiedBotConfig()
        eval_config = config.get_evaluator_config("M5")
        assert eval_config.timeframe == "M5"


