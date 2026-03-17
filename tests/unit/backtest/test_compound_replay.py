"""compound_replayのユニットテスト"""

from __future__ import annotations

import pandas as pd
import pytest

from autotrader.backtest.compound_replay import (
    CompoundReplayConfig,
    CompoundReplayResult,
    replay_compound,
)


def _make_trades(
    rows: list[dict],
) -> pd.DataFrame:
    """テスト用トレードDF生成"""
    defaults = {
        "symbol": "USDJPY",
        "lot": 0.10,
        "sl_pips": 30.0,
        "pips": 10.0,
        "profit_loss": 1000.0,
    }
    data = []
    for r in rows:
        row = {**defaults, **r}
        data.append(row)
    return pd.DataFrame(data)


class TestCompoundReplay:
    """compound replay基本テスト"""

    def test_empty_trades(self) -> None:
        """空のトレード"""
        df = pd.DataFrame()
        result = replay_compound(df)
        assert result.total_trades == 0
        assert result.final_equity == 1_000_000.0

    def test_single_winning_trade(self) -> None:
        """1件の勝ちトレード"""
        df = _make_trades([{
            "entry_time": "2023-01-15 10:00",
            "exit_time": "2023-01-15 14:00",
            "lot": 0.10,
            "pips": 20.0,
            "profit_loss": 2000.0,
        }])
        result = replay_compound(df)
        assert result.total_trades == 1
        assert result.win_rate == 100.0
        # 初期100万、scale=1.0、PnL=2000
        assert result.final_equity == 1_002_000.0

    def test_compound_growth(self) -> None:
        """複利でロットが増加する"""
        # 2トレード: 1st → 利益でequity増加 → 2nd大きいロット
        df = _make_trades([
            {
                "entry_time": "2023-01-10 10:00",
                "exit_time": "2023-01-10 14:00",
                "lot": 0.10,
                "profit_loss": 100_000.0,  # 10%利益
            },
            {
                "entry_time": "2023-01-20 10:00",
                "exit_time": "2023-01-20 14:00",
                "lot": 0.10,
                "profit_loss": 100_000.0,
            },
        ])
        result = replay_compound(df)
        assert result.total_trades == 2
        # 1st: scale=1.0, lot=0.10, pnl=100,000
        # equity = 1,100,000
        # 2nd: scale=1.1, lot=0.11, pnl=110,000
        # equity = 1,210,000
        assert result.trades_df is not None
        lots = result.trades_df["replay_lot"].tolist()
        assert lots[0] == 0.10
        assert abs(lots[1] - 0.11) < 0.005
        assert abs(
            result.final_equity - 1_210_000
        ) < 1000

    def test_losing_trades_reduce_lot(self) -> None:
        """損失でロットが縮小する"""
        df = _make_trades([
            {
                "entry_time": "2023-01-10 10:00",
                "exit_time": "2023-01-10 14:00",
                "lot": 0.10,
                "profit_loss": -100_000.0,  # -10%
            },
            {
                "entry_time": "2023-01-20 10:00",
                "exit_time": "2023-01-20 14:00",
                "lot": 0.10,
                "profit_loss": -100_000.0,
            },
        ])
        result = replay_compound(df)
        lots = result.trades_df["replay_lot"].tolist()
        # 1st: scale=1.0, lot=0.10
        # equity=900,000 → 2nd: scale=0.9, lot=0.09
        assert lots[0] == 0.10
        assert abs(lots[1] - 0.09) < 0.005

    def test_max_drawdown(self) -> None:
        """ドローダウン計算"""
        df = _make_trades([
            {
                "entry_time": "2023-01-10 10:00",
                "exit_time": "2023-01-10 14:00",
                "lot": 0.10,
                "profit_loss": 200_000.0,
            },
            {
                "entry_time": "2023-01-20 10:00",
                "exit_time": "2023-01-20 14:00",
                "lot": 0.10,
                "profit_loss": -100_000.0,
            },
        ])
        result = replay_compound(df)
        # peak=1,200,000 → -100k(scale=1.2)=-120k
        # equity=1,080,000 → DD=(1.2M-1.08M)/1.2M*100
        assert result.max_drawdown_pct > 0

    def test_yearly_details(self) -> None:
        """年別サマリが生成される"""
        df = _make_trades([
            {
                "entry_time": "2023-06-01 10:00",
                "exit_time": "2023-06-01 14:00",
                "profit_loss": 50_000.0,
            },
            {
                "entry_time": "2024-06-01 10:00",
                "exit_time": "2024-06-01 14:00",
                "profit_loss": 60_000.0,
            },
        ])
        result = replay_compound(df)
        assert len(result.yearly_details) == 2
        assert result.yearly_details[0]["year"] == 2023
        assert result.yearly_details[1]["year"] == 2024

    def test_pair_details(self) -> None:
        """ペア別サマリが生成される"""
        df = _make_trades([
            {
                "entry_time": "2023-01-10 10:00",
                "exit_time": "2023-01-10 14:00",
                "symbol": "USDJPY",
                "profit_loss": 1000.0,
            },
            {
                "entry_time": "2023-01-20 10:00",
                "exit_time": "2023-01-20 14:00",
                "symbol": "EURJPY",
                "profit_loss": 2000.0,
            },
        ])
        result = replay_compound(df)
        assert len(result.pair_details) == 2
        symbols = [p["symbol"] for p in result.pair_details]
        assert "USDJPY" in symbols
        assert "EURJPY" in symbols

    def test_lot_capped_by_max_lot(self) -> None:
        """max_lotでキャップされる"""
        config = CompoundReplayConfig(
            initial_equity=1_000_000.0,
            max_lot=0.15,
        )
        df = _make_trades([
            {
                "entry_time": "2023-01-10 10:00",
                "exit_time": "2023-01-10 14:00",
                "lot": 0.50,  # 大きなロット
                "profit_loss": 50_000.0,
            },
        ])
        result = replay_compound(df, config)
        assert result.trades_df["replay_lot"].iloc[0] <= 0.15

    def test_lot_floored_by_min_lot(self) -> None:
        """min_lotで下限保護される"""
        config = CompoundReplayConfig(
            initial_equity=1_000_000.0,
            min_lot=0.05,
        )
        # エクイティが大幅減少してもmin_lot以下にならない
        df = _make_trades([
            {
                "entry_time": "2023-01-10 10:00",
                "exit_time": "2023-01-10 14:00",
                "lot": 0.01,
                "profit_loss": -900_000.0,
            },
            {
                "entry_time": "2023-01-20 10:00",
                "exit_time": "2023-01-20 14:00",
                "lot": 0.01,
                "profit_loss": 1000.0,
            },
        ])
        result = replay_compound(df, config)
        assert (
            result.trades_df["replay_lot"].iloc[1]
            >= 0.05
        )

    def test_monthly_plus_rate(self) -> None:
        """月間勝率が正しく計算される"""
        df = _make_trades([
            {
                "entry_time": "2023-01-15 10:00",
                "exit_time": "2023-01-15 14:00",
                "profit_loss": 10_000.0,
            },
            {
                "entry_time": "2023-02-15 10:00",
                "exit_time": "2023-02-15 14:00",
                "profit_loss": -5_000.0,
            },
            {
                "entry_time": "2023-03-15 10:00",
                "exit_time": "2023-03-15 14:00",
                "profit_loss": 8_000.0,
            },
        ])
        result = replay_compound(df)
        # 3ヶ月中2ヶ月がプラス = 66.7%
        assert abs(result.monthly_plus_rate - 66.7) < 0.1

    def test_custom_initial_equity(self) -> None:
        """カスタム初期資金"""
        config = CompoundReplayConfig(
            initial_equity=500_000.0,
        )
        df = _make_trades([
            {
                "entry_time": "2023-01-15 10:00",
                "exit_time": "2023-01-15 14:00",
                "lot": 0.10,
                "profit_loss": 50_000.0,
            },
        ])
        result = replay_compound(df, config)
        assert result.initial_equity == 500_000.0
        assert result.final_equity == 550_000.0

    def test_missing_column_raises(self) -> None:
        """必須列がない場合はエラー"""
        df = pd.DataFrame({"foo": [1]})
        with pytest.raises(ValueError, match="必須列"):
            replay_compound(df)
