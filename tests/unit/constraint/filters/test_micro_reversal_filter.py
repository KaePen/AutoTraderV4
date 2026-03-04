"""M1マイクロ反転フィルタのユニットテスト"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrader.constraint.filters.micro_reversal_filter import (
    MicroReversalConfig,
    MicroReversalFilter,
)
from autotrader.core.entities import SignalType


@pytest.fixture
def default_config() -> MicroReversalConfig:
    return MicroReversalConfig(enabled=True)


@pytest.fixture
def filter_obj(
    default_config: MicroReversalConfig,
) -> MicroReversalFilter:
    return MicroReversalFilter(default_config)


def _make_m1_row(
    bb_percent_b: float | None = 0.5,
    stoch_k: float | None = 50.0,
    atr_14: float | None = 0.10,
    close: float | None = 150.0,
) -> pd.Series:
    """テスト用M1データ行を生成"""
    data: dict = {
        "bb_percent_b": bb_percent_b,
        "stoch_k": stoch_k,
        "atr_14": atr_14,
        "close": close,
    }
    return pd.Series(data)


def _make_m1_df(
    n: int = 10,
    start_close: float = 149.5,
    end_close: float = 150.0,
) -> pd.DataFrame:
    """テスト用M1 DataFrameを生成"""
    closes = np.linspace(start_close, end_close, n)
    return pd.DataFrame(
        {
            "close": closes,
            "bb_percent_b": [0.5] * n,
            "stoch_k": [50.0] * n,
            "atr_14": [0.10] * n,
        }
    )


class TestMicroReversalFilterDisabled:
    """無効時のテスト"""

    def test_disabled_returns_no_filter(self) -> None:
        config = MicroReversalConfig(enabled=False)
        f = MicroReversalFilter(config)
        row = _make_m1_row()
        result = f.check(SignalType.BUY, row)
        assert result.should_filter is False

    def test_hold_direction_returns_no_filter(
        self,
        filter_obj: MicroReversalFilter,
    ) -> None:
        row = _make_m1_row()
        result = filter_obj.check(SignalType.HOLD, row)
        assert result.should_filter is False

    def test_none_row_returns_no_filter(
        self,
        filter_obj: MicroReversalFilter,
    ) -> None:
        result = filter_obj.check(SignalType.BUY, None)
        assert result.should_filter is False


class TestBBCheck:
    """BB %Bチェックのテスト"""

    def test_buy_bb_extreme_triggers(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(bb_percent_b=0.95)
        result = f.check(SignalType.BUY, row)
        assert result.bb_triggered is True

    def test_buy_bb_normal_no_trigger(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(bb_percent_b=0.50)
        result = f.check(SignalType.BUY, row)
        assert result.bb_triggered is False

    def test_sell_bb_extreme_triggers(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(bb_percent_b=0.05)
        result = f.check(SignalType.SELL, row)
        assert result.bb_triggered is True

    def test_sell_bb_normal_no_trigger(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(bb_percent_b=0.50)
        result = f.check(SignalType.SELL, row)
        assert result.bb_triggered is False


class TestStochCheck:
    """Stochastic Kチェックのテスト"""

    def test_buy_stoch_extreme(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            stoch_extreme=80.0,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(stoch_k=85.0)
        result = f.check(SignalType.BUY, row)
        assert result.stoch_triggered is True

    def test_sell_stoch_extreme(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            stoch_extreme=80.0,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(stoch_k=15.0)
        result = f.check(SignalType.SELL, row)
        assert result.stoch_triggered is True


class TestROCATRCheck:
    """ROC/ATRチェックのテスト"""

    def test_buy_roc_extreme(self) -> None:
        """BUY時、価格が上昇してATRの1.5倍超"""
        config = MicroReversalConfig(
            enabled=True,
            roc_atr_extreme=1.5,
            roc_lookback=5,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        # lookback=5: idx4→idx9の価格差が対象
        # linspace(149.50, 150.00, 10)の場合:
        #   idx4=149.722, idx9=150.00 → 差=0.278
        # ATR=0.10, ratio=2.78>1.5
        df = _make_m1_df(
            n=10,
            start_close=149.50,
            end_close=150.00,
        )
        row = df.iloc[9]
        result = f.check(
            SignalType.BUY,
            row,
            m1_df=df,
            m1_index=9,
        )
        assert result.roc_triggered is True

    def test_buy_roc_normal(self) -> None:
        """BUY時、価格変化がATRの1.5倍以下"""
        config = MicroReversalConfig(
            enabled=True,
            roc_atr_extreme=1.5,
            roc_lookback=5,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        # ATR=0.10, 価格変化=0.05 → ratio=0.5<1.5
        df = _make_m1_df(
            n=10,
            start_close=149.95,
            end_close=150.00,
        )
        row = df.iloc[9]
        result = f.check(
            SignalType.BUY,
            row,
            m1_df=df,
            m1_index=9,
        )
        assert result.roc_triggered is False

    def test_no_df_returns_false(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row()
        result = f.check(
            SignalType.BUY,
            row,
            m1_df=None,
            m1_index=None,
        )
        assert result.roc_triggered is False


class TestConsensusVoting:
    """合議制テスト"""

    def test_2_of_3_triggers_filter(self) -> None:
        """2/3でフィルタ発動"""
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            stoch_extreme=80.0,
            min_signals=2,
        )
        f = MicroReversalFilter(config)
        # BB=0.95(trigger), Stoch=85(trigger)
        row = _make_m1_row(
            bb_percent_b=0.95,
            stoch_k=85.0,
        )
        result = f.check(SignalType.BUY, row)
        assert result.should_filter is True
        assert result.signal_count == 2

    def test_1_of_3_no_filter(self) -> None:
        """1/3ではフィルタ発動しない"""
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            stoch_extreme=80.0,
            min_signals=2,
        )
        f = MicroReversalFilter(config)
        # BB=0.95(trigger), Stoch=50(no)
        row = _make_m1_row(
            bb_percent_b=0.95,
            stoch_k=50.0,
        )
        result = f.check(SignalType.BUY, row)
        assert result.should_filter is False
        assert result.signal_count == 1

    def test_3_of_3_triggers_filter(self) -> None:
        """3/3でもフィルタ発動"""
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            stoch_extreme=80.0,
            roc_atr_extreme=1.5,
            roc_lookback=5,
            min_signals=2,
        )
        f = MicroReversalFilter(config)
        # 全シグナルトリガー（価格差を大きく設定）
        df = _make_m1_df(
            n=10,
            start_close=149.50,
            end_close=150.00,
        )
        df["bb_percent_b"] = 0.95
        df["stoch_k"] = 85.0
        row = df.iloc[9]
        result = f.check(
            SignalType.BUY,
            row,
            m1_df=df,
            m1_index=9,
        )
        assert result.should_filter is True
        assert result.signal_count == 3

    def test_min_signals_3_requires_all(self) -> None:
        """min_signals=3では全一致が必要"""
        config = MicroReversalConfig(
            enabled=True,
            bb_extreme=0.90,
            stoch_extreme=80.0,
            min_signals=3,
        )
        f = MicroReversalFilter(config)
        # BB=0.95(trigger), Stoch=85(trigger), ROC無し
        row = _make_m1_row(
            bb_percent_b=0.95,
            stoch_k=85.0,
        )
        result = f.check(SignalType.BUY, row)
        assert result.should_filter is False
        assert result.signal_count == 2


class TestMissingData:
    """欠損データのテスト"""

    def test_nan_bb_no_trigger(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(bb_percent_b=float("nan"))
        result = f.check(SignalType.BUY, row)
        assert result.bb_triggered is False

    def test_none_stoch_no_trigger(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        row = _make_m1_row(stoch_k=None)
        result = f.check(SignalType.BUY, row)
        assert result.stoch_triggered is False

    def test_zero_atr_no_roc_trigger(self) -> None:
        config = MicroReversalConfig(
            enabled=True,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        df = _make_m1_df()
        df["atr_14"] = 0.0
        row = df.iloc[9]
        result = f.check(
            SignalType.BUY,
            row,
            m1_df=df,
            m1_index=9,
        )
        assert result.roc_triggered is False

    def test_insufficient_lookback_no_roc_trigger(
        self,
    ) -> None:
        """lookback分のデータがない場合"""
        config = MicroReversalConfig(
            enabled=True,
            roc_lookback=5,
            min_signals=1,
        )
        f = MicroReversalFilter(config)
        df = _make_m1_df(n=3)
        row = df.iloc[2]
        result = f.check(
            SignalType.BUY,
            row,
            m1_df=df,
            m1_index=2,
        )
        assert result.roc_triggered is False
