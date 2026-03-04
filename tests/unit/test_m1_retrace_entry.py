"""M1リトレースエントリーのユニットテスト."""

from __future__ import annotations

import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.trade_bot import PendingEntry


class TestPendingEntry:
    """PendingEntryデータクラスのテスト."""

    def test_creation(self) -> None:
        """基本的な生成テスト."""
        pe = PendingEntry(
            direction=SignalType.BUY,
            target_price=149.50,
            original_close=150.00,
        )
        assert pe.direction == SignalType.BUY
        assert pe.target_price == 149.50
        assert pe.original_close == 150.00
        assert pe.bars_waited == 0
        assert pe.max_wait_bars == 5
        assert pe.fallback_entry is True

    def test_bars_waited_mutable(self) -> None:
        """bars_waitedは変更可能."""
        pe = PendingEntry(
            direction=SignalType.BUY,
            target_price=149.50,
            original_close=150.00,
        )
        pe.bars_waited += 1
        assert pe.bars_waited == 1

    def test_custom_config(self) -> None:
        """カスタム設定での生成."""
        pe = PendingEntry(
            direction=SignalType.SELL,
            target_price=150.50,
            original_close=150.00,
            max_wait_bars=10,
            fallback_entry=False,
            sl_pips=25.0,
            tp_pips=35.0,
            confidence=0.85,
            primary_tf="M15",
        )
        assert pe.direction == SignalType.SELL
        assert pe.max_wait_bars == 10
        assert pe.fallback_entry is False
        assert pe.sl_pips == 25.0
        assert pe.tp_pips == 35.0


class TestRetraceEntryConfig:
    """リトレースエントリー設定のテスト."""

    def test_default_disabled(self) -> None:
        """デフォルトで無効."""
        config = UnifiedBotConfig()
        assert config.m1_retrace_entry_enabled is False

    def test_default_values(self) -> None:
        """デフォルト値の確認."""
        config = UnifiedBotConfig()
        assert config.m1_retrace_atr_factor == 0.5
        assert config.m1_retrace_max_wait_bars == 5
        assert config.m1_retrace_fallback_entry is True

    def test_custom_values(self) -> None:
        """カスタム値の設定."""
        config = UnifiedBotConfig(
            m1_retrace_entry_enabled=True,
            m1_retrace_atr_factor=0.3,
            m1_retrace_max_wait_bars=10,
            m1_retrace_fallback_entry=False,
        )
        assert config.m1_retrace_entry_enabled is True
        assert config.m1_retrace_atr_factor == 0.3
        assert config.m1_retrace_max_wait_bars == 10
        assert config.m1_retrace_fallback_entry is False


class TestRetraceTargetCalculation:
    """リトレースターゲット価格の計算テスト."""

    def test_buy_target(self) -> None:
        """BUY: 現在価格からATR分下にターゲット."""
        close = 150.00
        atr_14 = 0.10  # 10pips
        pip_unit = 0.01
        factor = 0.5
        retrace_pips = atr_14 / pip_unit * factor
        # 10 * 0.5 = 5 pips
        target = close - retrace_pips * pip_unit
        # 150.00 - 0.05 = 149.95
        assert target == pytest.approx(149.95)

    def test_sell_target(self) -> None:
        """SELL: 現在価格からATR分上にターゲット."""
        close = 150.00
        atr_14 = 0.10  # 10pips
        pip_unit = 0.01
        factor = 0.5
        retrace_pips = atr_14 / pip_unit * factor
        target = close + retrace_pips * pip_unit
        # 150.00 + 0.05 = 150.05
        assert target == pytest.approx(150.05)


class TestRetraceReachDetection:
    """リトレース到達判定のテスト."""

    def test_buy_reached(self) -> None:
        """BUY: lowがターゲット以下 → 到達."""
        target = 149.95
        low = 149.93
        assert low <= target

    def test_buy_not_reached(self) -> None:
        """BUY: lowがターゲット上 → 未到達."""
        target = 149.95
        low = 149.97
        assert not (low <= target)

    def test_sell_reached(self) -> None:
        """SELL: highがターゲット以上 → 到達."""
        target = 150.05
        high = 150.07
        assert high >= target

    def test_sell_not_reached(self) -> None:
        """SELL: highがターゲット下 → 未到達."""
        target = 150.05
        high = 150.03
        assert not (high >= target)


class TestTimeoutBehavior:
    """タイムアウト動作のテスト."""

    def test_timeout_with_fallback(self) -> None:
        """タイムアウト+フォールバック → エントリー実行."""
        pe = PendingEntry(
            direction=SignalType.BUY,
            target_price=149.95,
            original_close=150.00,
            max_wait_bars=5,
            fallback_entry=True,
        )
        pe.bars_waited = 5
        assert pe.bars_waited >= pe.max_wait_bars
        assert pe.fallback_entry is True

    def test_timeout_without_fallback(self) -> None:
        """タイムアウト+フォールバック無し → キャンセル."""
        pe = PendingEntry(
            direction=SignalType.BUY,
            target_price=149.95,
            original_close=150.00,
            max_wait_bars=5,
            fallback_entry=False,
        )
        pe.bars_waited = 5
        assert pe.bars_waited >= pe.max_wait_bars
        assert pe.fallback_entry is False

    def test_not_timed_out(self) -> None:
        """待機中 → タイムアウトしていない."""
        pe = PendingEntry(
            direction=SignalType.BUY,
            target_price=149.95,
            original_close=150.00,
            max_wait_bars=5,
        )
        pe.bars_waited = 3
        assert pe.bars_waited < pe.max_wait_bars
