"""事前計算エンジン

バックテスト前にテクニカル指標・特徴量をバッチ計算し、
Parquet形式で保存・キャッシュする。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import polars as pl

logger = logging.getLogger(__name__)

from autotrader.calculator.features.trend_features import TrendFeatures
from autotrader.calculator.features.volatility_features import (
    VolatilityFeatures,
)
from autotrader.calculator.market_structure.liquidity_analyzer import (
    LiquidityAnalyzer,
)
from autotrader.calculator.market_structure.structure_analyzer import (
    StructureAnalyzer,
)
from autotrader.calculator.market_structure.swing_analyzer import SwingAnalyzer
from autotrader.calculator.technical.momentum import MomentumIndicators
from autotrader.calculator.technical.price_structure import (
    PriceStructureIndicators,
)
from autotrader.calculator.technical.trend import TrendIndicators
from autotrader.calculator.technical.volatility import VolatilityIndicators
from autotrader.core.enums import Timeframe


@dataclass(frozen=True)
class PrecomputeConfig:
    """事前計算設定

    Attributes:
        sma_periods: SMA期間リスト
        ema_periods: EMA期間リスト
        rsi_period: RSI期間
        macd_fast: MACD短期
        macd_slow: MACD長期
        macd_signal: MACDシグナル
        atr_period: ATR期間
        bb_period: BB期間
        adx_period: ADX期間
    """

    sma_periods: tuple[int, ...] = (10, 20, 50, 100, 200)
    ema_periods: tuple[int, ...] = (10, 20, 50, 100, 200)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    bb_period: int = 20
    adx_period: int = 14

    def get_hash(self) -> str:
        """設定のハッシュ値を取得

        Returns:
            str: 設定を一意に識別するハッシュ
        """
        config_str = (
            f"{sorted(self.sma_periods)}"
            f"{sorted(self.ema_periods)}"
            f"{self.rsi_period}{self.macd_fast}{self.macd_slow}"
            f"{self.macd_signal}{self.atr_period}{self.bb_period}"
            f"{self.adx_period}"
        )
        return hashlib.md5(config_str.encode()).hexdigest()[:8]


class PrecomputeEngine:
    """事前計算エンジン

    大規模データに対してテクニカル指標・特徴量を効率的にバッチ計算。
    結果はParquet形式でキャッシュし、再利用可能。

    Args:
        config: 事前計算設定
        cache_dir: キャッシュディレクトリ
    """

    def __init__(
        self,
        config: PrecomputeConfig | None = None,
        cache_dir: Path | str = "data/cache/precomputed",
    ) -> None:
        self.config = config or PrecomputeConfig()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Path:
        """キャッシュファイルパスを取得

        Args:
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時

        Returns:
            Path: キャッシュファイルパス
        """
        config_hash = self.config.get_hash()
        date_range = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
        filename = (
            f"{symbol}_{timeframe.value}_{date_range}_{config_hash}.parquet"
        )
        return self.cache_dir / filename

    def _check_cache(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame | None:
        """キャッシュをチェック

        Args:
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時

        Returns:
            pd.DataFrame | None: キャッシュがあればDataFrame
        """
        cache_path = self._get_cache_path(symbol, timeframe, start, end)
        if cache_path.exists():
            logger.info(f"キャッシュから読み込み: {cache_path}")
            return pd.read_parquet(cache_path)
        return None

    def _save_cache(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> None:
        """結果をキャッシュに保存

        Args:
            df: 計算結果DataFrame
            symbol: シンボル
            timeframe: 時間足
            start: 開始日時
            end: 終了日時
        """
        cache_path = self._get_cache_path(symbol, timeframe, start, end)
        df.to_parquet(cache_path, index=True)
        logger.info(f"キャッシュに保存: {cache_path}")

    def compute_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """テクニカル指標を計算

        Args:
            df: OHLCV DataFrame

        Returns:
            pd.DataFrame: テクニカル指標を追加したDataFrame
        """
        result = df.copy()
        high = df["high"]
        low = df["low"]
        close = df["close"]

        # トレンド系
        for period in self.config.sma_periods:
            trend = TrendIndicators(sma_period=period)
            result[f"sma_{period}"] = trend.calculate_sma(close)

        for period in self.config.ema_periods:
            trend = TrendIndicators(ema_period=period)
            result[f"ema_{period}"] = trend.calculate_ema(close)

        # ADX
        trend = TrendIndicators(adx_period=self.config.adx_period)
        adx_df = trend.calculate_adx(high, low, close)
        result[f"adx_{self.config.adx_period}"] = adx_df[
            f"ADX_{self.config.adx_period}"
        ]
        result[f"plus_di_{self.config.adx_period}"] = adx_df[
            f"DMP_{self.config.adx_period}"
        ]
        result[f"minus_di_{self.config.adx_period}"] = adx_df[
            f"DMN_{self.config.adx_period}"
        ]

        # モメンタム系
        momentum = MomentumIndicators(
            rsi_period=self.config.rsi_period,
            macd_fast=self.config.macd_fast,
            macd_slow=self.config.macd_slow,
            macd_signal=self.config.macd_signal,
        )
        result[f"rsi_{self.config.rsi_period}"] = momentum.calculate_rsi(close)

        macd_df = momentum.calculate_macd(close)
        result["macd"] = macd_df["MACD"]
        result["macd_signal"] = macd_df["MACD_signal"]
        result["macd_histogram"] = macd_df["MACD_histogram"]

        stoch_df = momentum.calculate_stochastics(high, low, close)
        result["stoch_k"] = stoch_df["stoch_k"]
        result["stoch_d"] = stoch_df["stoch_d"]

        # ボラティリティ系
        volatility = VolatilityIndicators(
            atr_period=self.config.atr_period, bb_period=self.config.bb_period
        )
        result[f"atr_{self.config.atr_period}"] = volatility.calculate_atr(
            high, low, close
        )

        bb_df = volatility.calculate_bollinger_bands(close)
        result["bb_upper"] = bb_df["bb_upper"]
        result["bb_middle"] = bb_df["bb_middle"]
        result["bb_lower"] = bb_df["bb_lower"]
        result["bb_width"] = bb_df["bb_width"]
        result["bb_percent_b"] = bb_df["bb_percent_b"]

        # 価格構造
        structure = PriceStructureIndicators()
        result["pivot_high"] = structure.calculate_pivot_high(high)
        result["pivot_low"] = structure.calculate_pivot_low(low)

        # SMC（Smart Money Concept）指標
        result = self.compute_smc_indicators(result)

        # ボリューム移動平均比率
        # MT5 Parquetは tick_volume(実データ) と volume(常に0) を持つ
        _vol_col = None
        if "tick_volume" in result.columns and result["tick_volume"].sum() > 0:
            _vol_col = "tick_volume"
        elif "volume" in result.columns and result["volume"].sum() > 0:
            _vol_col = "volume"

        if _vol_col is not None:
            _vol_ma = result[_vol_col].rolling(20).mean()
            result["volume_ma_20"] = _vol_ma
            result["volume_ratio"] = result[_vol_col] / (
                _vol_ma.replace(0, float("nan"))
            )

        return result

    def compute_smc_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """SMC（Smart Money Concept）指標を計算

        Args:
            df: OHLCV DataFrame（テクニカル指標計算済み）

        Returns:
            pd.DataFrame: SMC指標を追加したDataFrame
        """
        result = df.copy()

        # スイング分析
        swing_analyzer = SwingAnalyzer(lookback=5, lookforward=2)
        swing_df = swing_analyzer.calculate_all(df)
        result["swing_high"] = swing_df["swing_high"]
        result["swing_low"] = swing_df["swing_low"]
        result["last_swing_high"] = swing_df["last_swing_high"]
        result["last_swing_low"] = swing_df["last_swing_low"]

        # 市場構造分析（BOS/CHoCH）
        structure_analyzer = StructureAnalyzer(swing_analyzer=swing_analyzer)
        structure_df = structure_analyzer.detect_bos_choch(df)
        result["bos_signal"] = structure_df["bos_signal"]
        result["choch_signal"] = structure_df["choch_signal"]
        result["structure_direction"] = structure_df["structure_direction"]
        result["trend_state_smc"] = structure_df["trend_state"]

        # 流動性分析
        liquidity_analyzer = LiquidityAnalyzer(swing_analyzer=swing_analyzer)
        liquidity_df = liquidity_analyzer.detect_liquidity_grab(df)
        result["liquidity_grab_bullish"] = liquidity_df["liquidity_grab_bullish"]
        result["liquidity_grab_bearish"] = liquidity_df["liquidity_grab_bearish"]
        result["buy_side_liquidity"] = liquidity_df["buy_side_liquidity"]
        result["sell_side_liquidity"] = liquidity_df["sell_side_liquidity"]

        # Look-ahead bias 修正: スイング判定は lookforward 本先の
        # 未来データを参照するため、全スイング依存カラムを
        # lookforward 分だけ遅延させる（遅延確認方式）
        lookforward = swing_analyzer.lookforward
        smc_columns = [
            # SwingAnalyzer 出力
            "swing_high", "swing_low",
            "last_swing_high", "last_swing_low",
            # StructureAnalyzer 出力
            "bos_signal", "choch_signal",
            "structure_direction", "trend_state_smc",
            # LiquidityAnalyzer 出力
            "liquidity_grab_bullish", "liquidity_grab_bearish",
            "buy_side_liquidity", "sell_side_liquidity",
        ]
        for col in smc_columns:
            if col in result.columns:
                result[col] = result[col].shift(lookforward)

        return result

    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """特徴量を計算

        Args:
            df: テクニカル指標計算済みDataFrame

        Returns:
            pd.DataFrame: 特徴量を追加したDataFrame
        """
        result = df.copy()
        high = df["high"]
        low = df["low"]
        close = df["close"]

        # トレンド特徴量
        trend_features = TrendFeatures()
        trend_df = trend_features.calculate_all(high, low, close)
        result["trend_direction"] = trend_df["trend_direction"].apply(
            lambda x: x.value if hasattr(x, "value") else x
        )
        result["trend_strength"] = trend_df["trend_strength"]
        result["ma_alignment"] = trend_df["ma_alignment"]
        result["slope_consistency"] = trend_df["slope_consistency"]
        result["deviation_score"] = trend_df["deviation_score"]

        # ボラティリティ特徴量
        vol_features = VolatilityFeatures()
        vol_df = vol_features.calculate_all(high, low, close)
        result["volatility_regime"] = vol_df["volatility_regime"].apply(
            lambda x: x.value if hasattr(x, "value") else x
        )
        result["normalized_atr"] = vol_df["normalized_atr"]
        result["bb_squeeze"] = vol_df["bb_squeeze"]
        result["range_expansion"] = vol_df["range_expansion"]
        result["volatility_trend"] = vol_df["volatility_trend"]

        return result

    def precompute(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: Timeframe,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """事前計算を実行

        Args:
            df: OHLCV DataFrame
            symbol: シンボル
            timeframe: 時間足
            use_cache: キャッシュを使用するか

        Returns:
            pd.DataFrame: テクニカル指標・特徴量を追加したDataFrame
        """
        if df.empty:
            logger.warning("空のDataFrameが渡されました")
            return df

        start = df.index.min()
        end = df.index.max()

        # キャッシュチェック
        if use_cache:
            cached = self._check_cache(symbol, timeframe, start, end)
            if cached is not None:
                return cached

        logger.info(
            f"事前計算開始: {symbol} {timeframe.value} "
            f"({len(df)}本, {start} - {end})"
        )

        # 計算実行
        result = self.compute_technical_indicators(df)
        result = self.compute_features(result)

        # キャッシュ保存
        if use_cache:
            self._save_cache(result, symbol, timeframe, start, end)

        logger.info(f"事前計算完了: {len(result.columns)}列")
        return result

    def precompute_chunked(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: Timeframe,
        chunk_months: int = 1,
    ) -> pd.DataFrame:
        """チャンク単位で事前計算（大規模データ用）

        Args:
            df: OHLCV DataFrame
            symbol: シンボル
            timeframe: 時間足
            chunk_months: チャンクサイズ（月単位）

        Returns:
            pd.DataFrame: 結合された計算結果
        """
        if df.empty:
            return df

        df = df.copy()
        df["_year_month"] = df.index.to_period("M")

        results: list[pd.DataFrame] = []
        periods = df["_year_month"].unique()

        logger.info(f"チャンク処理開始: {len(periods)}チャンク")

        for period in periods:
            chunk = df[df["_year_month"] == period].drop(
                columns=["_year_month"]
            )
            chunk_start = chunk.index.min()
            chunk_end = chunk.index.max()

            cached = self._check_cache(
                symbol, timeframe, chunk_start, chunk_end
            )

            if cached is not None:
                results.append(cached)
            else:
                result = self.compute_technical_indicators(chunk)
                result = self.compute_features(result)
                self._save_cache(
                    result, symbol, timeframe, chunk_start, chunk_end
                )
                results.append(result)

        final = pd.concat(results, axis=0)
        final = final.sort_index()

        logger.info(f"チャンク処理完了: {len(final)}行")
        return final

    def load_with_polars(self, file_path: Path | str) -> pl.DataFrame:
        """Polarsで高速読み込み

        Args:
            file_path: Parquetファイルパス

        Returns:
            pl.DataFrame: Polars DataFrame
        """
        return pl.read_parquet(file_path)

    def clear_cache(self, symbol: str | None = None) -> int:
        """キャッシュをクリア

        Args:
            symbol: 特定シンボルのみクリア（Noneで全クリア）

        Returns:
            int: 削除したファイル数
        """
        count = 0
        for file in self.cache_dir.glob("*.parquet"):
            if symbol is None or file.name.startswith(symbol):
                file.unlink()
                count += 1

        logger.info(f"キャッシュクリア: {count}ファイル削除")
        return count
