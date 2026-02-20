"""エンジン-TickEntryOptimizer統合テスト

LiveTradingEngineとTickEntryOptimizerの統合フロー。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autotrader.adapters.mt5.config import MT5Config
from autotrader.core.entities import Signal
from autotrader.core.enums import SignalType, Timeframe
from autotrader.live.config import LiveTradingConfig
from autotrader.live.engine import LiveTradingEngine
from autotrader.live.tick_entry_config import TickEntryConfig
from autotrader.live.tick_entry_optimizer import (
    OptimizerState,
)


@pytest.fixture
def tick_enabled_config() -> LiveTradingConfig:
    """ティック最適化有効の設定"""
    return LiveTradingConfig(
        symbol="USDJPY",
        check_interval_sec=0.01,
        candle_lookback=10,
        mt5_config=MT5Config(
            transport="direct",
            retry_count=1,
            retry_delay_sec=0.01,
        ),
        tick_entry_config=TickEntryConfig(
            enabled=True,
            enabled_modes=("SCALPING",),
            poll_interval_sec=0.01,
            max_monitoring_sec=1.0,
        ),
    )


@pytest.fixture
def tick_disabled_config() -> LiveTradingConfig:
    """ティック最適化無効の設定"""
    return LiveTradingConfig(
        symbol="USDJPY",
        check_interval_sec=0.01,
        candle_lookback=10,
        mt5_config=MT5Config(
            transport="direct",
            retry_count=1,
            retry_delay_sec=0.01,
        ),
        tick_entry_config=TickEntryConfig(enabled=False),
    )


class TestEngineTickOptimizer:
    """エンジン-TickEntryOptimizer統合テスト"""

    def test_ティック最適化インスタンス生成(
        self, tick_enabled_config: LiveTradingConfig,
    ) -> None:
        """エンジン初期化でTickEntryOptimizerが生成"""
        engine = LiveTradingEngine(tick_enabled_config)

        assert engine._tick_optimizer is not None
        assert (
            engine._tick_optimizer.state
            == OptimizerState.IDLE
        )

    def test_無効時のインスタンス(
        self, tick_disabled_config: LiveTradingConfig,
    ) -> None:
        """無効時もインスタンスは生成される"""
        engine = LiveTradingEngine(tick_disabled_config)

        assert engine._tick_optimizer is not None

    def test_should_use_tick_optimizer_有効(
        self, tick_enabled_config: LiveTradingConfig,
    ) -> None:
        """enabled=Trueで使用すべきと判定"""
        engine = LiveTradingEngine(tick_enabled_config)

        # モード設定（botのcurrent_modeモック）
        engine._bot.current_mode = "SCALPING"  # type: ignore[attr-defined]

        result = engine._should_use_tick_optimizer()
        assert result is True

    def test_should_use_tick_optimizer_無効(
        self, tick_disabled_config: LiveTradingConfig,
    ) -> None:
        """enabled=Falseで使用しないと判定"""
        engine = LiveTradingEngine(tick_disabled_config)

        result = engine._should_use_tick_optimizer()
        assert result is False

    def test_should_use_tick_optimizer_デモモード(
        self, tick_enabled_config: LiveTradingConfig,
    ) -> None:
        """デモモードでは無効"""
        engine = LiveTradingEngine(tick_enabled_config)
        # bot.configはfrozen dataclassのためdemo_mode=Trueの新configで置換
        import dataclasses
        demo_config = dataclasses.replace(
            engine._bot.config, demo_mode=True
        )
        engine._bot.config = demo_config

        result = engine._should_use_tick_optimizer()
        assert result is False

    def test_should_use_tick_optimizer_モード不一致(
        self, tick_enabled_config: LiveTradingConfig,
    ) -> None:
        """有効モード外では使用しない"""
        engine = LiveTradingEngine(tick_enabled_config)
        engine._bot._last_mode = "SWING"  # type: ignore[attr-defined]

        result = engine._should_use_tick_optimizer()
        assert result is False


class TestEngineTickConfigIntegration:
    """設定統合テスト"""

    def test_デフォルトConfigにTickEntry含む(self) -> None:
        """デフォルトLiveTradingConfigにtick_entry_config"""
        config = LiveTradingConfig()
        assert hasattr(config, "tick_entry_config")
        assert config.tick_entry_config.enabled is True

    def test_カスタムTickEntryConfig(self) -> None:
        """カスタムTickEntryConfigが正しく設定"""
        config = LiveTradingConfig(
            tick_entry_config=TickEntryConfig(
                enabled=True,
                max_monitoring_sec=15.0,
            ),
        )
        assert config.tick_entry_config.enabled is True
        assert (
            config.tick_entry_config.max_monitoring_sec
            == 15.0
        )


class TestEngineStopWithOptimizer:
    """エンジン停止時のOptimizer処理テスト"""

    @pytest.mark.asyncio
    async def test_停止時に監視キャンセル(
        self, tick_enabled_config: LiveTradingConfig,
    ) -> None:
        """エンジン停止時に監視をキャンセル"""
        engine = LiveTradingEngine(tick_enabled_config)

        # 監視開始をシミュレート
        signal = Signal(
            signal_type=SignalType.BUY,
            symbol="USDJPY",
            timeframe=Timeframe.M1,
            confidence=0.8,
        )
        engine._tick_optimizer.start_monitoring(signal)
        assert engine._tick_optimizer.is_active is True

        # 停止（MT5接続モック）
        engine._conn._connected = False
        engine._conn._transport = MagicMock()
        engine._conn._transport.shutdown = AsyncMock()

        await engine.stop()

        assert (
            engine._tick_optimizer.state
            != OptimizerState.MONITORING
        )
