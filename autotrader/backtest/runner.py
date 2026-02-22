"""バックテストランナーモジュール

統合バックテスト実行を管理。
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed,
)
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from autotrader.backtest.candle_arrays import CandleArrays
from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.engine import (
    ParallelMultiTFBacktestEngine,
    ParallelEngineConfig,
)
from autotrader.backtest.events import (
    BacktestEventEmitter,
    ConsoleEventListener,
)
from autotrader.backtest.file_listener import FileEventListener
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.position_event_logger import (
    PositionEventLogger,
)
from autotrader.backtest.simulator import TradeSimulator, SimulatorConfig
from autotrader.backtest.strategy_factory import StrategyFactory
from autotrader.calculator.features.divergence_features import (
    DivergenceDetector,
)
from autotrader.config import DEFAULT_TRADING_PARAMS
from autotrader.config.trading_params import get_preset
from autotrader.core.entities import Candle, Signal
from autotrader.core.enums import (
    ExitReason,
    SignalType,
    Timeframe,
)
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)


@dataclass
class BacktestConfig:
    """バックテスト設定

    Attributes:
        symbol: シンボル
        timeframe: 時間足
        initial_balance: 初期残高
        volume: 取引ボリューム
        max_positions: 最大ポジション数（通常時）
        spread_pips: スプレッド（pips）
        pip_value: pip価値
        bonus_max_positions: 高品質シグナル時に追加するポジション数（0=無効）
        bonus_score_threshold: bonus発動のconsensus_score閾値
    """

    symbol: str = "USDJPY"
    timeframe: str = "H1"
    initial_balance: float = 1_000_000.0
    volume: float | None = None  # None時は戦略デフォルト
    max_positions: int = 2
    # 高品質シグナル時に追加する枠数（0=無効）
    bonus_max_positions: int = 1
    # bonus発動のconsensus_score閾値
    bonus_score_threshold: float = 7.0
    spread_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.spread_pips
    )
    slippage_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.slippage_pips
    )
    pip_value: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.pip_value
    )
    commission_per_lot: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.commission_per_lot
    )
    use_session_spread: bool = False

    @classmethod
    def from_preset(
        cls,
        symbol: str,
        preset_path: Path | None = None,
        **overrides: Any,
    ) -> "BacktestConfig":
        """シンボルプリセットから BacktestConfig を生成.

        プリセット値をデフォルトとして使用し、
        overrides で任意フィールドを上書きできる。

        Args:
            symbol: 通貨ペア名
            preset_path: YAMLファイルパス（None時はデフォルトパス）
            **overrides: 上書きするフィールド

        Returns:
            BacktestConfig: プリセット値で初期化した設定
        """
        preset = get_preset(symbol, preset_path)
        kwargs: dict[str, Any] = {
            "symbol": symbol,
            "spread_pips": preset.spread_pips,
            "slippage_pips": preset.slippage_pips,
            "pip_value": preset.pip_value,
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
        }
        kwargs.update(overrides)
        return cls(**kwargs)


@dataclass
class BacktestResult:
    """バックテスト結果

    Attributes:
        trades: 取引数
        win_rate: 勝率（%）
        profit_factor: プロフィットファクター
        net_profit: 純利益
        max_drawdown: 最大ドローダウン（%）
        sharpe_ratio: シャープレシオ
        annual_return: 年間収益率（%）
        non_loss_rate: 非敗率（%）
        monthly_results: 月別結果
    """

    trades: int = 0
    win_rate: float = 0.0
    non_loss_rate: float = 0.0
    profit_factor: float = 0.0
    net_profit: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    annual_return: float = 0.0
    monthly_results: list[dict[str, Any]] = field(default_factory=list)
    yearly_results: list[dict[str, Any]] = field(default_factory=list)


class BacktestRunner:
    """バックテストランナー

    戦略のバックテストを統一的に実行。
    """

    def __init__(
        self,
        data_dir: str | Path = "data",
        config: BacktestConfig | None = None,
        verbose: bool = True,
        log_to_file: bool = True,
        log_dir: str | Path = "logs/backtest_log",
    ) -> None:
        """初期化

        Args:
            data_dir: データ基底ディレクトリ（通貨ペアサブディレクトリの親）
            config: バックテスト設定
            verbose: 詳細ログ出力
            log_to_file: ファイルログ出力
            log_dir: ログ出力先ディレクトリ
        """
        self.config = config or BacktestConfig()
        # 通貨ペア別サブディレクトリに解決
        # 例: data/USDJPY/ (data_dir="data", symbol="USDJPY")
        _base = Path(data_dir)
        self.data_dir = _base / self.config.symbol
        self._h1_df: pd.DataFrame | None = None
        self._h4_df: pd.DataFrame | None = None
        self._h8_df: pd.DataFrame | None = None
        self._d1_df: pd.DataFrame | None = None
        self._m15_df: pd.DataFrame | None = None
        self._m30_df: pd.DataFrame | None = None
        self._m1_df: pd.DataFrame | None = None
        self._m5_df: pd.DataFrame | None = None
        self._cancel_callback: Callable[[], bool] | None = None

        # イベントエミッター初期化
        self._emitter = BacktestEventEmitter()
        if verbose:
            self._emitter.add_listener(ConsoleEventListener(verbose=True))

        # ファイルログリスナー追加
        self._file_listener: FileEventListener | None = None
        if log_to_file:
            self._file_listener = FileEventListener(
                log_dir=log_dir, verbose=verbose
            )
            self._emitter.add_listener(self._file_listener)

    def set_cancel_callback(self, callback: Callable[[], bool]) -> None:
        """キャンセルコールバックを設定

        Args:
            callback: キャンセル状態を返すコールバック関数
        """
        self._cancel_callback = callback

    def get_log_path(self) -> Path | None:
        """ログファイルパスを取得

        Returns:
            ログファイルパス（ログ出力無効時はNone）
        """
        if self._file_listener:
            return self._file_listener.get_log_path()
        return None

    def _check_cancel_requested(self) -> bool:
        """キャンセルがリクエストされたかチェック

        Returns:
            bool: キャンセルされた場合True
        """
        if self._cancel_callback is None:
            return False
        return self._cancel_callback()

    def load_data(
        self,
        on_tf_loaded: "Callable[[str, int, int], None] | None" = None,
    ) -> None:
        """データを読み込み

        Args:
            on_tf_loaded: TFロード完了コールバック(tf名, 完了数, 全数)。
                各タイムフレームのインジケータ計算後に呼ばれ、UIへの進捗通知に使用。
        """
        symbol = self.config.symbol
        tf = self.config.timeframe
        _total = 4  # H1, H4, D1, M15
        _loaded = 0

        # メイン時間足
        main_files = list(self.data_dir.glob(f"{symbol}_{tf}_*.csv"))
        if not main_files:
            # H1、M15などのパターン
            main_files = list(self.data_dir.glob(f"{symbol}_H1_*.csv"))
        if main_files:
            self._h1_df = DataLoader.load_mt5_csv(main_files[0])
            self._h1_df = self._calculate_indicators(self._h1_df)
        _loaded += 1
        if on_tf_loaded:
            on_tf_loaded("H1", _loaded, _total)

        # 上位足（H4）
        h4_files = list(self.data_dir.glob(f"{symbol}_H4_*.csv"))
        if h4_files:
            self._h4_df = DataLoader.load_mt5_csv(h4_files[0])
            self._h4_df = self._calculate_indicators(self._h4_df)
        _loaded += 1
        if on_tf_loaded:
            on_tf_loaded("H4", _loaded, _total)

        # 日足
        d1_files = list(self.data_dir.glob(f"{symbol}_Daily_*.csv"))
        if not d1_files:
            d1_files = list(self.data_dir.glob(f"{symbol}_D1_*.csv"))
        if d1_files:
            self._d1_df = DataLoader.load_mt5_csv(d1_files[0])
            self._d1_df = self._calculate_indicators(self._d1_df)
        _loaded += 1
        if on_tf_loaded:
            on_tf_loaded("D1", _loaded, _total)

        # M15（マルチ戦略用）
        m15_files = list(self.data_dir.glob(f"{symbol}_M15_*.csv"))
        if m15_files:
            self._m15_df = DataLoader.load_mt5_csv(m15_files[0])
            self._m15_df = self._calculate_indicators(self._m15_df)
        _loaded += 1
        if on_tf_loaded:
            on_tf_loaded("M15", _loaded, _total)

    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """インジケータを計算

        Args:
            df: OHLCVデータ

        Returns:
            pd.DataFrame: インジケータ付きデータ
        """
        import pandas_ta as ta

        df["sma_20"] = ta.sma(df["close"], length=20)
        df["sma_50"] = ta.sma(df["close"], length=50)
        df["rsi_14"] = ta.rsi(df["close"], length=14)

        macd = ta.macd(df["close"])
        if macd is not None:
            cols = macd.columns.tolist()
            macd_cols = [
                c for c in cols
                if "MACD_" in c and "MACDs" not in c and "MACDh" not in c
            ]
            signal_cols = [c for c in cols if "MACDs" in c]
            hist_cols = [c for c in cols if "MACDh" in c]
            if macd_cols and signal_cols and hist_cols:
                df["macd"] = macd[macd_cols[0]]
                df["macd_signal"] = macd[signal_cols[0]]
                df["macd_histogram"] = macd[hist_cols[0]]
                df["macd_hist_slope"] = df["macd_histogram"].diff()

        stoch = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
        if stoch is not None:
            k_cols = [c for c in stoch.columns if "STOCHk" in c]
            if k_cols:
                df["stoch_k"] = stoch[k_cols[0]]

        df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        adx = ta.adx(df["high"], df["low"], df["close"], length=14)
        if adx is not None:
            adx_cols = [c for c in adx.columns if c.startswith("ADX")]
            if adx_cols:
                df["adx"] = adx[adx_cols[0]]

        # normalized_atr（ATRを20期間平均で正規化）
        atr_mean = df["atr_14"].rolling(20).mean()
        df["normalized_atr"] = df["atr_14"] / atr_mean.replace(0, float("nan"))

        # ma_alignment（MA整列度: -1〜1、ATRで正規化）
        # MA間距離と価格-MA距離をATRで正規化して意味あるスケールに
        atr_safe = df["atr_14"].replace(0, float("nan"))
        sma_diff = (df["sma_20"] - df["sma_50"]) / atr_safe
        price_diff = (df["close"] - df["sma_20"]) / atr_safe
        # 合算して-1〜1にクリップ（各成分が±2ATR程度で飽和）
        df["ma_alignment"] = ((sma_diff + price_diff) / 4.0).clip(-1, 1)

        # EMA（_calculate_scoreで使用）
        df["ema_12"] = ta.ema(df["close"], length=12)
        df["ema_26"] = ta.ema(df["close"], length=26)

        # ボリンジャーバンド幅（BB幅 = upper - lower）
        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None:
            upper_cols = [c for c in bb.columns if "BBU" in c]
            lower_cols = [c for c in bb.columns if "BBL" in c]
            if upper_cols and lower_cols:
                df["bb_width"] = (
                    bb[upper_cols[0]] - bb[lower_cols[0]]
                )

        # ダイバージェンス
        detector = DivergenceDetector(
            swing_lookback=7, min_swing_distance=10, max_swing_distance=40
        )
        div_df = detector.calculate_divergence_signal(
            df["close"], df["rsi_14"]
        )
        df["is_bullish_div"] = div_df["is_bullish_div"]
        df["is_bearish_div"] = div_df["is_bearish_div"]

        return df

    def _calculate_indicators_cached(
        self,
        df: pd.DataFrame,
        cache_key: str,
        needed_years: list[int] | None = None,
    ) -> pd.DataFrame:
        """インジケータをキャッシュ付きで計算（年別分割保存）

        全CSVデータでインジケータを計算し、年別parquetに分割保存する。
        ウォームアップは全期間計算によって自動的に処理される。
        2回目以降は必要な年のparquetだけを読み込むため高速。

        キャッシュ構造:
            .indicator_cache/{cache_key}/
                2009.parquet
                2010.parquet
                ...

        Args:
            df: OHLCVデータ（全期間）
            cache_key: キャッシュ識別キー
                （例: "M1_1708600000000_12345678"）
            needed_years: 必要な年のリスト。
                Noneの場合は全期間を計算・返却する。
                指定した場合、不足年のみ再計算してキャッシュに追記。

        Returns:
            pd.DataFrame: インジケータ付きデータ。
                needed_years指定時は該当年のみ、
                それ以外は全期間を返す。
        """
        _log = logging.getLogger(__name__)
        cache_dir = self.data_dir / ".indicator_cache" / cache_key

        # キャッシュヒット確認
        if cache_dir.is_dir():
            cached_years = {
                int(p.stem)
                for p in cache_dir.glob("*.parquet")
                if p.stem.isdigit()
            }
            if cached_years:
                years_needed = (
                    set(needed_years)
                    if needed_years is not None
                    else cached_years
                )
                missing = years_needed - cached_years
                if not missing:
                    years_to_load = sorted(years_needed)
                    try:
                        dfs = [
                            pd.read_parquet(
                                cache_dir / f"{y}.parquet"
                            )
                            for y in years_to_load
                        ]
                        _log.info(
                            "年別キャッシュ使用: %s [%s]",
                            cache_key,
                            ", ".join(
                                str(y) for y in years_to_load
                            ),
                        )
                        return pd.concat(dfs, ignore_index=True)
                    except Exception as e:
                        _log.warning(
                            "キャッシュ読み込み失敗（再計算）: %s", e
                        )

        # インジケータ計算（全期間・ウォームアップ込み）
        _log.info("インジケータ計算（全期間）: %s", cache_key)
        df = self._calculate_indicators(df)

        # 年別に分割してparquet保存
        cache_dir.mkdir(parents=True, exist_ok=True)
        time_years = pd.to_datetime(df["time"]).dt.year
        for year in time_years.unique():
            year_path = cache_dir / f"{year}.parquet"
            try:
                year_df = df[time_years == year]
                year_df.to_parquet(year_path, index=False)
                _log.debug(
                    "年別キャッシュ保存: %s/%d", cache_key, year
                )
            except Exception as e:
                _log.warning(
                    "キャッシュ保存失敗 %d: %s", year, e
                )

        # 必要年のみ返す
        if needed_years is not None:
            mask = time_years.isin(set(needed_years))
            return df[mask].reset_index(drop=True)

        return df

    def _set_higher_tf_data(
        self,
        generator: Any,
        df: pd.DataFrame | None,
        timeframe: str,
    ) -> None:
        """上位足データを設定（シグネチャ互換対応）

        Args:
            generator: シグナルジェネレータ
            df: 上位足データ
            timeframe: 時間足名
        """
        if df is None:
            return

        # 2引数パターン (timeframe, df) を試す
        try:
            generator.set_higher_tf_data(timeframe, df)
        except TypeError:
            # 1引数パターン (df) にフォールバック
            generator.set_higher_tf_data(df)

    def _generate_signal(
        self,
        generator: Any,
        row: pd.Series,
        candle: Candle,
        symbol: str,
        timeframe: Timeframe,
    ) -> Any:
        """シグナル生成（シグネチャ互換対応）

        Args:
            generator: シグナルジェネレータ
            row: データ行
            candle: キャンドル
            symbol: シンボル
            timeframe: 時間足

        Returns:
            Signal | None: シグナル
        """
        # MultiStrategyManagerの場合
        if hasattr(generator, "generate_h1_signal"):
            return generator.generate_h1_signal(row, candle, symbol)

        # 4引数パターン (row, candle, symbol, timeframe) を試す
        try:
            return generator.generate(row, candle, symbol, timeframe)
        except TypeError:
            # 3引数パターン (row, candle, symbol) にフォールバック
            return generator.generate(row, candle, symbol)

    def run(
        self,
        strategy_name: str,
        start_year: int,
        end_year: int,
        preset: str = "standard",
    ) -> BacktestResult:
        """バックテスト実行

        Args:
            strategy_name: 戦略名
            start_year: 開始年
            end_year: 終了年
            preset: 設定プリセット

        Returns:
            BacktestResult: バックテスト結果
        """
        if self._h1_df is None:
            self.load_data()

        # 戦略インスタンス生成
        strategy_info = StrategyFactory.get_info(strategy_name)
        generator = StrategyFactory.create(
            strategy_name,
            preset=preset,
            timeframe=self.config.timeframe,
        )

        # 上位足データを設定
        if hasattr(generator, "set_higher_tf_data"):
            self._set_higher_tf_data(generator, self._h4_df, "H4")
            self._set_higher_tf_data(generator, self._d1_df, "D1")

        # ボリューム決定
        volume = self.config.volume
        if volume is None and strategy_info:
            volume = strategy_info.default_volume
        volume = volume or 1.0

        # シミュレーター設定
        _pip_unit = (
            0.01
            if "JPY" in self.config.symbol.upper()
            else 0.0001
        )
        sim_config = SimulatorConfig(
            initial_balance=self.config.initial_balance,
            spread_pips=self.config.spread_pips,
            pip_value=self.config.pip_value,
            max_positions=self.config.max_positions,
            bonus_max_positions=self.config.bonus_max_positions,
            bonus_score_threshold=self.config.bonus_score_threshold,
            default_volume=volume,
            pip_unit=_pip_unit,
        )

        # 年別・月別結果を収集
        yearly_results = []
        monthly_results = []

        for year in range(start_year, end_year + 1):
            year_result = self._run_year(
                generator, sim_config, year, monthly_results
            )
            if year_result:
                yearly_results.append(year_result)
            # ジェネレータリセット
            if hasattr(generator, "reset"):
                generator.reset()

        # 集計
        return self._aggregate_results(yearly_results, monthly_results)

    def _run_year(
        self,
        generator: Any,
        sim_config: SimulatorConfig,
        year: int,
        monthly_results: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """1年分のバックテスト実行

        Args:
            generator: シグナルジェネレータ
            sim_config: シミュレーター設定
            year: 対象年
            monthly_results: 月別結果リスト（追記用）

        Returns:
            年別結果
        """
        start_date = datetime(year, 1, 1)
        end_date = datetime(year + 1, 1, 1)

        df = self._h1_df
        if df is None:
            return None

        period_df = df[
            (df["time"] >= start_date) & (df["time"] < end_date)
        ].reset_index(drop=True)

        if period_df.empty:
            return None

        simulator = TradeSimulator(config=sim_config)
        tf = Timeframe[self.config.timeframe]

        # 月別トラッキング
        current_month = None
        month_start_balance = sim_config.initial_balance
        month_trades = 0

        last_candle = None

        # numpy配列ベースのループ
        arrays = CandleArrays.from_dataframe(period_df)
        for i in range(arrays.n_rows):
            candle = arrays.get_candle(i, self.config.symbol, tf)
            last_candle = candle
            candle_time = arrays.get_time(i)

            # 月変わり検出
            candle_month = (candle_time.year, candle_time.month)
            if current_month is None:
                current_month = candle_month
                month_start_balance = simulator.state.balance
            elif candle_month != current_month:
                # 月末処理
                month_pnl = simulator.state.balance - month_start_balance
                month_return = month_pnl / month_start_balance * 100
                monthly_results.append({
                    "year": current_month[0],
                    "month": current_month[1],
                    "trades": month_trades,
                    "pnl": month_pnl,
                    "return_pct": month_return,
                })
                current_month = candle_month
                month_start_balance = simulator.state.balance
                month_trades = 0

            row = period_df.iloc[i]
            signal = self._generate_signal(
                generator, row, candle, self.config.symbol, tf
            )
            prev_trade_count = len(simulator.get_closed_trades())
            simulator.process_candle(candle, signal)
            if len(simulator.get_closed_trades()) > prev_trade_count:
                month_trades += 1

        # 強制決済
        if last_candle:
            simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

        # 最終月の結果
        if current_month:
            month_pnl = simulator.state.balance - month_start_balance
            month_return = month_pnl / month_start_balance * 100
            monthly_results.append({
                "year": current_month[0],
                "month": current_month[1],
                "trades": month_trades,
                "pnl": month_pnl,
                "return_pct": month_return,
            })

        trades = simulator.get_closed_trades()
        calculator = MetricsCalculator(
            initial_balance=sim_config.initial_balance
        )
        metrics = calculator.calculate(trades, simulator.state.daily_pnl)

        # ブレークダウン生成（regime/mode/exit_reason別）
        breakdown = calculator.generate_breakdown(trades)

        return {
            "year": year,
            "trades": len(trades),
            "win_rate": metrics.win_rate * 100,
            "non_loss_rate": metrics.non_loss_rate * 100,
            "profit_factor": metrics.profit_factor,
            "net_profit": simulator.state.balance - sim_config.initial_balance,
            "max_drawdown": metrics.max_drawdown_pct * 100,
            "sharpe": metrics.sharpe_ratio or 0,
            "breakdown": breakdown,
        }

    def _aggregate_results(
        self,
        yearly_results: list[dict[str, Any]],
        monthly_results: list[dict[str, Any]],
    ) -> BacktestResult:
        """結果を集計（後方互換用）

        Args:
            yearly_results: 年別結果
            monthly_results: 月別結果

        Returns:
            BacktestResult: 集計結果
        """
        result = self._aggregate_results_from_yearly(yearly_results)
        # 外部から monthly_results が渡された場合は上書き
        if monthly_results:
            result.monthly_results = monthly_results
        return result

    def _aggregate_results_from_yearly(
        self,
        yearly_results: list[dict[str, Any]],
    ) -> BacktestResult:
        """年別結果から集計結果を生成

        各年の結果から月別結果を抽出して集計する。

        Args:
            yearly_results: 年別結果（monthly_results フィールドを含む）

        Returns:
            BacktestResult: 集計結果
        """
        if not yearly_results:
            return BacktestResult()

        total_trades = sum(r["trades"] for r in yearly_results)
        total_profit = sum(r["net_profit"] for r in yearly_results)
        avg_win_rate = sum(r["win_rate"] for r in yearly_results) / len(
            yearly_results
        )
        avg_non_loss_rate = sum(
            r.get("non_loss_rate", 0) for r in yearly_results
        ) / len(yearly_results)
        avg_pf = sum(r["profit_factor"] for r in yearly_results) / len(
            yearly_results
        )
        max_dd = max(r["max_drawdown"] for r in yearly_results)
        avg_sharpe = sum(r["sharpe"] for r in yearly_results) / len(
            yearly_results
        )

        years = len(yearly_results)
        annual_return = (
            total_profit / self.config.initial_balance * 100 / years
        )

        # 各年の月別結果をマージして時系列順にソート
        monthly_results: list[dict[str, Any]] = []
        for yr in yearly_results:
            monthly_results.extend(yr.get("monthly_results", []))
        monthly_results.sort(
            key=lambda r: (r.get("year", 0), r.get("month", 0))
        )

        return BacktestResult(
            trades=total_trades,
            win_rate=avg_win_rate,
            non_loss_rate=avg_non_loss_rate,
            profit_factor=avg_pf,
            net_profit=total_profit,
            max_drawdown=max_dd,
            sharpe_ratio=avg_sharpe,
            annual_return=annual_return,
            monthly_results=monthly_results,
            yearly_results=yearly_results,
        )

    def run_compare(
        self,
        strategies: list[str],
        start_year: int,
        end_year: int,
        preset: str = "standard",
    ) -> dict[str, BacktestResult]:
        """複数戦略を比較実行

        Args:
            strategies: 戦略名リスト
            start_year: 開始年
            end_year: 終了年
            preset: 設定プリセット

        Returns:
            dict: 戦略名→結果のマッピング
        """
        results = {}
        for name in strategies:
            results[name] = self.run(name, start_year, end_year, preset)
        return results

    def run_walk_forward(
        self,
        strategy_name: str,
        train_years: int = 3,
        valid_years: int = 1,
        start_year: int = 2015,
        end_year: int = 2025,
    ) -> list[dict[str, Any]]:
        """ウォークフォワード検証を実行

        Args:
            strategy_name: 戦略名
            train_years: 訓練期間（年）
            valid_years: 検証期間（年）
            start_year: 開始年
            end_year: 終了年

        Returns:
            list: 各期間の結果リスト
        """
        results = []
        current = start_year

        while current + train_years + valid_years <= end_year + 1:
            train_start = current
            train_end = current + train_years - 1
            valid_start = train_end + 1
            valid_end = valid_start + valid_years - 1

            # 訓練期間
            train_result = self.run(
                strategy_name, train_start, train_end
            )

            # 検証期間
            valid_result = self.run(
                strategy_name, valid_start, valid_end
            )

            results.append({
                "train_period": f"{train_start}-{train_end}",
                "valid_period": f"{valid_start}-{valid_end}",
                "train_return": train_result.annual_return,
                "valid_return": valid_result.annual_return,
                "train_win_rate": train_result.win_rate,
                "valid_win_rate": valid_result.win_rate,
                "train_trades": train_result.trades,
                "valid_trades": valid_result.trades,
            })

            current += valid_years

        return results

    def run_unified(
        self,
        start_year: int,
        end_year: int,
        config: "UnifiedBotConfig | None" = None,
        use_m1: bool = False,
        use_multi_mode: bool = False,
        use_parallel_tf: bool = False,
        enable_scalping: bool = False,
        pm_config: "PositionManagerConfig | None" = None,
        fundamental_csv: str | None = None,
        fundamental_csv_list: list[str] | None = None,
        fundamental_guard_minutes: int = 30,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        sequential: bool = False,
    ) -> BacktestResult:
        """統合ボットでのバックテスト実行

        複数年指定時はデフォルトで年単位の並列実行を行う。
        各年が独立した UnifiedTradeBot インスタンスを使用するため
        年をまたいだ状態の汚染が発生しない。

        Args:
            start_year: 開始年
            end_year: 終了年
            config: 統合ボット設定
            use_m1: M1データを基準タイムフレームとして使用
            use_multi_mode: マルチモードトレード有効化
            use_parallel_tf: 並列マルチTFモード（全TFでエントリー可能）
            enable_scalping: スキャルピングモード有効化（M1/M5からエントリー可能）
            pm_config: PositionManager設定（外部注入）
            fundamental_csv: 経済イベントCSVパス（Noneで無効）
            fundamental_csv_list: 複数の経済イベントCSVパスリスト
            fundamental_guard_minutes: 重要指標前の停止分数
            period_start: 日単位の開始日時（Noneで年始）
            period_end: 日単位の終了日時・exclusive（Noneで年末）
            sequential: Trueでシーケンシャル実行を強制（デバッグ用）

        Returns:
            BacktestResult: バックテスト結果
        """
        from autotrader.decision.unified import UnifiedBotConfig

        if self._h1_df is None:
            self.load_data()

        # ファンダメンタルプロバイダー初期化
        # fundamental_csv_list を優先し、次に fundamental_csv を使用
        fundamental_provider = None
        _csv_paths: list[str] = []
        if fundamental_csv_list:
            _csv_paths = fundamental_csv_list
        elif fundamental_csv:
            _csv_paths = [fundamental_csv]

        if _csv_paths:
            try:
                from autotrader.adapters.fundamental.backtest_provider import (
                    BacktestFundamentalProvider,
                )
                fundamental_provider = BacktestFundamentalProvider(
                    event_guard_minutes=fundamental_guard_minutes
                )
                total_count = 0
                for _csv in _csv_paths:
                    count = fundamental_provider.load_csv(_csv)
                    total_count += count
                logger.info(
                    f"[Fundamental] バックテスト用CSV読込: "
                    f"{total_count}件 ({len(_csv_paths)}ファイル)"
                )
            except Exception as e:
                logger.warning(
                    f"[Fundamental] CSV読込失敗（無効化）: {e}"
                )

        # ボット設定（各年の bot インスタンスはこの設定から生成）
        bot_config = config or UnifiedBotConfig()

        # UNIVERSALモードまたはbot_configにM1/M5が含まれる場合は自動でロード。
        # enable_scalping/use_m1は後方互換性のため維持するが、
        # UNIVERSALモードでは明示指定不要。
        _short_tfs = {"M1", "M5"}
        _needs_short_tf = (
            use_m1
            or enable_scalping
            or bool(set(bot_config.timeframes) & _short_tfs)
        )

        years = list(range(start_year, end_year + 1))
        yearly_results: list[dict[str, Any]] = []

        # イベント発行: バックテスト開始（TFロード前に発行しUIを起動）
        # use_parallel_tf は _run_parallel_multi_tf 内で発行するためスキップ
        if not use_parallel_tf:
            self._emitter.emit_backtest_start(
                start_year=start_year,
                end_year=end_year,
                config={
                    "min_alignment": bot_config.consolidator.min_alignment,
                    "timeframes": bot_config.timeframes,
                    "use_m1": use_m1,
                    "use_multi_mode": use_multi_mode,
                    "use_parallel_tf": use_parallel_tf,
                }
            )

        # TFロード進捗コールバック（use_parallel_tf時はなし）
        def _on_tf_loaded(tf: str, current: int, total: int) -> None:
            self._emitter.emit_init_progress(
                "tf_loading", tf, current, total
            )

        # バックテスト対象年リスト（キャッシュの絞り込みに使用）
        needed_years = list(range(start_year, end_year + 1))

        market_data = self._load_all_timeframes(
            include_m1=_needs_short_tf,
            on_tf_loaded=_on_tf_loaded if not use_parallel_tf else None,
            needed_years=needed_years,
        )

        # 並列マルチTFモードの場合（旧方式・後方互換）
        if use_parallel_tf:
            return self._run_parallel_multi_tf(
                start_year=start_year,
                end_year=end_year,
                market_data=market_data,
                bot_config=bot_config,
                enable_scalping=enable_scalping,
            )

        # マルチモードコントローラー（オプション）
        multi_mode_controller = None
        if use_multi_mode:
            from autotrader.decision.unified import MultiModeController
            multi_mode_controller = MultiModeController()
            multi_mode_controller.set_market_data(market_data)

        # シミュレーター設定
        _pip_unit = (
            0.01
            if "JPY" in self.config.symbol.upper()
            else 0.0001
        )
        sim_config = SimulatorConfig(
            initial_balance=self.config.initial_balance,
            spread_pips=self.config.spread_pips,
            slippage_pips=self.config.slippage_pips,
            pip_value=self.config.pip_value,
            max_positions=self.config.max_positions,
            bonus_max_positions=self.config.bonus_max_positions,
            bonus_score_threshold=self.config.bonus_score_threshold,
            default_volume=self.config.volume or 1.0,
            use_position_manager=bot_config.use_position_manager,
            pm_config=pm_config,
            use_dynamic_lot=bot_config.use_dynamic_lot,
            pip_unit=_pip_unit,
            commission_per_lot=self.config.commission_per_lot,
            use_session_spread=self.config.use_session_spread,
        )

        if len(years) > 1 and not sequential:
            # 年単位並列実行：ProcessPoolExecutorで真のCPU並列化（GIL回避）
            import pickle as _pickle

            _log = logging.getLogger(__name__)
            max_workers = min(len(years), os.cpu_count() or 4)
            _total_years = len(years)
            _completed_count = 0

            # pickle前チェック: 不可の場合はシーケンシャルにフォールバック
            _can_parallel = True
            for _obj, _name in [
                (bot_config, "bot_config"),
                (sim_config, "sim_config"),
            ]:
                try:
                    _pickle.dumps(_obj)
                except Exception as _pe:
                    _log.warning(
                        "%s がpickle不可: シーケンシャル実行にフォールバック"
                        " (%s)",
                        _name,
                        _pe,
                    )
                    _can_parallel = False
                    break

            # fundamental_providerのpickle確認（Noneの場合はスキップ）
            _fp_picklable = fundamental_provider is None
            if not _fp_picklable:
                try:
                    _pickle.dumps(fundamental_provider)
                    _fp_picklable = True
                except Exception as _fpe:
                    _log.warning(
                        "fundamental_provider がpickle不可:"
                        " 並列実行でスキップ (%s)",
                        _fpe,
                    )
                    _fp_picklable = False

            if not _can_parallel:
                # pickle失敗 → シーケンシャルで実行
                for year in years:
                    if self._check_cancel_requested():
                        break
                    yr = self._run_unified_year(
                        bot_config, sim_config, year, market_data,
                        use_m1=use_m1,
                        multi_mode_controller=multi_mode_controller,
                        fundamental_provider=fundamental_provider,
                        period_start=period_start,
                        period_end=period_end,
                    )
                    if yr is not None:
                        yearly_results.append(yr)
            else:
                # worktree対応: プロジェクトルート
                _project_root = str(
                    Path(__file__).resolve().parent.parent.parent
                )

                # 年ごとのmarket_dataを事前フィルタリング（IPC転送量削減）
                _year_md: dict[int, dict[str, pd.DataFrame]] = {}
                for _yr in years:
                    _s = datetime(_yr, 1, 1)
                    _e = datetime(_yr + 1, 1, 1)
                    _year_md[_yr] = {
                        tf: df[
                            (df["time"] >= _s) & (df["time"] < _e)
                        ].reset_index(drop=True)
                        for tf, df in market_data.items()
                    }

                # Manager Queueでクロスプロセス進捗通知
                with multiprocessing.Manager() as _manager:
                    _progress_q = _manager.Queue()
                    _stop_drain = threading.Event()

                    def _drain_progress() -> None:
                        """キューを消費して進捗イベントをエミット"""
                        while not _stop_drain.is_set():
                            try:
                                msg = _progress_q.get(timeout=0.1)
                                self._emitter.emit_init_progress(
                                    "year_row_update",
                                    str(msg["year"]),
                                    msg["done"],
                                    msg["total"],
                                )
                            except Exception:
                                pass
                        # 残余アイテムを消化
                        while True:
                            try:
                                msg = _progress_q.get_nowait()
                                self._emitter.emit_init_progress(
                                    "year_row_update",
                                    str(msg["year"]),
                                    msg["done"],
                                    msg["total"],
                                )
                            except Exception:
                                break

                    _drain_thread = threading.Thread(
                        target=_drain_progress, daemon=True
                    )
                    _drain_thread.start()

                    # 並列開始を即座に通知
                    self._emitter.emit_init_progress(
                        "year_parallel", "", 0, _total_years
                    )

                    _mp_ctx = multiprocessing.get_context("spawn")
                    with ProcessPoolExecutor(
                        max_workers=max_workers,
                        mp_context=_mp_ctx,
                        initializer=_worker_process_init,
                        initargs=(_project_root, _progress_q),
                    ) as executor:
                        futures = {
                            executor.submit(
                                _run_year_worker,
                                (
                                    str(self.data_dir.parent),
                                    self.config,
                                    bot_config,
                                    sim_config,
                                    year,
                                    _year_md[year],
                                    use_m1,
                                    use_multi_mode,
                                    (
                                        fundamental_provider
                                        if _fp_picklable
                                        else None
                                    ),
                                    period_start,
                                    period_end,
                                ),
                            ): year
                            for year in years
                        }
                        for future in as_completed(futures):
                            try:
                                year_result = future.result()
                            except Exception as exc:
                                _log.error(
                                    "年バックテスト失敗: %s",
                                    exc,
                                    exc_info=True,
                                )
                                year_result = None
                            completed_year = futures[future]
                            if year_result is not None:
                                yearly_results.append(year_result)
                            _completed_count += 1
                            self._emitter.emit_init_progress(
                                "year_parallel",
                                f"{completed_year}年",
                                _completed_count,
                                _total_years,
                            )

                    _stop_drain.set()
                    _drain_thread.join(timeout=2.0)

                # 失敗した年を警告
                _missing = set(years) - {
                    r["year"] for r in yearly_results
                }
                if _missing:
                    _log.error(
                        "並列バックテスト失敗年: %s",
                        sorted(_missing),
                    )

            yearly_results.sort(key=lambda r: r["year"])

            # ワーカー収集データをFileEventListenerにマージ
            for _listener in self._emitter._listeners:
                if not isinstance(
                    _listener, FileEventListener
                ):
                    continue
                for yr in yearly_results:
                    _listener.merge_worker_data(
                        yr.pop("_worker_trade_rows", []),
                        yr.pop("_worker_stats", {}),
                    )
                _listener.sort_trade_rows()
                break
        else:
            # シーケンシャル実行（単年 または --sequential 指定時）
            for year in years:
                # キャンセルチェック
                if self._check_cancel_requested():
                    self._emitter.emit_backtest_end(
                        {"cancelled": True}
                    )
                    return self._aggregate_results_from_yearly(
                        yearly_results
                    )

                self._emitter.emit_year_start(year)
                year_result = self._run_unified_year(
                    bot_config,
                    sim_config,
                    year,
                    market_data,
                    use_m1=use_m1,
                    multi_mode_controller=multi_mode_controller,
                    fundamental_provider=fundamental_provider,
                    period_start=period_start,
                    period_end=period_end,
                )
                if (
                    year_result is None
                    and self._check_cancel_requested()
                ):
                    return self._aggregate_results_from_yearly(
                        yearly_results
                    )
                if year_result is not None:
                    yearly_results.append(year_result)
                    self._emitter.emit_year_end(year_result)

        # 並列実行後: 年別サマリをまとめて発行
        if len(years) > 1 and not sequential:
            for yr in yearly_results:
                self._emitter.emit_year_start(yr["year"])
                self._emitter.emit_year_end(yr)

        result = self._aggregate_results_from_yearly(yearly_results)

        # イベント発行: バックテスト終了
        self._emitter.emit_backtest_end({
            "total_trades": result.trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
        })

        return result

    def _load_all_timeframes(
        self,
        include_m1: bool = False,
        on_tf_loaded: "Callable[[str, int, int], None] | None" = None,
        needed_years: list[int] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """全時間足データをロード

        needed_years が指定された場合、キャッシュが揃っている年は
        CSVを再読み込みせずparquetから高速ロードする。
        ウォームアップは初回の全期間計算時に処理済みのため問題なし。

        Args:
            include_m1: M1/M5データを含める（メモリ使用量増加）。
                UNIVERSALモードでは自動的にTrueになる。
            on_tf_loaded: TFロード完了コールバック(tf名, 完了数, 全数)。
                インジケータ計算後に呼ばれ、UIへの進捗通知に使用。
            needed_years: バックテストに必要な年のリスト。
                指定時は該当年のデータのみを返し、メモリを節約する。
                Noneの場合は全期間を返す（後方互換性）。

        Returns:
            dict[str, pd.DataFrame]: 時間足別データフレーム
        """
        _log = logging.getLogger(__name__)
        loader = DataLoader(self.data_dir)
        data = {}

        # M30・H8はDEFAULT_TIMEFRAMESに含まれるため常にロード。
        # M1・M5はUNIVERSALモードやenable_scalping時のみロード。
        if include_m1:
            timeframes_to_load = [
                "M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"
            ]
        else:
            timeframes_to_load = ["M15", "M30", "H1", "H4", "H8", "D1"]

        total_tf = len(timeframes_to_load)

        # インスタンス変数との対応マップ
        _tf_attr_map = {
            "M1": "_m1_df",
            "M5": "_m5_df",
            "M30": "_m30_df",
            "H8": "_h8_df",
        }

        symbol = self.config.symbol
        for i, tf in enumerate(timeframes_to_load):
            # ワイルドカードでファイル検索
            pattern = f"{symbol}_{tf}_*.csv"
            tf_files = list(self.data_dir.glob(pattern))

            if not tf_files:
                # ワイルドカードなしも試行（後方互換性）
                tf_path = self.data_dir / f"{symbol}_{tf}.csv"
                tf_files = [tf_path] if tf_path.exists() else []

            if tf_files:
                tf_path = sorted(tf_files)[0]
                # キャッシュキー: TF名＋ファイルのmtime＋サイズ
                stat = tf_path.stat()
                cache_key = (
                    f"{tf}"
                    f"_{int(stat.st_mtime * 1000)}"
                    f"_{stat.st_size}"
                )

                # 年別キャッシュが揃っているか事前確認
                # → 揃っていれば CSV 読み込みをスキップ可能
                cache_dir = (
                    self.data_dir / ".indicator_cache" / cache_key
                )
                _can_skip_csv = False
                if needed_years is not None and cache_dir.is_dir():
                    cached_years = {
                        int(p.stem)
                        for p in cache_dir.glob("*.parquet")
                        if p.stem.isdigit()
                    }
                    if set(needed_years).issubset(cached_years):
                        _can_skip_csv = True

                if _can_skip_csv:
                    # CSV読み込み不要: 年別parquetを直接ロード
                    try:
                        years_to_load = sorted(needed_years)
                        _dfs = [
                            pd.read_parquet(
                                cache_dir / f"{y}.parquet"
                            )
                            for y in years_to_load
                        ]
                        df = pd.concat(_dfs, ignore_index=True)
                        _log.info(
                            "年別キャッシュ使用（CSV省略）: %s [%s]",
                            cache_key,
                            ", ".join(
                                str(y) for y in years_to_load
                            ),
                        )
                    except Exception as e:
                        _log.warning(
                            "キャッシュ読み込み失敗: %s"
                            "（CSV再読み込み）",
                            e,
                        )
                        df = loader.load_csv(tf_path)
                        if df is not None:
                            df = self._calculate_indicators_cached(
                                df, cache_key, needed_years
                            )
                else:
                    df = loader.load_csv(tf_path)
                    if df is not None:
                        df = self._calculate_indicators_cached(
                            df, cache_key, needed_years
                        )

                if df is not None and not df.empty:
                    data[tf] = df
                    # インスタンス変数にも保存
                    attr = _tf_attr_map.get(tf)
                    if attr:
                        setattr(self, attr, df)

            # TFロード・インジケータ計算完了を通知
            if on_tf_loaded is not None:
                on_tf_loaded(tf, i + 1, total_tf)

        # 既にロード済みのデータをマージ（load_data()で先にロードした分）
        for tf, attr in [
            ("H1", "_h1_df"),
            ("H4", "_h4_df"),
            ("H8", "_h8_df"),
            ("D1", "_d1_df"),
            ("M15", "_m15_df"),
            ("M30", "_m30_df"),
            ("M1", "_m1_df"),
            ("M5", "_m5_df"),
        ]:
            df = getattr(self, attr, None)
            if df is not None and tf not in data:
                data[tf] = df

        return data

    def _run_parallel_multi_tf(
        self,
        start_year: int,
        end_year: int,
        market_data: dict[str, pd.DataFrame],
        bot_config: "UnifiedBotConfig",
        enable_scalping: bool = False,
    ) -> BacktestResult:
        """並列マルチタイムフレームバックテストを実行

        全タイムフレームでエントリー可能なイベント駆動バックテスト。

        Args:
            start_year: 開始年
            end_year: 終了年
            market_data: タイムフレーム別データ
            bot_config: 統合ボット設定
            enable_scalping: スキャルピングモード有効化

        Returns:
            BacktestResult: バックテスト結果
        """
        # イベント発行: バックテスト開始
        self._emitter.emit_backtest_start(
            start_year=start_year,
            end_year=end_year,
            config={
                "mode": "parallel_multi_tf",
                "timeframes": list(market_data.keys()),
            }
        )

        # 全期間の結果を集約
        all_trades = []
        all_monthly = []

        for year in range(start_year, end_year + 1):
            if self._check_cancel_requested():
                self._emitter.emit_backtest_end({"cancelled": True})
                break

            self._emitter.emit_year_start(year)

            # 並列エンジン設定
            engine_config = ParallelEngineConfig(
                symbol=self.config.symbol,
                initial_balance=self.config.initial_balance,
                spread_pips=self.config.spread_pips,
                pip_value=self.config.pip_value,
                max_positions=self.config.max_positions,
                default_volume=self.config.volume or 1.0,
                min_confidence=bot_config.consolidator.confidence_threshold,
                enable_parallel=True,
                timeframes=list(market_data.keys()),
                use_mode_aware_consensus=True,
                enable_scalping=enable_scalping,
            )

            # 並列エンジン実行
            engine = ParallelMultiTFBacktestEngine(
                config=engine_config,
                event_emitter=self._emitter,
                cancel_callback=self._cancel_callback,
            )

            start_date = datetime(year, 1, 1)
            end_date = datetime(year + 1, 1, 1)

            result = engine.run(
                market_data=market_data,
                start_date=start_date,
                end_date=end_date,
            )

            if result.cancelled:
                self._emitter.emit_backtest_end({"cancelled": True})
                break

            all_trades.extend(result.trades)
            all_monthly.extend(result.monthly_results)

            # 年終了イベント
            year_result = {
                "year": year,
                "trades": len(result.trades),
                "win_rate": result.metrics.win_rate * 100 if result.metrics else 0,
                "net_profit": result.metrics.net_profit if result.metrics else 0,
            }
            self._emitter.emit_year_end(year_result)

        # 最終結果を構築
        metrics = None
        if all_trades:
            calculator = MetricsCalculator(
                initial_balance=self.config.initial_balance
            )
            # 日次PnLを再計算
            daily_pnl: dict[str, float] = {}
            for trade in all_trades:
                if trade.closed_at:
                    date_key = trade.closed_at.strftime("%Y-%m-%d")
                    pnl = trade.profit_loss or 0
                    daily_pnl[date_key] = daily_pnl.get(date_key, 0) + pnl
            metrics = calculator.calculate(all_trades, daily_pnl)

        result = BacktestResult(
            trades=len(all_trades),
            win_rate=metrics.win_rate * 100 if metrics else 0,
            profit_factor=metrics.profit_factor if metrics else 0,
            net_profit=metrics.net_profit if metrics else 0,
            max_drawdown=metrics.max_drawdown_pct * 100
            if metrics else 0,
            sharpe_ratio=metrics.sharpe_ratio if metrics else 0,
            annual_return=metrics.annual_return_pct
            if metrics else 0,
        )

        # イベント発行: バックテスト終了
        self._emitter.emit_backtest_end({
            "total_trades": result.trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
        })

        return result

    def _run_unified_year(
        self,
        bot_config: "UnifiedBotConfig",
        sim_config: SimulatorConfig,
        year: int,
        market_data: "dict[str, pd.DataFrame]",
        use_m1: bool = False,
        multi_mode_controller: Any = None,
        fundamental_provider: Any = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        emitter: "BacktestEventEmitter | None" = None,
        row_progress_callback: (
            "Callable[[int, int], None] | None"
        ) = None,
    ) -> dict[str, Any] | None:
        """統合ボットで1年分のバックテスト実行（self-contained）

        年ごとに新しい UnifiedTradeBot インスタンスを生成するため、
        年をまたいだ状態の累積が発生しない。
        並列実行に対応している。

        Args:
            bot_config: ボット設定（各年で fresh な bot を生成）
            sim_config: シミュレーター設定
            year: 対象年
            market_data: 全時間足データ
            use_m1: M1データを基準タイムフレームとして使用
            multi_mode_controller: マルチモードコントローラー
            fundamental_provider: ファンダメンタルプロバイダー
            period_start: 日単位の開始日時（Noneで年始）
            period_end: 日単位の終了日時・exclusive（Noneで年末）
            emitter: イベントエミッター（Noneの場合は self._emitter）
            row_progress_callback: 行レベル進捗コールバック
                (completed_rows, total_rows) → None。
                並列実行時にUIへリアルタイム進捗を通知する。

        Returns:
            年別結果（monthly_results フィールドを含む）
        """
        from autotrader.decision.unified import UnifiedTradeBot, UnifiedBotConfig  # noqa: F401

        # 年ごとに fresh な bot を生成（状態の累積を防止）
        bot = UnifiedTradeBot(bot_config)
        bot.state.initial_equity = sim_config.initial_balance
        bot.state.equity = sim_config.initial_balance
        bot.state.peak_equity = sim_config.initial_balance
        bot.set_market_data(market_data)

        # イベントエミッター（並列時はリスナーなしの no-op emitter）
        _emitter = emitter if emitter is not None else self._emitter

        # 年の標準範囲
        start_date = datetime(year, 1, 1)
        end_date = datetime(year + 1, 1, 1)
        # 日単位の期間指定で範囲を絞り込む
        if period_start is not None:
            start_date = max(start_date, period_start)
        if period_end is not None:
            end_date = min(end_date, period_end)
        # 有効な期間がなければスキップ
        if start_date >= end_date:
            return None

        # 基準タイムフレームの選択（M1 > M5 > M15 > H1）
        if use_m1 and self._m1_df is not None:
            df = self._m1_df
            tf = Timeframe.M1
        elif use_m1 and self._m5_df is not None:
            df = self._m5_df
            tf = Timeframe.M5
        elif self._m15_df is not None:
            df = self._m15_df
            tf = Timeframe.M15
        elif self._h1_df is not None:
            df = self._h1_df
            tf = Timeframe.H1
        else:
            return None

        period_df = df[
            (df["time"] >= start_date) & (df["time"] < end_date)
        ].reset_index(drop=True)

        _log = logging.getLogger(__name__)
        _log.info(
            "%d年: period_df=%d行, tf=%s",
            year, len(period_df), tf.value,
        )

        if period_df.empty:
            return None

        simulator = TradeSimulator(config=sim_config)

        # ポジションイベントロガー設定
        _pos_evt_logger = PositionEventLogger()
        simulator.set_position_event_logger(_pos_evt_logger)

        # 月別トラッキング（スレッド安全なローカルリスト）
        _monthly_results: list[dict[str, Any]] = []
        current_month = None
        month_start_balance = sim_config.initial_balance
        month_trades = 0

        # 進捗トラッキング
        total_rows = len(period_df)
        start_time = time.time()

        # メトリクス追跡
        winning_trades = 0
        losing_trades = 0
        # モード/レジーム追跡（ポジション別）
        _pos_mode_regime: dict[str, tuple[str, str]] = {}

        last_candle = None

        # numpy配列ベースのループ
        arrays = CandleArrays.from_dataframe(period_df)
        for idx in range(arrays.n_rows):
            candle = arrays.get_candle(idx, self.config.symbol, tf)
            last_candle = candle
            candle_time = arrays.get_time(idx)

            # 月変わり検出
            candle_month = (candle_time.year, candle_time.month)
            if current_month is None:
                current_month = candle_month
                month_start_balance = simulator.state.balance
            elif candle_month != current_month:
                # 月末処理
                month_pnl = simulator.state.balance - month_start_balance
                month_return = month_pnl / month_start_balance * 100
                month_result = {
                    "year": current_month[0],
                    "month": current_month[1],
                    "trades": month_trades,
                    "pnl": month_pnl,
                    "return_pct": month_return,
                }
                _monthly_results.append(month_result)
                _emitter.emit_month_end(month_result)

                current_month = candle_month
                month_start_balance = simulator.state.balance
                month_trades = 0

            # 資金管理: botのequityとエクスポージャーを同期
            open_positions = simulator.get_open_positions()
            bot.state.open_exposure_lot = sum(
                p.volume for p in open_positions
            )
            # 同方向ロット: BUY/SELLの大きい方を設定
            buy_lot = sum(
                p.volume for p in open_positions
                if p.signal_type == SignalType.BUY
            )
            sell_lot = sum(
                p.volume for p in open_positions
                if p.signal_type == SignalType.SELL
            )
            bot.state.open_same_direction_lot = max(
                buy_lot, sell_lot
            )

            # [FUNDAMENTAL] 重要指標前スキップチェック
            current_time = pd.Timestamp(candle_time)
            if fundamental_provider is not None:
                import datetime as _dt
                _now_utc = _dt.datetime(
                    candle_time.year, candle_time.month,
                    candle_time.day, candle_time.hour,
                    candle_time.minute,
                    tzinfo=_dt.timezone.utc,
                )
                _fctx = fundamental_provider.get_context(
                    _now_utc, self.config.symbol
                )
                if _fctx.has_high_impact_within_30min:
                    continue  # 重要指標直前はスキップ

            # 統合ボットでシグナル生成
            consolidated = bot.generate_signal(current_time, candle)

            # シグナルイベント発行（HOLD以外）
            if consolidated.direction.value != "HOLD":
                _emitter.emit_signal(
                    signal_type=consolidated.direction.value,
                    symbol=self.config.symbol,
                    timeframe=tf.value,
                    confidence=consolidated.confidence,
                    sl_pips=consolidated.sl_pips,
                    tp_pips=consolidated.tp_pips,
                    rationale=consolidated.rationale,
                    aligned_timeframes=consolidated.aligned_tfs,
                    candle_time=candle_time,
                    score_breakdowns=(
                        consolidated.tf_score_breakdowns
                    ),
                )

            # Signalオブジェクトに変換
            signal = None
            if consolidated.direction != SignalType.HOLD:
                if consolidated.confidence >= 0.5:
                    # SL/TPを価格に変換
                    sl_price = None
                    tp_price = None
                    if consolidated.sl_pips > 0:
                        sl_pips = consolidated.sl_pips
                        tp_pips = consolidated.tp_pips
                        if consolidated.direction == SignalType.BUY:
                            sl_price = candle.close - sl_pips / 100
                            tp_price = candle.close + tp_pips / 100
                        else:
                            sl_price = candle.close + sl_pips / 100
                            tp_price = candle.close - tp_pips / 100

                    signal = Signal(
                        symbol=self.config.symbol,
                        timeframe=tf,
                        signal_type=consolidated.direction,
                        confidence=min(consolidated.confidence, 1.0),
                        stop_loss=sl_price,
                        take_profit=tp_price,
                        reasoning=consolidated.rationale,
                        regime=consolidated.regime,
                        mode=consolidated.mode,
                        consensus_score=consolidated.consensus_score,
                        lot=consolidated.lot,
                    )

            prev_position_ids = {
                p.position_id
                for p in simulator.get_open_positions()
            }
            prev_trade_count = len(simulator.get_closed_trades())
            simulator.process_candle(candle, signal)

            # 新規ポジション検出
            current_positions = simulator.get_open_positions()
            for pos in current_positions:
                if pos.position_id not in prev_position_ids:
                    _mode = (
                        consolidated.mode
                        or getattr(bot, "_last_mode", "")
                        if consolidated
                        else getattr(bot, "_last_mode", "")
                    )
                    _regime = (
                        consolidated.regime
                        or getattr(bot, "_last_regime", "")
                        if consolidated
                        else getattr(
                            bot, "_last_regime", ""
                        )
                    )
                    _key = pos.position_id
                    # エントリー時メトリクス取得
                    row = period_df.iloc[idx]
                    _entry_atr = float(
                        row.get("atr_14", 0) or 0
                    )
                    _entry_adx = float(
                        row.get("adx", 0) or 0
                    )
                    _entry_bb_w = float(
                        row.get("bb_width", 0) or 0
                    )
                    # エントリー時メトリクス（sim側）
                    _em = simulator.get_entry_metrics(
                        pos.position_id,
                    )
                    # スプレッド: 価格→pips変換
                    _entry_spread_pips = (
                        _em.get("spread", 0) * 100
                        if _em else 0.0
                    )
                    _pos_mode_regime[_key] = {
                        "mode": _mode,
                        "regime": _regime,
                        "primary_tf": (
                            consolidated.primary_tf
                        ),
                        "strategy_id": (
                            consolidated.strategy_id
                        ),
                        "score_breakdowns": (
                            consolidated.tf_score_breakdowns
                        ),
                        "confidence": consolidated.confidence,
                        "consensus_score": (
                            consolidated.consensus_score
                            or 0.0
                        ),
                        "sl_pips": consolidated.sl_pips,
                        "tp_pips": consolidated.tp_pips,
                        "rationale": consolidated.rationale,
                        "entry_spread_pips": (
                            _entry_spread_pips
                        ),
                        "entry_atr": _entry_atr,
                        "entry_adx": _entry_adx,
                        "entry_bb_width": _entry_bb_w,
                        "position_id": pos.position_id,
                        "equity_before": (
                            _em.get("equity_before", 0)
                            if _em else 0
                        ),
                        "dd_pct_at_entry": (
                            _em.get("dd_pct_at_entry", 0)
                            if _em else 0
                        ),
                        "consecutive_losses": int(
                            _em.get(
                                "consecutive_losses", 0,
                            )
                            if _em else 0
                        ),
                        "risk_per_trade_pct": (
                            _em.get(
                                "risk_per_trade_pct", 0,
                            )
                            if _em else 0
                        ),
                        "lot": pos.volume,
                        # Phase5: 新メタデータ
                        "entry_threshold": (
                            consolidated.entry_threshold
                        ),
                        "htf_alignment": (
                            consolidated.htf_alignment
                        ),
                        "penalty_total": (
                            consolidated.penalty_total
                        ),
                        "penalty_breakdown": (
                            consolidated.penalty_breakdown
                        ),
                        "trend_strength": (
                            consolidated.trend_strength
                        ),
                    }
                    _emitter.emit_trade_opened(
                        trade_id=pos.position_id,
                        symbol=pos.symbol,
                        direction=pos.signal_type.value,
                        entry_price=pos.entry_price,
                        volume=pos.volume,
                        candle_time=candle_time,
                        trading_mode=_mode,
                        market_regime=_regime,
                    )

            # 決済検出
            closed_trades = simulator.get_closed_trades()
            if len(closed_trades) > prev_trade_count:
                month_trades += 1

                # 最新の決済トレードを取得
                new_trade = closed_trades[-1]
                pnl = new_trade.profit_loss or 0

                # リスク管理に記録（PnLを渡して複利計算に反映）
                bot.on_trade_executed(candle_time, pnl=pnl)

                if pnl > 0:
                    winning_trades += 1
                else:
                    losing_trades += 1

                # 保有時間・pips計算
                _holding_min = 0.0
                if new_trade.opened_at and candle_time:
                    _td = candle_time - new_trade.opened_at
                    _holding_min = _td.total_seconds() / 60.0
                _pips = 0.0
                if new_trade.exit_price and new_trade.entry_price:
                    _pips = (
                        (new_trade.exit_price - new_trade.entry_price) * 100
                        if new_trade.signal_type.value == "BUY"
                        else (new_trade.entry_price - new_trade.exit_price) * 100
                    )
                _ckey = (
                    new_trade.position_id
                    or str(new_trade.opened_at)
                )
                _sig_data = _pos_mode_regime.get(
                    _ckey, {}
                )
                if not _sig_data:
                    logging.getLogger(__name__).warning(
                        "sig_data欠落: key=%s", _ckey,
                    )
                _cm = _sig_data.get("mode", "")
                _cr = _sig_data.get("regime", "")
                # MFE/MAE取得
                _pos_id = (
                    new_trade.position_id
                    or _sig_data.get(
                        "position_id", ""
                    )
                )
                _mfe_mae = simulator.get_position_mfe_mae(
                    _pos_id,
                )
                # 完全クローズ時のみpop
                _still_open = any(
                    p.position_id == _pos_id
                    for p in simulator.get_open_positions()
                )
                if not _still_open:
                    _pos_mode_regime.pop(_ckey, None)
                # Exit時メトリクス取得
                _xm = simulator.get_exit_metrics(
                    new_trade.trade_id,
                )
                _exit_spread_pips = (
                    _xm.get("exit_spread", 0) * 100
                )
                # time_to_mfe計算
                _time_to_mfe_min = 0.0
                _mfe_time = _mfe_mae.get("mfe_time")
                if (
                    _mfe_time
                    and new_trade.opened_at
                ):
                    _td_mfe = (
                        _mfe_time - new_trade.opened_at
                    )
                    _time_to_mfe_min = (
                        _td_mfe.total_seconds() / 60.0
                    )
                # session判定（UTC時間ベース）
                _session = ""
                if new_trade.opened_at:
                    _h = new_trade.opened_at.hour
                    if 0 <= _h < 7:
                        _session = "TOKYO"
                    elif 7 <= _h < 12:
                        _session = "LONDON"
                    elif 12 <= _h < 17:
                        _session = "NEWYORK"
                    else:
                        _session = "TOKYO"
                # parent_trade_id / position_id
                _parent_id = (
                    new_trade.parent_trade_id or ""
                )
                _position_id = (
                    new_trade.position_id or ""
                )
                _emitter.emit_trade_closed(
                    trade_id=new_trade.trade_id,
                    symbol=new_trade.symbol,
                    direction=new_trade.signal_type.value,
                    entry_price=new_trade.entry_price,
                    exit_price=new_trade.exit_price or 0,
                    volume=new_trade.volume,
                    profit_loss=pnl,
                    exit_reason=(
                        new_trade.exit_reason.value
                        if new_trade.exit_reason
                        else "UNKNOWN"
                    ),
                    candle_time=candle_time,
                    opened_at=new_trade.opened_at,
                    holding_minutes=_holding_min,
                    pips=_pips,
                    trading_mode=_cm,
                    market_regime=_cr,
                    signal_data=_sig_data,
                    mfe_pips=_mfe_mae.get("mfe", 0.0),
                    mae_pips=_mfe_mae.get("mae", 0.0),
                    entry_spread_pips=_sig_data.get(
                        "entry_spread_pips", 0.0,
                    ),
                    entry_atr=_sig_data.get(
                        "entry_atr", 0.0,
                    ),
                    entry_adx=_sig_data.get(
                        "entry_adx", 0.0,
                    ),
                    entry_bb_width=_sig_data.get(
                        "entry_bb_width", 0.0,
                    ),
                    exit_spread_pips=_exit_spread_pips,
                    slippage_pips=_xm.get(
                        "slippage_pips", 0.0,
                    ),
                    commission=_xm.get(
                        "commission", 0.0,
                    ),
                    equity_before=_sig_data.get(
                        "equity_before", 0.0,
                    ),
                    equity_after=_xm.get(
                        "equity_after", 0.0,
                    ),
                    dd_pct_at_entry=_sig_data.get(
                        "dd_pct_at_entry", 0.0,
                    ),
                    consecutive_losses=_sig_data.get(
                        "consecutive_losses", 0,
                    ),
                    risk_per_trade_pct=_sig_data.get(
                        "risk_per_trade_pct", 0.0,
                    ),
                    lot=_sig_data.get("lot", 0.0),
                    # Phase5-6: 新フィールド
                    parent_trade_id=_parent_id,
                    position_id=_position_id,
                    entry_threshold=_sig_data.get(
                        "entry_threshold", 0.0,
                    ),
                    htf_alignment=_sig_data.get(
                        "htf_alignment", 0.0,
                    ),
                    penalty_total=_sig_data.get(
                        "penalty_total", 0.0,
                    ),
                    penalty_breakdown=_sig_data.get(
                        "penalty_breakdown", {},
                    ),
                    trend_strength=_sig_data.get(
                        "trend_strength", 0.0,
                    ),
                    mfe_r=_mfe_mae.get("mfe_r", 0.0),
                    mae_r=_mfe_mae.get("mae_r", 0.0),
                    time_to_mfe_minutes=(
                        _time_to_mfe_min
                    ),
                    session=_session,
                    strategy_id=_sig_data.get(
                        "strategy_id", "",
                    ),
                    trigger_price=_xm.get(
                        "trigger_price", 0.0,
                    ),
                    fill_price=_xm.get(
                        "fill_price", 0.0,
                    ),
                )

                # メトリクス発行
                total_trades = len(closed_trades)
                _emitter.emit_metrics(
                    balance=simulator.state.balance,
                    equity=simulator.state.balance,
                    total_trades=total_trades,
                    winning_trades=winning_trades,
                    losing_trades=losing_trades,
                    max_drawdown=simulator.state.max_drawdown * 100,
                )

            # 進捗イベント（M1時は500行ごと、それ以外は100行ごと）
            progress_interval = 500 if tf == Timeframe.M1 else 100
            if idx % progress_interval == 0:
                elapsed = time.time() - start_time
                _emitter.emit_progress(
                    current=idx,
                    total=total_rows,
                    elapsed=elapsed,
                    message=f"{year}年処理中 ({tf.value})",
                )
                # 並列実行時：行レベル進捗をメインスレッドへ通知
                if row_progress_callback is not None:
                    row_progress_callback(idx, total_rows)

                # キャンセルチェック
                if self._check_cancel_requested():
                    _emitter.emit_backtest_end({"cancelled": True})
                    return None

        # ループ完了を100%として通知
        if row_progress_callback is not None:
            row_progress_callback(total_rows, total_rows)

        # 強制決済
        if last_candle:
            simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

        # 最終月の結果
        if current_month:
            month_pnl = simulator.state.balance - month_start_balance
            month_return = month_pnl / month_start_balance * 100
            month_result = {
                "year": current_month[0],
                "month": current_month[1],
                "trades": month_trades,
                "pnl": month_pnl,
                "return_pct": month_return,
            }
            _monthly_results.append(month_result)
            _emitter.emit_month_end(month_result)

        # ポジションイベントCSV出力
        if _pos_evt_logger.event_count > 0:
            for listener in self._emitter._listeners:
                if isinstance(listener, FileEventListener):
                    evt_path = (
                        listener.log_dir
                        / f"position_events_{year}.csv"
                    )
                    _pos_evt_logger.write_csv(evt_path)
                    break

        trades = simulator.get_closed_trades()
        calculator = MetricsCalculator(
            initial_balance=sim_config.initial_balance
        )
        metrics = calculator.calculate(trades, simulator.state.daily_pnl)

        # ブレークダウン生成（regime/mode/exit_reason別）
        breakdown = calculator.generate_breakdown(trades)

        # ログ品質チェック
        self._validate_trade_log(trades, year)

        return {
            "year": year,
            "trades": len(trades),
            "win_rate": metrics.win_rate * 100,
            "non_loss_rate": metrics.non_loss_rate * 100,
            "profit_factor": metrics.profit_factor,
            "net_profit": simulator.state.balance - sim_config.initial_balance,
            "max_drawdown": metrics.max_drawdown_pct * 100,
            "sharpe": metrics.sharpe_ratio or 0,
            "breakdown": breakdown,
            "monthly_results": _monthly_results,
        }

    def _validate_trade_log(
        self,
        trades: list,
        year: int,
    ) -> None:
        """トレードログの品質チェック

        regime/mode/scoreが欠落していないか検証。
        部分決済(parent_trade_id付き)は親情報を継承するため
        score=0でもエラーとしない。

        Args:
            trades: トレードリスト
            year: 対象年
        """
        _log = logging.getLogger(__name__)
        errors: list[str] = []
        for t in trades:
            tid = t.trade_id[:8] if t.trade_id else "?"
            if not t.regime or t.regime == "UNKNOWN":
                errors.append(
                    f"regime欠落: {tid}"
                )
            if not t.mode or t.mode == "UNKNOWN":
                errors.append(
                    f"mode欠落: {tid}"
                )
            if (
                (t.consensus_score or 0) == 0
                and not t.parent_trade_id
            ):
                errors.append(
                    f"score=0: {tid}"
                )
        if errors:
            msg = (
                f"{year}年ログ品質警告"
                f"({len(errors)}件):\n"
                + "\n".join(errors[:10])
            )
            _log.warning(msg)


# ---------------------------------------------------------------------------
# ProcessPool用モジュールレベル関数（picklable）
# ---------------------------------------------------------------------------

# ワーカープロセスの進捗キュー（initializerが設定）
_WORKER_PROGRESS_QUEUE: Any = None


def _worker_process_init(
    project_root: str,
    progress_queue: Any,
) -> None:
    """ProcessPoolワーカープロセス初期化

    spawn起動後にPythonパスと進捗キューを設定する。
    各ワーカープロセスで一度だけ呼ばれる。

    Args:
        project_root: プロジェクトルートパス
        progress_queue: メインプロセスへの進捗通知キュー
    """
    import sys

    global _WORKER_PROGRESS_QUEUE
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    _WORKER_PROGRESS_QUEUE = progress_queue


def _run_year_worker(
    task_args: tuple,
) -> "dict[str, Any] | None":
    """年バックテストワーカー関数（ProcessPool用モジュールレベル）

    ProcessPoolExecutorから呼び出されるpicklable関数。
    サブプロセス内でBacktestRunnerを再構築して_run_unified_yearを実行。
    進捗はグローバルキュー経由でメインプロセスへ送信する。

    Args:
        task_args: (data_dir_base, backtest_config, bot_config,
                    sim_config, year, year_market_data,
                    use_m1, use_multi_mode, fundamental_provider,
                    period_start, period_end) のタプル

    Returns:
        年別結果 または None
    """
    from autotrader.backtest.events import BacktestEventEmitter
    from autotrader.backtest.runner import BacktestRunner

    (
        data_dir_base,
        backtest_config,
        bot_config,
        sim_config,
        year,
        year_market_data,
        use_m1,
        use_multi_mode,
        fundamental_provider,
        period_start,
        period_end,
    ) = task_args

    # サブプロセス内でRunnerを最小設定で再構築（リスナーなし）
    runner = BacktestRunner(
        data_dir=data_dir_base,
        config=backtest_config,
        verbose=False,
        log_to_file=False,
    )

    # 全時間足のインスタンス変数を設定（_run_unified_yearが参照）
    runner._m1_df = year_market_data.get("M1")
    runner._m5_df = year_market_data.get("M5")
    runner._m15_df = year_market_data.get("M15")
    runner._m30_df = year_market_data.get("M30")
    runner._h1_df = year_market_data.get("H1")
    runner._h4_df = year_market_data.get("H4")
    runner._h8_df = year_market_data.get("H8")
    runner._d1_df = year_market_data.get("D1")

    # マルチモードコントローラーをサブプロセスで再構築
    multi_mode_controller = None
    if use_multi_mode:
        try:
            from autotrader.decision.unified import MultiModeController
            multi_mode_controller = MultiModeController()
            multi_mode_controller.set_market_data(year_market_data)
        except Exception:
            pass

    # 進捗コールバック → グローバルキューに送信
    def _progress_cb(done: int, total: int) -> None:
        if _WORKER_PROGRESS_QUEUE is not None:
            try:
                _WORKER_PROGRESS_QUEUE.put_nowait(
                    {"year": year, "done": done, "total": total}
                )
            except Exception:
                pass

    from autotrader.backtest.file_listener import (
        TradeRowCollector,
    )

    # トレードデータ収集用エミッターとコレクターを設定
    _emitter = BacktestEventEmitter()
    _collector = TradeRowCollector()
    _emitter.add_listener(_collector)

    result = runner._run_unified_year(
        bot_config=bot_config,
        sim_config=sim_config,
        year=year,
        market_data=year_market_data,
        use_m1=use_m1,
        multi_mode_controller=multi_mode_controller,
        fundamental_provider=fundamental_provider,
        period_start=period_start,
        period_end=period_end,
        emitter=_emitter,
        row_progress_callback=_progress_cb,
    )

    # 収集したトレードデータを結果に付加
    if result is not None:
        result["_worker_trade_rows"] = (
            _collector._trade_rows
        )
        result["_worker_stats"] = _collector.get_stats()

    return result
