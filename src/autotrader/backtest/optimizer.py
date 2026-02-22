"""パラメータ最適化モジュール

複数のパラメータセットをテストして最適な設定を見つける。
過剰フィッティング防止のため、訓練期間と検証期間を分離。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
from autotrader.config import DEFAULT_SCORING, DEFAULT_TRADING_PARAMS
from autotrader.config.trading_params import get_preset
from autotrader.core.entities import Candle, Signal
from autotrader.core.enums import ExitReason, SignalType, Timeframe

logger = logging.getLogger(__name__)


@dataclass
class OptimizeConfig:
    """最適化パラメータ

    Note:
        デフォルト値は中央設定(DEFAULT_SCORING)から参照。
        最適化グリッドで上書き可能。
    """

    min_signals: int = 3
    adx_threshold: float = (
        DEFAULT_SCORING.adx_threshold - 10
    )
    rsi_oversold: float = DEFAULT_SCORING.rsi_oversold + 5
    rsi_overbought: float = (
        DEFAULT_SCORING.rsi_overbought - 5
    )
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0
    cooldown_bars: int = 4
    mtf_bonus: int = 2
    volume: float = 0.5
    # v3拡張: 勝率向上フィルター
    require_trend_align: bool = False
    bb_filter: bool = False
    stoch_confirm: bool = False
    macd_hist_filter: bool = False
    # v3.5拡張: シグナル品質フィルター
    di_direction_filter: bool = False
    atr_expansion_filter: bool = False
    atr_expansion_threshold: float = 1.0


@dataclass
class OptimizeResult:
    """最適化結果

    Attributes:
        config: 使用したパラメータ設定
        train: 訓練期間の結果
        valid: 検証期間の結果
        score: 総合スコア
    """

    config: OptimizeConfig
    train: dict[str, Any] = field(default_factory=dict)
    valid: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class OptimizedGenerator:
    """最適化シグナル生成器

    パラメータ最適化用の軽量シグナル生成器。

    Attributes:
        config: 最適化パラメータ
    """

    def __init__(self, config: OptimizeConfig) -> None:
        """初期化

        Args:
            config: 最適化パラメータ
        """
        self.config = config
        self._last_signal_bar = -999
        self._current_bar = 0
        self._higher_tf_data: pd.DataFrame | None = None

    def set_higher_tf_data(self, df: pd.DataFrame) -> None:
        """上位時間足データを設定

        Args:
            df: H4等の上位時間足データ
        """
        self._higher_tf_data = df

    def reset(self) -> None:
        """状態リセット"""
        self._last_signal_bar = -999
        self._current_bar = 0

    def get_higher_tf_trend(
        self, current_time: Any,
    ) -> str | None:
        """上位時間足のトレンドを取得

        Args:
            current_time: 現在時刻

        Returns:
            "up", "down", またはNone
        """
        if self._higher_tf_data is None:
            return None
        mask = self._higher_tf_data["time"] <= current_time
        if not mask.any():
            return None
        row = self._higher_tf_data[mask].iloc[-1]
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        close = row.get("close")
        if pd.isna(sma_20) or pd.isna(sma_50):
            return None
        if close > sma_20 > sma_50:
            return "up"
        elif close < sma_20 < sma_50:
            return "down"
        return None

    def generate(
        self,
        row: pd.Series,
        candle: Candle,
        symbol: str,
        timeframe: Timeframe,
    ) -> Signal | None:
        """シグナル生成

        Args:
            row: インジケーター付きデータ行
            candle: ローソク足データ
            symbol: 通貨ペア
            timeframe: 時間足

        Returns:
            シグナルまたはNone
        """
        self._current_bar += 1

        if self.config.cooldown_bars > 0:
            bars_since = (
                self._current_bar - self._last_signal_bar
            )
            if bars_since < self.config.cooldown_bars:
                return None

        rsi = row.get("rsi_14")
        macd = row.get("macd")
        macd_signal = row.get("macd_signal")
        sma_20 = row.get("sma_20")
        sma_50 = row.get("sma_50")
        atr = row.get("atr_14", 0.5)
        adx = row.get("adx", 25.0)

        if any(
            pd.isna(v)
            for v in [rsi, macd, sma_20, sma_50]
        ):
            return None

        if (
            adx is not None
            and not pd.isna(adx)
            and adx < self.config.adx_threshold
        ):
            return None

        buy_signals, sell_signals = 0, 0
        reasons: list[str] = []

        # RSI
        if rsi < self.config.rsi_oversold - 5:
            buy_signals += 2
            reasons.append(f"RSI過売({rsi:.1f})")
        elif rsi < self.config.rsi_oversold:
            buy_signals += 1
        elif rsi > self.config.rsi_overbought + 5:
            sell_signals += 2
            reasons.append(f"RSI過買({rsi:.1f})")
        elif rsi > self.config.rsi_overbought:
            sell_signals += 1

        # MACD
        if macd > macd_signal:
            buy_signals += 1
            if macd > 0:
                buy_signals += 1
        elif macd < macd_signal:
            sell_signals += 1
            if macd < 0:
                sell_signals += 1

        # トレンド
        if candle.close > sma_20 > sma_50:
            buy_signals += 1
        elif candle.close < sma_20 < sma_50:
            sell_signals += 1

        # ADX
        if adx and not pd.isna(adx) and adx > 25:
            if buy_signals > sell_signals:
                buy_signals += 1
            elif sell_signals > buy_signals:
                sell_signals += 1

        # ダイバージェンス
        if row.get("is_bullish_div", False):
            buy_signals += 3
            reasons.append("強気ダイバージェンス")
        if row.get("is_bearish_div", False):
            sell_signals += 3
            reasons.append("弱気ダイバージェンス")

        # MTF
        higher_trend = self.get_higher_tf_trend(
            candle.time
        )
        if (
            higher_trend == "up"
            and buy_signals > sell_signals
        ):
            buy_signals += self.config.mtf_bonus
        elif (
            higher_trend == "down"
            and sell_signals > buy_signals
        ):
            sell_signals += self.config.mtf_bonus

        min_signals = self.config.min_signals
        signal_margin = 1

        # --- v3フィルター ---
        # MTFトレンド一致必須
        if self.config.require_trend_align:
            if higher_trend is None:
                return None
            if buy_signals > sell_signals:
                if higher_trend != "up":
                    return None
            elif sell_signals > buy_signals:
                if higher_trend != "down":
                    return None

        # ボリンジャーバンドフィルター（過熱排除）
        if self.config.bb_filter:
            bb_pct = row.get("bb_pct")
            if bb_pct is not None and not pd.isna(bb_pct):
                if buy_signals > sell_signals:
                    # 買い: BB上端の極度過熱を排除
                    if bb_pct > 0.85:
                        return None
                elif sell_signals > buy_signals:
                    # 売り: BB下端の極度過売を排除
                    if bb_pct < 0.15:
                        return None

        # ストキャスティクス確認（極端ゾーン排除）
        if self.config.stoch_confirm:
            stoch_k = row.get("stoch_k")
            if (
                stoch_k is not None
                and not pd.isna(stoch_k)
            ):
                if buy_signals > sell_signals:
                    # 買い: 超過買い圏は避ける
                    if stoch_k > 80:
                        return None
                elif sell_signals > buy_signals:
                    # 売り: 超過売り圏は避ける
                    if stoch_k < 20:
                        return None

        # MACDヒストグラム方向一致
        if self.config.macd_hist_filter:
            macd_hist = row.get("macd_hist")
            if (
                macd_hist is not None
                and not pd.isna(macd_hist)
            ):
                if buy_signals > sell_signals:
                    if macd_hist < 0:
                        return None
                elif sell_signals > buy_signals:
                    if macd_hist > 0:
                        return None
        # --- v3.5フィルター ---
        # +DI/-DI 方向一致フィルター
        if self.config.di_direction_filter:
            plus_di = row.get("plus_di")
            minus_di = row.get("minus_di")
            if (
                plus_di is not None
                and minus_di is not None
                and not pd.isna(plus_di)
                and not pd.isna(minus_di)
            ):
                if buy_signals > sell_signals:
                    if plus_di <= minus_di:
                        return None
                elif sell_signals > buy_signals:
                    if minus_di <= plus_di:
                        return None

        # ATR拡大フィルター（トレンド発生中のみ）
        if self.config.atr_expansion_filter:
            atr_ratio = row.get("atr_ratio")
            if (
                atr_ratio is not None
                and not pd.isna(atr_ratio)
            ):
                thr = self.config.atr_expansion_threshold
                if atr_ratio < thr:
                    return None

        # --- v3フィルターここまで ---

        if (
            buy_signals >= min_signals
            and buy_signals > sell_signals + signal_margin
        ):
            self._last_signal_bar = self._current_bar
            confidence = min(buy_signals / 8, 1.0)
            from uuid import uuid4

            return Signal(
                signal_id=str(uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                signal_type=SignalType.BUY,
                confidence=confidence,
                stop_loss=(
                    candle.close
                    - atr * self.config.sl_atr_mult
                ),
                take_profit=(
                    candle.close
                    + atr * self.config.tp_atr_mult
                ),
                reasoning=(
                    ", ".join(reasons)
                    if reasons
                    else "買いシグナル"
                ),
                created_at=candle.time,
            )

        if (
            sell_signals >= min_signals
            and sell_signals > buy_signals + signal_margin
        ):
            self._last_signal_bar = self._current_bar
            confidence = min(sell_signals / 8, 1.0)
            from uuid import uuid4

            return Signal(
                signal_id=str(uuid4()),
                symbol=symbol,
                timeframe=timeframe,
                signal_type=SignalType.SELL,
                confidence=confidence,
                stop_loss=(
                    candle.close
                    + atr * self.config.sl_atr_mult
                ),
                take_profit=(
                    candle.close
                    - atr * self.config.tp_atr_mult
                ),
                reasoning=(
                    ", ".join(reasons)
                    if reasons
                    else "売りシグナル"
                ),
                created_at=candle.time,
            )

        return None


def _calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """テクニカル指標を計算

    Args:
        df: OHLCVデータ

    Returns:
        指標付きデータ
    """
    import pandas_ta as ta

    df["sma_20"] = ta.sma(df["close"], length=20)
    df["sma_50"] = ta.sma(df["close"], length=50)
    df["ema_12"] = ta.ema(df["close"], length=12)
    df["ema_26"] = ta.ema(df["close"], length=26)
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    macd = ta.macd(df["close"])
    if macd is not None:
        cols = macd.columns.tolist()
        macd_col = [
            c for c in cols
            if "MACD_" in c
            and "MACDs" not in c
            and "MACDh" not in c
        ][0]
        signal_col = [
            c for c in cols if "MACDs" in c
        ][0]
        df["macd"] = macd[macd_col]
        df["macd_signal"] = macd[signal_col]

    stoch = ta.stoch(
        df["high"], df["low"], df["close"], k=14, d=3
    )
    if stoch is not None:
        stoch_cols = stoch.columns.tolist()
        k_col = [
            c for c in stoch_cols if "STOCHk" in c
        ][0]
        df["stoch_k"] = stoch[k_col]

    df["atr_14"] = ta.atr(
        df["high"], df["low"], df["close"], length=14
    )

    adx = ta.adx(
        df["high"], df["low"], df["close"], length=14
    )
    if adx is not None:
        adx_col = [
            c for c in adx.columns if c.startswith("ADX")
        ][0]
        df["adx"] = adx[adx_col]
        # +DI / -DI (v3.5)
        dmp_cols = [
            c for c in adx.columns if "DMP" in c
        ]
        dmn_cols = [
            c for c in adx.columns if "DMN" in c
        ]
        if dmp_cols:
            df["plus_di"] = adx[dmp_cols[0]]
        if dmn_cols:
            df["minus_di"] = adx[dmn_cols[0]]

    # ATR拡大指標 (v3.5): 現在ATR / 50期間平均ATR
    if "atr_14" in df.columns:
        df["atr_ratio"] = (
            df["atr_14"] / df["atr_14"].rolling(50).mean()
        )

    # ボリンジャーバンド (v3)
    bb = ta.bbands(df["close"], length=20, std=2.0)
    if bb is not None:
        bb_cols = bb.columns.tolist()
        lower = [c for c in bb_cols if "BBL" in c][0]
        mid = [c for c in bb_cols if "BBM" in c][0]
        upper = [c for c in bb_cols if "BBU" in c][0]
        df["bb_lower"] = bb[lower]
        df["bb_mid"] = bb[mid]
        df["bb_upper"] = bb[upper]
        band_w = df["bb_upper"] - df["bb_lower"]
        df["bb_pct"] = (
            (df["close"] - df["bb_lower"]) / band_w
        ).clip(0, 1)

    # MACDヒストグラム (v3)
    if "macd" in df.columns and "macd_signal" in df.columns:
        df["macd_hist"] = df["macd"] - df["macd_signal"]

    return df


def _load_mt5_csv(file_path: Path) -> pd.DataFrame:
    """MT5形式CSVを読み込み

    Args:
        file_path: CSVファイルパス

    Returns:
        OHLCVデータ
    """
    df = pl.read_csv(
        file_path, separator="\t", has_header=True
    )
    columns = df.columns

    if "<TIME>" not in columns:
        df = df.rename({
            "<DATE>": "date",
            "<OPEN>": "open",
            "<HIGH>": "high",
            "<LOW>": "low",
            "<CLOSE>": "close",
            "<TICKVOL>": "volume",
        })
        df = df.with_columns(
            pl.col("date")
            .str.strptime(pl.Datetime, "%Y.%m.%d")
            .alias("time")
        )
    else:
        df = df.rename({
            "<DATE>": "date",
            "<TIME>": "time_str",
            "<OPEN>": "open",
            "<HIGH>": "high",
            "<LOW>": "low",
            "<CLOSE>": "close",
            "<TICKVOL>": "volume",
        })
        df = df.with_columns(
            pl.concat_str([
                pl.col("date"),
                pl.lit(" "),
                pl.col("time_str"),
            ]).alias("datetime_str")
        )
        df = df.with_columns(
            pl.col("datetime_str")
            .str.strptime(
                pl.Datetime, "%Y.%m.%d %H:%M:%S"
            )
            .alias("time")
        )

    df = df.select([
        "time", "open", "high", "low", "close", "volume"
    ])
    return df.to_pandas()


def run_backtest_period(
    df: pd.DataFrame,
    h4_df: pd.DataFrame,
    start_year: int,
    end_year: int,
    config: OptimizeConfig,
    symbol: str = "USDJPY",
    timeframe: Timeframe = Timeframe.H1,
    initial_balance: float = 1_000_000.0,
) -> dict:
    """期間指定バックテスト

    Args:
        df: H1データ（指標計算済み）
        h4_df: H4データ（指標計算済み）
        start_year: 開始年
        end_year: 終了年
        config: 最適化パラメータ
        symbol: 通貨ペア
        timeframe: 時間足
        initial_balance: 初期残高

    Returns:
        結果辞書
    """
    start_date = datetime(start_year, 1, 1)
    end_date = datetime(end_year + 1, 1, 1)

    period_df = df[
        (df["time"] >= start_date)
        & (df["time"] < end_date)
    ].reset_index(drop=True)

    if period_df.empty:
        return {
            "trades": 0,
            "win_rate": 0,
            "pf": 0,
            "net_profit": 0,
            "max_dd": 0,
        }

    generator = OptimizedGenerator(config)
    generator.set_higher_tf_data(h4_df)

    # シンボル別プリセット取得（GBPUSDなどの非JPYペア対応）
    preset = get_preset(symbol)
    # pip_unit: JPY建て=0.01、非JPY建て=0.0001
    pip_unit = 0.01 if symbol.endswith("JPY") else 0.0001
    simulator = TradeSimulator(
        config=SimulatorConfig(
            initial_balance=initial_balance,
            spread_pips=preset.spread_pips,
            pip_value=preset.pip_value,
            pip_unit=pip_unit,
            max_positions=1,
            default_volume=config.volume,
        )
    )

    last_candle = None

    # numpy配列ベースのループ
    from autotrader.backtest.candle_arrays import CandleArrays
    arrays = CandleArrays.from_dataframe(period_df)
    for i in range(arrays.n_rows):
        candle = arrays.get_candle(i, symbol, timeframe)
        last_candle = candle
        row = period_df.iloc[i]
        signal = generator.generate(
            row, candle, symbol, timeframe
        )
        simulator.process_candle(candle, signal)

    if last_candle:
        simulator.force_close_all(
            last_candle, ExitReason.FORCE_CLOSE
        )

    trades = simulator.get_closed_trades()
    equity_history = simulator.state.daily_pnl
    calculator = MetricsCalculator(
        initial_balance=initial_balance
    )
    metrics = calculator.calculate(trades, equity_history)

    return {
        "trades": len(trades),
        "win_rate": metrics.win_rate * 100,
        "pf": metrics.profit_factor,
        "net_profit": (
            simulator.state.balance - initial_balance
        ),
        "max_dd": metrics.max_drawdown_pct * 100,
    }


def get_default_param_grid() -> list[OptimizeConfig]:
    """デフォルトパラメータグリッドを取得

    Returns:
        パラメータ設定リスト
    """
    return [
        # オリジナル設定
        OptimizeConfig(
            min_signals=3, adx_threshold=15.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4,
        ),
        # 勝率重視（TP短め）
        OptimizeConfig(
            min_signals=3, adx_threshold=15.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=2.0,
            cooldown_bars=4,
        ),
        # 勝率重視（SL広め）
        OptimizeConfig(
            min_signals=3, adx_threshold=15.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.5, tp_atr_mult=2.5,
            cooldown_bars=4,
        ),
        # 高品質シグナルのみ
        OptimizeConfig(
            min_signals=4, adx_threshold=20.0,
            rsi_oversold=30.0, rsi_overbought=70.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=6,
        ),
        # ADX厳格
        OptimizeConfig(
            min_signals=3, adx_threshold=22.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4,
        ),
        # クールダウン短め
        OptimizeConfig(
            min_signals=3, adx_threshold=15.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=2,
        ),
        # リスクリワード改善
        OptimizeConfig(
            min_signals=3, adx_threshold=18.0,
            rsi_oversold=32.0, rsi_overbought=68.0,
            sl_atr_mult=1.5, tp_atr_mult=2.5,
            cooldown_bars=4,
        ),
        # MTFボーナス増加
        OptimizeConfig(
            min_signals=3, adx_threshold=15.0,
            rsi_oversold=35.0, rsi_overbought=65.0,
            sl_atr_mult=2.0, tp_atr_mult=3.0,
            cooldown_bars=4, mtf_bonus=3,
        ),
        # バランス型
        OptimizeConfig(
            min_signals=3, adx_threshold=18.0,
            rsi_oversold=33.0, rsi_overbought=67.0,
            sl_atr_mult=1.8, tp_atr_mult=2.7,
            cooldown_bars=3,
        ),
    ]


def _run_single_config(
    args: tuple,
) -> tuple[int, dict, dict]:
    """単一パラメータ設定のバックテスト（並列実行用）

    Args:
        args: (idx, config, df, h4_df, train_years,
               valid_years, symbol)

    Returns:
        (idx, train_result, valid_result) のタプル
    """
    (
        idx, config, df, h4_df,
        train_years, valid_years, symbol,
    ) = args
    train = run_backtest_period(
        df, h4_df,
        train_years[0], train_years[1], config, symbol,
    )
    valid = run_backtest_period(
        df, h4_df,
        valid_years[0], valid_years[1], config, symbol,
    )
    return idx, train, valid


def run_optimization(
    data_dir: str = "data",
    symbol: str = "USDJPY",
    train_years: tuple[int, int] = (2010, 2019),
    valid_years: tuple[int, int] = (2020, 2025),
    param_grid: list[OptimizeConfig] | None = None,
    max_workers: int = 1,
) -> list[OptimizeResult]:
    """パラメータ最適化を実行

    Args:
        data_dir: データ基底ディレクトリ（通貨ペアサブディレクトリの親）
        symbol: 通貨ペア
        train_years: 訓練期間 (開始年, 終了年)
        valid_years: 検証期間 (開始年, 終了年)
        param_grid: パラメータグリッド

    Returns:
        最適化結果リスト（スコア降順）
    """
    print("=" * 80)
    print("AutoTraderV4 戦略最適化（過剰フィッティング防止）")
    print("=" * 80)

    # 通貨ペア別サブディレクトリに解決
    data_path = Path(data_dir) / symbol
    h1_files = list(data_path.glob(f"{symbol}_H1_*.csv"))
    h4_files = list(data_path.glob(f"{symbol}_H4_*.csv"))

    if not h1_files or not h4_files:
        print("H1/H4データが見つかりません")
        return []

    print("データ読み込み中...")
    df = _load_mt5_csv(h1_files[0])
    df = _calculate_indicators(df)
    h4_df = _load_mt5_csv(h4_files[0])
    h4_df = _calculate_indicators(h4_df)
    print(f"H1: {len(df):,}, H4: {len(h4_df):,} レコード")

    print(
        f"\n訓練期間: {train_years[0]}-{train_years[1]}"
    )
    print(
        f"検証期間: {valid_years[0]}-{valid_years[1]}"
    )
    print("-" * 80)

    if param_grid is None:
        param_grid = get_default_param_grid()

    results: list[OptimizeResult] = []

    print("\n最適化実行中...")
    if max_workers > 1:
        print(f"  並列実行: {max_workers}コア / {len(param_grid)}設定")
    header = (
        f"{'#':<4} {'min_sig':<8} {'ADX':<6} "
        f"{'RSI':<10} {'SL/TP':<10} {'CD':<4} | "
        f"{'勝率':>8} {'PF':>8} {'利益':>14} | "
        f"{'勝率':>8} {'PF':>8} {'利益':>14}"
    )
    print(header)
    sub_header = (
        f"{'':4} {'':8} {'':6} {'':10} {'':10} {'':4}"
        f" | {'(訓練)':>8} {'':>8} {'':>14}"
        f" | {'(検証)':>8} {'':>8} {'':>14}"
    )
    print(sub_header)
    print("-" * 120)

    # 並列or逐次で各パラメータを評価
    raw_results: list[tuple[int, dict, dict]] = []

    if max_workers > 1:
        import concurrent.futures

        task_args = [
            (
                i, cfg, df, h4_df,
                train_years, valid_years, symbol,
            )
            for i, cfg in enumerate(param_grid)
        ]
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers
        ) as executor:
            for res in executor.map(
                _run_single_config, task_args
            ):
                raw_results.append(res)
        raw_results.sort(key=lambda x: x[0])
    else:
        for i, cfg in enumerate(param_grid):
            tr = run_backtest_period(
                df, h4_df,
                train_years[0], train_years[1],
                cfg, symbol,
            )
            vr = run_backtest_period(
                df, h4_df,
                valid_years[0], valid_years[1],
                cfg, symbol,
            )
            raw_results.append((i, tr, vr))

    for i, train_result, valid_result in raw_results:
        config = param_grid[i]

        # スコア計算
        score = 0.0
        valid = valid_result
        if valid["net_profit"] > 0 and valid["pf"] > 0:
            score = (
                (valid["win_rate"] / 100)
                * valid["pf"]
                * (valid["net_profit"] / 100000)
            )

        opt_result = OptimizeResult(
            config=config,
            train=train_result,
            valid=valid_result,
            score=score,
        )
        results.append(opt_result)

        print(
            f"{i+1:<4} "
            f"{config.min_signals:<8} "
            f"{config.adx_threshold:<6.0f} "
            f"{config.rsi_oversold:.0f}"
            f"/{config.rsi_overbought:.0f}  "
            f"{config.sl_atr_mult:.1f}"
            f"/{config.tp_atr_mult:.1f}  "
            f"{config.cooldown_bars:<4} | "
            f"{train_result['win_rate']:>7.1f}% "
            f"{train_result['pf']:>7.2f} "
            f"${train_result['net_profit']:>+12,.0f} | "
            f"{valid_result['win_rate']:>7.1f}% "
            f"{valid_result['pf']:>7.2f} "
            f"${valid_result['net_profit']:>+12,.0f}"
        )

    # スコア降順ソート
    results.sort(key=lambda x: x.score, reverse=True)

    # ベスト表示
    print("\n" + "=" * 80)
    print("検証期間でのベスト設定:")
    print("-" * 80)

    for i, r in enumerate(results[:5]):
        c = r.config
        v = r.valid
        print(
            f"#{i+1}: min_sig={c.min_signals}, "
            f"ADX>{c.adx_threshold}, "
            f"RSI={c.rsi_oversold}/{c.rsi_overbought}, "
            f"SL/TP={c.sl_atr_mult}/{c.tp_atr_mult}, "
            f"CD={c.cooldown_bars}"
        )
        print(
            f"     検証: 勝率{v['win_rate']:.1f}%, "
            f"PF={v['pf']:.2f}, "
            f"利益${v['net_profit']:+,.0f}, "
            f"最大DD={v['max_dd']:.2f}%"
        )
        print()

    return results
