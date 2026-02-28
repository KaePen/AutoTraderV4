"""UnifiedTradeBotのユニットテスト"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from autotrader.calculator.features.regime_detector import (
    RegimeResult,
)
from autotrader.constraint.soft_guard import SoftGuardResult
from autotrader.core.enums import (
    MarketRegime,
    SignalType,
    TradingStrategyMode,
)
from autotrader.decision.unified import (
    BotState,
    RiskManager,
    UnifiedTradeBot,
)
from autotrader.decision.unified.config import RiskConfig, UnifiedBotConfig
from autotrader.decision.unified.mode_aware_consensus import (
    ConsensusResult,
)
from autotrader.decision.unified.mode_selector import TradingPlan


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


# --- RANGEフィルタ統合テスト用ヘルパー ---

def _make_regime(
    regime: MarketRegime = MarketRegime.RANGE,
    trend_strength: float = 0.1,
    volatility_level: float = 0.5,
) -> RegimeResult:
    """テスト用RegimeResult生成"""
    return RegimeResult(
        regime=regime,
        trend_strength=trend_strength,
        volatility_level=volatility_level,
        adx=25.0,
        confidence=0.8,
        reasoning="test",
    )


def _make_consensus(
    score: float = 9.0,
    threshold: float = 8.0,
) -> ConsensusResult:
    """テスト用ConsensusResult生成"""
    return ConsensusResult(
        direction=SignalType.BUY,
        score=score,
        threshold=threshold,
        aligned_tfs=["M15"],
        reasoning="test",
    )


def _make_sg(
    penalty: float = 0.0,
) -> SoftGuardResult:
    """テスト用SoftGuardResult生成"""
    return SoftGuardResult(total_penalty=penalty)


def _make_plan() -> TradingPlan:
    """テスト用TradingPlan生成"""
    return TradingPlan(
        mode=TradingStrategyMode.UNIVERSAL,
        primary_tf="M15",
        entry_tf="M5",
        confirm_tfs=["H1"],
        manage_tf="M15",
        max_holding_bars=32,
        tp_sl_ratio_range=(1.1, 1.4),
    )


class TestRangeFilterConsolidated:
    """統合RANGEフィルタのテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.config = UnifiedBotConfig(
            range_filter_consolidated=True,
            range_filter_block_threshold=0.6,
            range_day_bbw_threshold=0.25,
            range_day_score_premium=0.3,
            weak_hours_enabled=True,
            weak_hours_score_premium=0.5,
        )
        self.bot = UnifiedTradeBot(self.config)
        self.plan = _make_plan()
        self.time = pd.Timestamp("2023-06-15 10:00:00")

    def test_trend_regime_passes(self) -> None:
        """TRENDレジームはフィルタ対象外"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                regime=MarketRegime.TREND,
            ),
            consensus=_make_consensus(),
            sg_result=_make_sg(),
            hour_utc=10,
            plan=self.plan,
            current_time=self.time,
        )
        assert result is None

    def test_range_high_score_passes(self) -> None:
        """RANGEでもスコアが十分高ければ通過"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.5,
            ),
            consensus=_make_consensus(
                score=12.0, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        assert result is None

    def test_range_weak_trend_blocks(self) -> None:
        """RANGE+弱トレンドで閾値超過時にブロック"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.05,
            ),
            consensus=_make_consensus(
                score=8.1, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        # trend_strength=0.05 → スコア≈0.83
        # score_premium: score=8.1, threshold+0.3=8.3
        #   margin=0.2, _s=0.2/0.3≈0.67
        # 合計 ≈ 1.5 > 0.6 → ブロック
        assert result is not None
        assert "RANGE統合フィルタ" in result

    def test_low_vol_marginal_blocks(self) -> None:
        """LOW_VOLでスコア余裕が小さい場合ブロック"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                regime=MarketRegime.LOW_VOL,
            ),
            consensus=_make_consensus(
                score=8.5, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        # margin = 8.0+1.5-8.5 = 1.0
        # _s = min(1.0/1.5, 1.0) ≈ 0.67 > 0.6
        assert result is not None
        assert "LOW_VOL" in result

    def test_low_vol_high_score_passes(self) -> None:
        """LOW_VOLでもスコアが高ければ通過"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                regime=MarketRegime.LOW_VOL,
            ),
            consensus=_make_consensus(
                score=10.0, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        # margin = 8.0+1.5-10.0 = -0.5 < 0 → スコア0
        assert result is None

    def test_weak_hours_adds_score(self) -> None:
        """WeakHours時間帯でスコアが加算される"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.5,
            ),
            consensus=_make_consensus(
                score=8.2, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=10,  # WeakHours帯
            plan=self.plan,
            current_time=self.time,
        )
        # weak_hours: threshold+0.5=8.5, margin=0.3
        #   _s = min(0.3/0.5, 1.0) = 0.6
        # score_premium: threshold+0.3=8.3, margin=0.1
        #   _s = min(0.1/0.3, 1.0) ≈ 0.33
        # 合計 ≈ 0.93 > 0.6 → ブロック
        assert result is not None
        assert "WeakHours" in result

    def test_non_weak_hours_fewer_signals(self) -> None:
        """WeakHours外ではWeakHoursスコアが加算されない"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.5,
            ),
            consensus=_make_consensus(
                score=8.2, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,  # WeakHours外
            plan=self.plan,
            current_time=self.time,
        )
        # score_premiumのみ: margin=0.1, _s≈0.33 < 0.6
        assert result is None

    def test_multiple_conditions_accumulate(self) -> None:
        """複数条件のスコアが累積する"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.15,
            ),
            consensus=_make_consensus(
                score=8.1, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=10,
            plan=self.plan,
            current_time=self.time,
        )
        # 弱トレンド: 1-0.15/0.3=0.5
        # WeakHours: margin=0.4, _s=0.8
        # スコアPrem: margin=0.2, _s≈0.67
        # 合計 ≈ 1.97 > 0.6 → ブロック
        assert result is not None

    def test_custom_block_threshold(self) -> None:
        """カスタム閾値でのブロック判定"""
        config = UnifiedBotConfig(
            range_filter_consolidated=True,
            range_filter_block_threshold=2.0,
            range_day_score_premium=0.3,
        )
        bot = UnifiedTradeBot(config)
        result = bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.05,
            ),
            consensus=_make_consensus(
                score=8.1, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        # 弱トレンド≈0.83 + スコアPrem≈0.67 = 1.5 < 2.0
        assert result is None


class TestRangeFilterLegacy:
    """従来RANGEフィルタ（フォールバック）のテスト"""

    def setup_method(self) -> None:
        """テストセットアップ"""
        self.config = UnifiedBotConfig(
            range_filter_consolidated=False,
            range_day_bbw_threshold=0.25,
            range_day_score_premium=0.3,
            weak_hours_enabled=True,
            weak_hours_score_premium=0.5,
        )
        self.bot = UnifiedTradeBot(self.config)
        self.plan = _make_plan()
        self.time = pd.Timestamp("2023-06-15 10:00:00")

    def test_low_vol_blocks(self) -> None:
        """LOW_VOLで低スコア時にブロック"""
        result = self.bot._check_range_legacy(
            regime_result=_make_regime(
                regime=MarketRegime.LOW_VOL,
            ),
            consensus=_make_consensus(
                score=9.0, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        # score=9.0 < threshold+1.5=9.5 → ブロック
        assert result is not None
        assert "LOW_VOL制限" in result

    def test_range_weak_trend_blocks(self) -> None:
        """RANGE+弱トレンドでブロック"""
        result = self.bot._check_range_legacy(
            regime_result=_make_regime(
                trend_strength=0.1,
            ),
            consensus=_make_consensus(),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        assert result is not None
        assert "RANGE制限" in result

    def test_trend_regime_passes(self) -> None:
        """TRENDレジームは全フィルタ通過"""
        result = self.bot._check_range_legacy(
            regime_result=_make_regime(
                regime=MarketRegime.TREND,
            ),
            consensus=_make_consensus(),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        assert result is None

    def test_range_score_premium_blocks(self) -> None:
        """RANGEスコアプレミアムでブロック"""
        result = self.bot._check_range_legacy(
            regime_result=_make_regime(
                trend_strength=0.5,
            ),
            consensus=_make_consensus(
                score=8.1, threshold=8.0,
            ),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        # score=8.1 < threshold+0.3=8.3 → ブロック
        assert result is not None
        assert "RANGEスコアプレミアム" in result

    def test_fallback_mode_uses_legacy(self) -> None:
        """consolidated=Falseでlegacyが使用される"""
        result = self.bot._check_range_regime_filter(
            regime_result=_make_regime(
                trend_strength=0.1,
            ),
            consensus=_make_consensus(),
            sg_result=_make_sg(),
            hour_utc=14,
            plan=self.plan,
            current_time=self.time,
        )
        assert result is not None
        assert "RANGE制限" in result


