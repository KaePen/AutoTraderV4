"""V2RiskManager テスト。"""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import V2RiskConfig
from autotrader.decision.v2.market_context import H1Indicators
from autotrader.decision.v2.risk_manager import (
    V2BotState,
    V2RiskManager,
)
from autotrader.decision.v2.strategies.base import V2EntrySignal
from tests.unit.decision.v2.conftest import make_context


class TestNoTradeCheck:
    """NoTrade条件テスト。"""

    def test_スプレッド超過(self):
        mgr = V2RiskManager()
        ctx = make_context(spread_pips=5.0)
        state = V2BotState()
        result = mgr.check_no_trade(ctx, state)
        assert result is not None
        assert result.code == "HIGH_SPREAD"

    def test_ブロック時間帯(self):
        from datetime import datetime
        from autotrader.decision.v2.market_context import (
            MarketContext,
        )

        mgr = V2RiskManager()
        ctx = make_context(spread_pips=1.0)
        # UTC 23時に設定
        ctx_blocked = MarketContext(
            current_price=ctx.current_price,
            current_time=datetime(2024, 6, 15, 23, 0),
            h1=ctx.h1,
            h4=ctx.h4,
            d1=ctx.d1,
            price_action=ctx.price_action,
            ma_alignment=ctx.ma_alignment,
            volatility_trend=ctx.volatility_trend,
            spread_pips=1.0,
        )
        state = V2BotState()
        result = mgr.check_no_trade(ctx_blocked, state)
        assert result is not None
        assert result.code == "BLOCKED_HOURS"

    def test_連敗超過(self):
        mgr = V2RiskManager()
        ctx = make_context(spread_pips=1.0)
        state = V2BotState(consecutive_losses=4)
        result = mgr.check_no_trade(ctx, state)
        assert result is not None
        assert result.code == "CONSECUTIVE_LOSSES"

    def test_正常時はNone(self):
        mgr = V2RiskManager()
        ctx = make_context(spread_pips=1.0)
        state = V2BotState()
        result = mgr.check_no_trade(ctx, state)
        assert result is None


class TestValidateSignal:
    """シグナル検証テスト。"""

    def test_BUY_正常(self):
        mgr = V2RiskManager()
        ctx = make_context(price=150.0)
        signal = V2EntrySignal(
            direction=SignalType.BUY,
            confidence=0.7,
            sl_price=149.50,
            tp_price=151.00,
            reasoning="test",
            strategy_name="test",
        )
        assert mgr.validate_signal(signal, ctx) is True

    def test_BUY_SL不正(self):
        mgr = V2RiskManager()
        ctx = make_context(price=150.0)
        signal = V2EntrySignal(
            direction=SignalType.BUY,
            confidence=0.7,
            sl_price=150.50,  # SL > price は不正
            tp_price=151.00,
            reasoning="test",
            strategy_name="test",
        )
        assert mgr.validate_signal(signal, ctx) is False

    def test_SELL_正常(self):
        mgr = V2RiskManager()
        ctx = make_context(price=150.0)
        signal = V2EntrySignal(
            direction=SignalType.SELL,
            confidence=0.7,
            sl_price=150.50,
            tp_price=149.00,
            reasoning="test",
            strategy_name="test",
        )
        assert mgr.validate_signal(signal, ctx) is True


class TestLotCalculation:
    """ロットサイズ計算テスト。"""

    def test_基本計算(self):
        cfg = V2RiskConfig(
            base_risk_pct=0.02,
            confidence_scale=False,
        )
        mgr = V2RiskManager(config=cfg, pip_value=100.0)
        ctx = make_context(price=150.0)
        state = V2BotState(equity=1_000_000.0)
        signal = V2EntrySignal(
            direction=SignalType.BUY,
            confidence=0.7,
            sl_price=149.80,  # 20pips SL
            tp_price=150.40,
            reasoning="test",
            strategy_name="test",
        )
        lot = mgr.calculate_lot(signal, ctx, state)
        # リスク = 1M × 0.02 = 20,000
        # SL 20pips × pip_value 100 = 2000/lot
        # lot = 20,000 / 2,000 = 10.0 → clamped to 5.0
        assert lot == 5.0

    def test_確信度スケーリング(self):
        cfg = V2RiskConfig(
            base_risk_pct=0.02,
            max_risk_pct=0.04,
            confidence_scale=True,
        )
        mgr = V2RiskManager(config=cfg, pip_value=100.0)
        ctx = make_context(price=150.0)
        state = V2BotState(equity=500_000.0)

        # 低確信度
        sig_low = V2EntrySignal(
            direction=SignalType.BUY,
            confidence=0.5,
            sl_price=149.70,  # 30pips
            tp_price=150.60,
            reasoning="test",
            strategy_name="test",
        )
        lot_low = mgr.calculate_lot(sig_low, ctx, state)

        # 高確信度
        sig_high = V2EntrySignal(
            direction=SignalType.BUY,
            confidence=1.0,
            sl_price=149.70,
            tp_price=150.60,
            reasoning="test",
            strategy_name="test",
        )
        lot_high = mgr.calculate_lot(sig_high, ctx, state)

        assert lot_high > lot_low

    def test_最低ロット(self):
        cfg = V2RiskConfig(
            base_risk_pct=0.001,
            confidence_scale=False,
        )
        mgr = V2RiskManager(config=cfg, pip_value=100.0)
        ctx = make_context(price=150.0)
        state = V2BotState(equity=10_000.0)
        signal = V2EntrySignal(
            direction=SignalType.BUY,
            confidence=0.5,
            sl_price=149.50,  # 50pips
            tp_price=151.00,
            reasoning="test",
            strategy_name="test",
        )
        lot = mgr.calculate_lot(signal, ctx, state)
        assert lot >= 0.01


class TestTrailingStop:
    """トレーリングストップテスト。"""

    def test_ブレイクイーブン(self):
        mgr = V2RiskManager()
        # BUY: entry=150, SL=149.5 (50pips risk)
        # price=150.5 → 1R達成
        new_sl = mgr.calculate_trailing_stop(
            entry_price=150.0,
            current_price=150.50,
            current_sl=149.50,
            direction=SignalType.BUY,
            atr=0.15,
        )
        assert new_sl is not None
        assert new_sl == 150.0  # ブレイクイーブン

    def test_トレーリング開始(self):
        mgr = V2RiskManager()
        # BUY: entry=150, SL=150(BE) (50pips risk)
        # price=150.75 → 1.5R達成
        new_sl = mgr.calculate_trailing_stop(
            entry_price=150.0,
            current_price=150.75,
            current_sl=150.0,  # BE済み
            direction=SignalType.BUY,
            atr=0.15,
        )
        assert new_sl is not None
        assert new_sl > 150.0  # BEより上

    def test_変更不要(self):
        mgr = V2RiskManager()
        # BUY: まだ利益不足
        new_sl = mgr.calculate_trailing_stop(
            entry_price=150.0,
            current_price=150.20,
            current_sl=149.50,
            direction=SignalType.BUY,
            atr=0.15,
        )
        # 0.4R、BE条件(1R)未達
        assert new_sl is None
