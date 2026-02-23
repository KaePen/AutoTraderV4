"""TickEntryOptimizer テスト

状態遷移、条件評価、タイムアウト、衝突ポリシーのテスト。
合成tickデータで全ロジックをテスト（MT5不要）。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from autotrader.core.entities import Signal
from autotrader.core.enums import SignalType, Timeframe
from autotrader.live.tick_entry_config import TickEntryConfig
from autotrader.live.tick_entry_optimizer import (
    OptimizerState,
    TickBuffer,
    TickEntryOptimizer,
)


# --- ヘルパー ---

def _make_signal(
    signal_type: SignalType = SignalType.BUY,
    symbol: str = "USDJPY",
) -> Signal:
    """テスト用シグナル生成"""
    return Signal(
        signal_type=signal_type,
        symbol=symbol,
        timeframe=Timeframe.M1,
        confidence=0.8,
    )


def _make_tick(
    ask: float = 150.100,
    bid: float = 150.090,
) -> dict:
    """テスト用ティックデータ生成"""
    return {
        "ask": ask,
        "bid": bid,
        "last": (ask + bid) / 2,
        "time": int(time.time()),
    }


def _make_ticks_uptrend(
    count: int = 10,
    start_ask: float = 150.100,
    start_bid: float = 150.090,
    step: float = 0.001,
) -> list[dict]:
    """上昇トレンドのティック列を生成"""
    return [
        _make_tick(
            ask=start_ask + i * step,
            bid=start_bid + i * step,
        )
        for i in range(count)
    ]


def _make_ticks_downtrend(
    count: int = 10,
    start_ask: float = 150.100,
    start_bid: float = 150.090,
    step: float = 0.001,
) -> list[dict]:
    """下降トレンドのティック列を生成"""
    return [
        _make_tick(
            ask=start_ask - i * step,
            bid=start_bid - i * step,
        )
        for i in range(count)
    ]


def _make_ticks_retrace_buy(count: int = 10) -> list[dict]:
    """BUYリトレース: 下がって戻る"""
    ticks = []
    # 最初は下降
    for i in range(count // 2):
        ticks.append(
            _make_tick(
                ask=150.100 - i * 0.002,
                bid=150.090 - i * 0.002,
            )
        )
    # 後半は上昇（回復）
    low_ask = 150.100 - (count // 2 - 1) * 0.002
    low_bid = 150.090 - (count // 2 - 1) * 0.002
    for i in range(count - count // 2):
        ticks.append(
            _make_tick(
                ask=low_ask + i * 0.003,
                bid=low_bid + i * 0.003,
            )
        )
    return ticks


# --- TickBuffer テスト ---

class TestTickBuffer:
    """TickBufferテスト"""

    def test_初期状態(self) -> None:
        """初期状態は空"""
        buf = TickBuffer()
        assert buf.count == 0
        assert buf.elapsed == 0.0

    def test_ティック追加(self) -> None:
        """ティック追加で数が増える"""
        buf = TickBuffer()
        buf.add(_make_tick())
        assert buf.count == 1
        buf.add(_make_tick())
        assert buf.count == 2

    def test_クリア(self) -> None:
        """クリアで空に戻る"""
        buf = TickBuffer()
        buf.add(_make_tick())
        buf.start_time = time.monotonic()
        buf.clear()
        assert buf.count == 0
        assert buf.start_time == 0.0


# --- 状態遷移テスト ---

class TestOptimizerStateTransitions:
    """状態遷移テスト"""

    def test_初期状態はIDLE(self) -> None:
        """初期化後はIDLE"""
        config = TickEntryConfig()
        opt = TickEntryOptimizer(config)
        assert opt.state == OptimizerState.IDLE
        assert opt.is_active is False

    def test_IDLE_to_MONITORING(self) -> None:
        """監視開始でMONITORINGに遷移"""
        config = TickEntryConfig()
        opt = TickEntryOptimizer(config)
        signal = _make_signal()

        result = opt.start_monitoring(signal)

        assert result is True
        assert opt.state == OptimizerState.MONITORING
        assert opt.is_active is True
        assert opt.pending_signal == signal

    def test_MONITORING_to_CANCELLED(self) -> None:
        """キャンセルでCANCELLEDに遷移"""
        config = TickEntryConfig()
        opt = TickEntryOptimizer(config)
        opt.start_monitoring(_make_signal())

        opt.cancel_monitoring("テストキャンセル")

        assert opt.state == OptimizerState.CANCELLED
        assert opt.pending_signal is None

    def test_reset後はIDLE(self) -> None:
        """resetでIDLEに復帰"""
        config = TickEntryConfig()
        opt = TickEntryOptimizer(config)
        opt.start_monitoring(_make_signal())
        opt.cancel_monitoring("test")

        opt.reset()

        assert opt.state == OptimizerState.IDLE
        assert opt.pending_signal is None
        assert opt.tick_count == 0


# --- 衝突ポリシーテスト ---

class TestConflictPolicy:
    """シグナル衝突ポリシーテスト"""

    def test_replaceポリシー(self) -> None:
        """replaceポリシーで既存シグナルを置換"""
        config = TickEntryConfig(conflict_policy="replace")
        opt = TickEntryOptimizer(config)
        signal1 = _make_signal(SignalType.BUY)
        signal2 = _make_signal(SignalType.SELL)

        opt.start_monitoring(signal1)
        result = opt.start_monitoring(signal2)

        assert result is True
        assert opt.pending_signal == signal2

    def test_ignoreポリシー(self) -> None:
        """ignoreポリシーで新シグナルを無視"""
        config = TickEntryConfig(conflict_policy="ignore")
        opt = TickEntryOptimizer(config)
        signal1 = _make_signal(SignalType.BUY)
        signal2 = _make_signal(SignalType.SELL)

        opt.start_monitoring(signal1)
        result = opt.start_monitoring(signal2)

        assert result is False
        assert opt.pending_signal == signal1


# --- タイムアウトテスト ---

class TestTimeout:
    """タイムアウトテスト"""

    @pytest.mark.asyncio
    async def test_タイムアウトで成行実行(self) -> None:
        """タイムアウト時にexecute_on_timeout=Trueで実行"""
        config = TickEntryConfig(
            max_monitoring_sec=0.01,
            execute_on_timeout=True,
            momentum_min_ticks=1,
        )
        mock_dp = MagicMock()
        mock_dp.get_tick_fast = AsyncMock(
            return_value=_make_tick()
        )
        opt = TickEntryOptimizer(
            config, data_provider=mock_dp
        )
        opt.start_monitoring(_make_signal())

        # 待ってタイムアウトさせる
        await asyncio.sleep(0.02)
        result = await opt.poll_tick()

        assert result is not None
        assert result.timed_out is True
        assert result.should_execute is True
        assert opt.state == OptimizerState.TIMED_OUT

    @pytest.mark.asyncio
    async def test_タイムアウトで実行しない(self) -> None:
        """execute_on_timeout=Falseで実行しない"""
        config = TickEntryConfig(
            max_monitoring_sec=0.01,
            execute_on_timeout=False,
            momentum_min_ticks=1,
        )
        opt = TickEntryOptimizer(config)
        opt.start_monitoring(_make_signal())

        await asyncio.sleep(0.02)
        result = await opt.poll_tick()

        assert result is not None
        assert result.timed_out is True
        assert result.should_execute is False


# --- 条件評価テスト ---

class TestEvaluateConditions:
    """条件評価（純粋関数）テスト"""

    def test_空ティックで実行しない(self) -> None:
        """空のティックリストでは実行しない"""
        config = TickEntryConfig()
        opt = TickEntryOptimizer(config)
        signal = _make_signal()

        result = opt.evaluate_conditions([], signal)

        assert result.should_execute is False

    def test_BUY_上昇トレンド_低スプレッド(self) -> None:
        """BUYシグナル+上昇トレンド+低スプレッドで高スコア"""
        config = TickEntryConfig(
            spread_threshold_pips=2.0,
            composite_threshold=0.5,
        )
        opt = TickEntryOptimizer(
            config, symbol="USDJPY"
        )
        # スプレッド0.1pip（ask-bid=0.001）+ 上昇トレンド
        ticks = _make_ticks_uptrend(
            10,
            start_ask=150.100,
            start_bid=150.099,
        )
        signal = _make_signal(SignalType.BUY)

        result = opt.evaluate_conditions(ticks, signal)

        assert result.spread_score > 0.8
        assert result.momentum_score > 0.5
        assert result.composite_score > 0.5
        assert result.should_execute is True

    def test_SELL_下降トレンド_低スプレッド(self) -> None:
        """SELLシグナル+下降トレンド+低スプレッドで高スコア"""
        config = TickEntryConfig(
            spread_threshold_pips=2.0,
            composite_threshold=0.5,
        )
        opt = TickEntryOptimizer(
            config, symbol="USDJPY"
        )
        ticks = _make_ticks_downtrend(
            10,
            start_ask=150.100,
            start_bid=150.099,
        )
        signal = _make_signal(SignalType.SELL)

        result = opt.evaluate_conditions(ticks, signal)

        assert result.spread_score > 0.8
        assert result.momentum_score > 0.5
        assert result.should_execute is True

    def test_BUY_逆行トレンドで低スコア(self) -> None:
        """BUYシグナル+下降トレンドでモメンタムスコア低"""
        config = TickEntryConfig(
            spread_threshold_pips=2.0,
            composite_threshold=0.7,
        )
        opt = TickEntryOptimizer(
            config, symbol="USDJPY"
        )
        ticks = _make_ticks_downtrend(
            10,
            start_ask=150.100,
            start_bid=150.099,
        )
        signal = _make_signal(SignalType.BUY)

        result = opt.evaluate_conditions(ticks, signal)

        assert result.momentum_score < 0.3

    def test_高スプレッドで低スコア(self) -> None:
        """高スプレッドでスプレッドスコアが低い"""
        config = TickEntryConfig(
            spread_threshold_pips=1.0,
            composite_threshold=0.5,
        )
        opt = TickEntryOptimizer(
            config, symbol="USDJPY"
        )
        # スプレッド5pips（ask-bid=0.05）
        ticks = _make_ticks_uptrend(
            10,
            start_ask=150.150,
            start_bid=150.100,
        )
        signal = _make_signal(SignalType.BUY)

        result = opt.evaluate_conditions(ticks, signal)

        assert result.spread_score == 0.0

    def test_リトレースメント有効(self) -> None:
        """リトレースメント有効時にスコアが反映される"""
        config = TickEntryConfig(
            spread_threshold_pips=2.0,
            retracement_enabled=True,
            composite_threshold=0.3,
        )
        opt = TickEntryOptimizer(
            config, symbol="USDJPY"
        )
        ticks = _make_ticks_retrace_buy(10)
        signal = _make_signal(SignalType.BUY)

        result = opt.evaluate_conditions(ticks, signal)

        assert result.retracement_score > 0.0


# --- ポーリング統合テスト ---

class TestPollTick:
    """poll_tick統合テスト"""

    @pytest.mark.asyncio
    async def test_IDLE状態ではNone返却(self) -> None:
        """IDLE状態ではNoneを返す"""
        config = TickEntryConfig()
        opt = TickEntryOptimizer(config)

        result = await opt.poll_tick()

        assert result is None

    @pytest.mark.asyncio
    async def test_最低観測数未達で継続(self) -> None:
        """最低観測数に達するまでNoneを返す"""
        config = TickEntryConfig(
            momentum_min_ticks=5,
            max_monitoring_sec=10.0,
        )
        mock_dp = MagicMock()
        mock_dp.get_tick_fast = AsyncMock(
            return_value=_make_tick()
        )
        opt = TickEntryOptimizer(
            config, data_provider=mock_dp
        )
        opt.start_monitoring(_make_signal())

        # 1回目: min_ticks=5 未到達
        result = await opt.poll_tick()
        assert result is None
        assert opt.tick_count == 1

    @pytest.mark.asyncio
    async def test_条件成立でEXECUTING(self) -> None:
        """条件成立でEXECUTINGに遷移"""
        config = TickEntryConfig(
            momentum_min_ticks=3,
            max_monitoring_sec=10.0,
            composite_threshold=0.3,
            spread_threshold_pips=5.0,
        )

        # 上昇ティックを順に返す
        tick_seq = _make_ticks_uptrend(
            5,
            start_ask=150.100,
            start_bid=150.099,
        )
        call_count = 0

        async def mock_get_tick_fast(symbol: str) -> dict:
            nonlocal call_count
            idx = min(call_count, len(tick_seq) - 1)
            call_count += 1
            return tick_seq[idx]

        mock_dp = MagicMock()
        mock_dp.get_tick_fast = mock_get_tick_fast

        opt = TickEntryOptimizer(
            config,
            data_provider=mock_dp,
            symbol="USDJPY",
        )
        opt.start_monitoring(
            _make_signal(SignalType.BUY)
        )

        # min_ticks回ポーリング
        result = None
        for _ in range(10):
            result = await opt.poll_tick()
            if result is not None:
                break

        assert result is not None
        assert result.should_execute is True
        assert opt.state == OptimizerState.EXECUTING

    @pytest.mark.asyncio
    async def test_データプロバイダなしで空dict(self) -> None:
        """データプロバイダなしでも安全に動作"""
        config = TickEntryConfig(
            momentum_min_ticks=1,
            max_monitoring_sec=10.0,
        )
        opt = TickEntryOptimizer(config)
        opt.start_monitoring(_make_signal())

        # ティック取得失敗→バッファに追加されない
        result = await opt.poll_tick()
        assert result is None
        assert opt.tick_count == 0


# --- EURUSD（非JPY）テスト ---

class TestNonJPYSymbol:
    """非JPYシンボルのテスト"""

    def test_EURUSD_スプレッド計算(self) -> None:
        """EURUSDでpip計算が正しい"""
        config = TickEntryConfig(
            spread_threshold_pips=1.0,
        )
        opt = TickEntryOptimizer(
            config, symbol="EURUSD"
        )
        # 0.5pips = 0.00005
        ticks = [
            _make_tick(ask=1.10005, bid=1.10000),
        ]
        signal = _make_signal(
            SignalType.BUY, symbol="EURUSD"
        )

        result = opt.evaluate_conditions(ticks, signal)

        assert result.spread_score > 0.8


# --- 設定テスト ---

class TestTickEntryConfig:
    """TickEntryConfig テスト"""

    def test_デフォルト値(self) -> None:
        """デフォルト値が正しい"""
        config = TickEntryConfig()
        assert config.enabled is True
        assert config.poll_interval_sec == 0.1
        assert config.max_monitoring_sec == 30.0
        assert config.execute_on_timeout is True
        assert config.conflict_policy == "replace"

    def test_frozenフィールド(self) -> None:
        """frozenなので変更不可"""
        config = TickEntryConfig()
        with pytest.raises(Exception):
            config.enabled = True  # type: ignore[misc]

    def test_カスタム値(self) -> None:
        """カスタム値が正しく設定される"""
        config = TickEntryConfig(
            enabled=True,
            poll_interval_sec=0.05,
            max_monitoring_sec=15.0,
            spread_threshold_pips=2.0,
        )
        assert config.enabled is True
        assert config.poll_interval_sec == 0.05
