"""実スプレッドデータ読み込みのテスト"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import polars as pl

from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.simulator import SimulatorConfig
from autotrader.core.entities import Candle


class TestDataLoaderSpread:
    """DataLoaderのスプレッドデータ読み込みテスト"""

    def test_load_mt5_csv_with_spread(self) -> None:
        """MT5形式CSVからスプレッドデータを読み込む"""
        with TemporaryDirectory() as tmpdir:
            # MT5形式のテストCSVを作成
            csv_content = """<DATE>	<TIME>	<OPEN>	<HIGH>	<LOW>	<CLOSE>	<TICKVOL>	<VOL>	<SPREAD>
2024.01.15	00:00:00	150.000	150.100	149.900	150.050	1000	0	12
2024.01.15	00:01:00	150.050	150.150	150.000	150.100	1200	0	15
2024.01.15	00:02:00	150.100	150.200	150.050	150.150	800	0	10
"""
            csv_path = Path(tmpdir) / "USDJPY_M1.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            # 読み込み
            df = DataLoader.load_mt5_csv(csv_path)

            # スプレッド列が存在することを確認
            assert "spread_points" in df.columns
            assert len(df) == 3

            # スプレッド値の確認
            assert df.iloc[0]["spread_points"] == 12
            assert df.iloc[1]["spread_points"] == 15
            assert df.iloc[2]["spread_points"] == 10

    def test_load_mt5_csv_without_spread(self) -> None:
        """スプレッド列がないMT5形式CSV"""
        with TemporaryDirectory() as tmpdir:
            # スプレッド列なしのCSV
            csv_content = """<DATE>	<TIME>	<OPEN>	<HIGH>	<LOW>	<CLOSE>	<TICKVOL>	<VOL>
2024.01.15	00:00:00	150.000	150.100	149.900	150.050	1000	0
"""
            csv_path = Path(tmpdir) / "USDJPY_M1.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            df = DataLoader.load_mt5_csv(csv_path)

            # スプレッド列がないことを確認
            assert "spread_points" not in df.columns

    def test_normalize_columns_with_spread(self) -> None:
        """列名正規化でスプレッド列をマッピング"""
        loader = DataLoader()

        # 様々なスプレッド列名をテスト
        test_cases = [
            (["time", "open", "high", "low", "close", "volume", "spread"],
             ["time", "open", "high", "low", "close", "volume", "spread_points"]),
            (["time", "open", "high", "low", "close", "volume", "SPREAD"],
             ["time", "open", "high", "low", "close", "volume", "spread_points"]),
            (["time", "open", "high", "low", "close", "volume", "spread_points"],
             ["time", "open", "high", "low", "close", "volume", "spread_points"]),
        ]

        for input_cols, expected_cols in test_cases:
            df = pl.DataFrame({col: [1.0] for col in input_cols})
            result = loader._normalize_columns(df)

            for expected_col in expected_cols:
                assert expected_col in result.columns, (
                    f"Expected {expected_col} in columns, "
                    f"got {result.columns}"
                )


class TestSimulatorActualSpread:
    """Simulatorの実スプレッドデータ使用テスト"""

    def test_get_spread_from_row_data(self) -> None:
        """row_dataから実スプレッドを取得"""
        from autotrader.backtest.simulator import TradeSimulator

        config = SimulatorConfig(
            spread_pips=1.5,  # デフォルトスプレッド
            pip_unit=0.01,
            use_actual_spread_data=True,  # 実スプレッドを使用
        )
        simulator = TradeSimulator(config)

        # テスト用Candle
        candle = Candle(
            time=datetime(2024, 1, 15, 10, 0, 0),
            open=150.0,
            high=150.1,
            low=149.9,
            close=150.05,
            volume=1000,
        )

        # 実スプレッドデータを含むrow_data
        row_data = {"spread_points": 15}

        spread = simulator._get_spread_for_candle(candle, row_data)

        # 15 points * 0.01 / 10 = 0.015 (価格単位)
        expected_spread = 15 * 0.01 / 10
        assert spread == pytest.approx(expected_spread, abs=0.0001)

    def test_fallback_to_session_spread(self) -> None:
        """row_dataがない場合はセッション別スプレッド"""
        from autotrader.backtest.simulator import TradeSimulator

        config = SimulatorConfig(
            spread_pips=1.5,
            pip_unit=0.01,
            use_actual_spread_data=True,
            use_session_spread=True,  # セッション別スプレッドも有効
            session_spreads={
                "tokyo": 1.2,
                "london": 1.0,
                "new_york": 1.2,
                "off_hours": 2.5,
            },
        )
        simulator = TradeSimulator(config)

        # LONDON時間のCandle（UTC 10:00）
        candle = Candle(
            time=datetime(2024, 1, 15, 10, 0, 0),
            open=150.0,
            high=150.1,
            low=149.9,
            close=150.05,
            volume=1000,
        )

        # row_dataなし
        spread = simulator._get_spread_for_candle(candle, None)

        # セッション別スプレッドを使用
        # UTC 10:00 はLONDONセッション（1.0 pips）
        expected_spread = 1.0 * 0.01
        assert spread == pytest.approx(expected_spread, abs=0.0001)

    def test_fallback_to_fixed_spread(self) -> None:
        """両方無効の場合は固定スプレッド"""
        from autotrader.backtest.simulator import TradeSimulator

        config = SimulatorConfig(
            spread_pips=1.5,
            pip_unit=0.01,
            use_actual_spread_data=False,  # 実スプレッド無効
            use_session_spread=False,  # セッション別も無効
        )
        simulator = TradeSimulator(config)

        candle = Candle(
            time=datetime(2024, 1, 15, 10, 0, 0),
            open=150.0,
            high=150.1,
            low=149.9,
            close=150.05,
            volume=1000,
        )

        spread = simulator._get_spread_for_candle(candle, None)

        # 固定スプレッドを使用
        expected_spread = 1.5 * 0.01
        assert spread == pytest.approx(expected_spread, abs=0.0001)

    def test_row_data_without_spread_key(self) -> None:
        """row_dataにspread_pointsがない場合"""
        from autotrader.backtest.simulator import TradeSimulator

        config = SimulatorConfig(
            spread_pips=1.5,
            pip_unit=0.01,
            use_actual_spread_data=True,
            use_session_spread=False,
        )
        simulator = TradeSimulator(config)

        candle = Candle(
            time=datetime(2024, 1, 15, 10, 0, 0),
            open=150.0,
            high=150.1,
            low=149.9,
            close=150.05,
            volume=1000,
        )

        # spread_pointsなしのrow_data
        row_data = {"other_field": 123}

        spread = simulator._get_spread_for_candle(candle, row_data)

        # 固定スプレッドにフォールバック
        expected_spread = 1.5 * 0.01
        assert spread == pytest.approx(expected_spread, abs=0.0001)

    def test_row_data_with_zero_spread(self) -> None:
        """spread_pointsが0の場合"""
        from autotrader.backtest.simulator import TradeSimulator

        config = SimulatorConfig(
            spread_pips=1.5,
            pip_unit=0.01,
            use_actual_spread_data=True,
            use_session_spread=False,
        )
        simulator = TradeSimulator(config)

        candle = Candle(
            time=datetime(2024, 1, 15, 10, 0, 0),
            open=150.0,
            high=150.1,
            low=149.9,
            close=150.05,
            volume=1000,
        )

        # spread_points=0のrow_data
        row_data = {"spread_points": 0}

        spread = simulator._get_spread_for_candle(candle, row_data)

        # 0は無効値としてフォールバック
        expected_spread = 1.5 * 0.01
        assert spread == pytest.approx(expected_spread, abs=0.0001)
