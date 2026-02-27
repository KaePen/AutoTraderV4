"""V2TradeBot テスト。"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from autotrader.core.enums import SignalType
from autotrader.decision.v2.config import (
    RegimeClassifierConfig,
    V2BotConfig,
)
from autotrader.decision.v2.regime_classifier import (
    MarketRegimeV2,
)
from autotrader.decision.v2.risk_manager import V2BotState
from autotrader.decision.v2.trade_bot import V2TradeBot


def _make_market_data(
    n_h1: int = 200,
    n_h4: int = 50,
    n_d1: int = 20,
    trend: str = "up",
) -> dict[str, pd.DataFrame]:
    """テスト用market_data生成。

    PrecomputeEngine通過前のraw OHLCVデータ。
    V2TradeBot.set_market_data が内部で
    SMC/PA列を追加する。
    """
    base_price = 150.0
    atr = 0.15

    def _make_df(
        n: int, freq: str,
    ) -> pd.DataFrame:
        times = pd.date_range(
            "2024-01-01", periods=n, freq=freq,
        )
        if trend == "up":
            closes = np.linspace(
                base_price, base_price + 2.0, n,
            )
        elif trend == "down":
            closes = np.linspace(
                base_price, base_price - 2.0, n,
            )
        else:
            closes = (
                base_price
                + np.sin(np.linspace(0, 4 * np.pi, n)) * 0.5
            )
        highs = closes + atr * np.random.rand(n)
        lows = closes - atr * np.random.rand(n)
        opens = closes + (np.random.rand(n) - 0.5) * atr
        return pd.DataFrame({
            "time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.random.randint(100, 1000, n),
        })

    return {
        "H1": _make_df(n_h1, "h"),
        "H4": _make_df(n_h4, "4h"),
        "D1": _make_df(n_d1, "D"),
    }


class TestV2TradeBotInit:
    """初期化テスト。"""

    def test_デフォルト設定で初期化(self):
        bot = V2TradeBot()
        assert bot.current_regime == MarketRegimeV2.QUIET
        assert bot.state.equity == 1_000_000.0

    def test_カスタム設定で初期化(self):
        config = V2BotConfig(min_confidence=0.7)
        bot = V2TradeBot(config)
        assert bot._config.min_confidence == 0.7


class TestV2TradeBotState:
    """状態管理テスト。"""

    def test_トレード結果_勝ち(self):
        bot = V2TradeBot()
        bot.update_trade_result(15.0)
        assert bot.state.consecutive_wins == 1
        assert bot.state.consecutive_losses == 0

    def test_トレード結果_負け(self):
        bot = V2TradeBot()
        bot.update_trade_result(-10.0)
        assert bot.state.consecutive_losses == 1
        assert bot.state.consecutive_wins == 0

    def test_連勝リセット(self):
        bot = V2TradeBot()
        bot.update_trade_result(10.0)
        bot.update_trade_result(10.0)
        assert bot.state.consecutive_wins == 2
        bot.update_trade_result(-5.0)
        assert bot.state.consecutive_wins == 0
        assert bot.state.consecutive_losses == 1

    def test_エクイティ更新(self):
        bot = V2TradeBot()
        bot.update_equity(1_100_000.0)
        assert bot.state.equity == 1_100_000.0
        assert bot.state.peak_equity == 1_100_000.0

    def test_リセット(self):
        bot = V2TradeBot()
        bot.update_trade_result(-10.0)
        bot.update_equity(900_000.0)
        bot.reset()
        assert bot.state.consecutive_losses == 0
        assert bot.state.equity == 1_000_000.0
        assert bot.current_regime == MarketRegimeV2.QUIET


class TestV2TradeBotSignal:
    """シグナル生成テスト。"""

    def test_データ未設定でNone(self):
        bot = V2TradeBot()
        result = bot.generate_signal(
            datetime(2024, 6, 15, 12, 0),
        )
        assert result is None

    def test_set_market_data実行可能(self):
        """set_market_data がエラーなく完了する。"""
        bot = V2TradeBot()
        data = _make_market_data()
        # エラーが出なければOK
        bot.set_market_data(data)
        assert bot._ctx_builder is not None

    def test_generate_signal_returns_signal_or_none(self):
        """generate_signalがSignalまたはNoneを返す。"""
        bot = V2TradeBot()
        data = _make_market_data(trend="up")
        bot.set_market_data(data)

        # 複数バーで試行（シグナルが出るかはデータ依存）
        results = []
        for hour in range(50, 100):
            ts = datetime(2024, 1, 1 + hour // 24, hour % 24)
            result = bot.generate_signal(ts)
            results.append(result)

        # NoneかSignalのみ
        for r in results:
            if r is not None:
                assert r.signal_type in (
                    SignalType.BUY,
                    SignalType.SELL,
                )
                assert 0 <= r.confidence <= 1
                assert r.stop_loss is not None
                assert r.take_profit is not None
                assert r.regime is not None
