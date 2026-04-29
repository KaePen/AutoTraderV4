"""ライブデータフィードサービス

MT5からのリアルタイムデータ取得・指標計算を担当する。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import pandas as pd

from autotrader.adapters.mt5.data_provider import MT5DataProvider
from autotrader.calculator.technical.batch import (
    TechnicalIndicatorBatch,
    calc_indicators_multi_tf,
)
from autotrader.core.enums import Timeframe
from autotrader.decision.unified.trade_bot import UnifiedTradeBot

logger = logging.getLogger(__name__)


class DataFeedService:
    """リアルタイムデータ取得・指標計算サービス

    Attributes:
        _data_provider: MT5データプロバイダ
        _bot: TradeBotへの参照（market_data読み書き用）
    """

    def __init__(
        self,
        data_provider: MT5DataProvider,
        bot: UnifiedTradeBot,
    ) -> None:
        """初期化

        Args:
            data_provider: MT5データプロバイダ
            bot: TradeBotへの参照
        """
        self._data_provider = data_provider
        self._bot = bot

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ):
        """ローソク足データ取得（public API）

        Args:
            symbol: 通貨ペア
            timeframe: 時間足文字列またはTimeframe
            limit: 取得本数

        Returns:
            pd.DataFrame: ローソク足DataFrame
        """
        return await self._data_provider.get_candles_from_pos(
            symbol, timeframe, limit
        )

    async def get_candles_before(
        self,
        symbol: str,
        timeframe: str,
        end_time: datetime,
        limit: int,
    ):
        """指定時刻より前のローソク足データ取得

        Args:
            symbol: 通貨ペア
            timeframe: 時間足文字列
            end_time: この時刻より前のデータを取得（排他）
            limit: 取得本数

        Returns:
            pd.DataFrame: ローソク足DataFrame
        """
        tf_val = timeframe.value if hasattr(timeframe, "value") else timeframe
        tf_enum = Timeframe(tf_val)
        tf_sec = tf_enum.minutes() * 60
        # 休場日を考慮して3倍マージンで開始時刻を推定
        start = end_time - timedelta(seconds=tf_sec * limit * 3)
        df = await self._data_provider.get_candles_async(
            symbol, tf_enum, start, end_time
        )
        if df.empty:
            return df
        # end_time未満にフィルタし末尾limit件を返す
        df = df[df["time"] < end_time]
        return df.tail(limit).reset_index(drop=True)

    def get_indicators(self, timeframe: str) -> dict | None:
        """計算済み指標取得（public API）

        Args:
            timeframe: 時間足文字列

        Returns:
            dict | None: 指標辞書（データなしの場合は空dict）
        """
        return self.extract_indicators(timeframe)

    def extract_indicators(self, timeframe: str) -> dict:
        """計算済み市場データから指標値を抽出

        Args:
            timeframe: 時間足文字列

        Returns:
            dict: renderIndicators()が期待するフィールド辞書
        """
        md = self._bot._market_data if self._bot else {}
        df = md.get(timeframe)
        if df is None or df.empty:
            return {}

        row = df.iloc[-1]

        def _v(col: str) -> float | None:
            """NaN/欠損を None に変換"""
            try:
                v = row[col]
                return None if math.isnan(float(v)) else float(v)
            except (KeyError, TypeError, ValueError):
                return None

        return {
            "rsi": _v("rsi_14"),
            "macd": _v("macd"),
            "macd_signal": _v("macd_signal"),
            "macd_hist": _v("macd_histogram"),
            "adx": _v("adx"),
            "plus_di": _v("plus_di"),
            "minus_di": _v("minus_di"),
            "bb_upper": _v("bb_upper"),
            "bb_middle": _v("bb_middle"),
            "bb_lower": _v("bb_lower"),
            "atr": _v("atr_14"),
            "ema_fast": _v("ema_12"),
            "ema_slow": _v("ema_26"),
        }

    async def load_historical_data(
        self,
        symbol: str,
        lookback: int,
    ) -> None:
        """起動時に過去データをTradeBotに供給

        全TFのデータを一括収集してから設定。
        （個別set_market_dataは辞書を上書きするため）

        Args:
            symbol: 通貨ペアシンボル
            lookback: 取得本数
        """
        timeframes = self._bot.timeframes

        logger.info(
            "過去データ読込: %s %d本 x %d時間足",
            symbol,
            lookback,
            len(timeframes),
        )

        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                logger.warning("未知の時間足: %s", tf_str)
                continue

            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, lookback
            )
            if df.empty:
                logger.warning("データなし: %s %s", symbol, tf_str)
                continue

            all_data[tf_str] = df
            logger.info(
                "データ読込完了: %s %s %d本",
                symbol,
                tf_str,
                len(df),
            )

        if all_data:
            all_data = self._calc_indicators(all_data, symbol)
            self._bot.set_market_data(all_data)
            logger.info("全TFデータ設定完了: %d時間足", len(all_data))

    async def update_market_data(
        self,
        symbol: str,
        last_tick_data: dict | None,
    ) -> None:
        """最新ローソク足データを取得してTradeBotに設定

        時間足確定を待たずリアルタイム評価するため、全TFの最後の
        バーのclose/high/lowを現在のtick価格で上書きしてから
        インジケータを再計算する。

        Args:
            symbol: 通貨ペアシンボル
            last_tick_data: 直近tick価格キャッシュ
        """
        # 全TFのデータを一括収集してから設定
        # sma_50計算に50本必要なためバッファを含め200本取得
        # （個別set_market_dataは辞書を上書きするため）
        all_data: dict[str, pd.DataFrame] = {}
        for tf_str in self._bot.timeframes:
            try:
                tf = Timeframe(tf_str)
            except ValueError:
                continue

            df = await self._data_provider.get_candles_from_pos(
                symbol, tf, 200
            )
            if not df.empty:
                all_data[tf_str] = df

        # リアルタイム評価: キャッシュ済みtick価格で最後のバーを更新
        tick = last_tick_data
        if tick:
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", 0.0))
            mid = (bid + ask) / 2.0
            if mid > 0:
                for tf_str, df in all_data.items():
                    if df.empty:
                        continue
                    df = df.copy()
                    idx = df.index[-1]
                    df.at[idx, "close"] = mid
                    if mid > float(df.at[idx, "high"]):
                        df.at[idx, "high"] = mid
                    if mid < float(df.at[idx, "low"]):
                        df.at[idx, "low"] = mid
                    all_data[tf_str] = df

        if all_data:
            all_data = self._calc_indicators(all_data, symbol)
            self._bot.set_market_data(all_data)

    def _calc_indicators(
        self,
        data: dict[str, pd.DataFrame],
        symbol: str = "USDJPY",
    ) -> dict[str, pd.DataFrame]:
        """生OHLCVデータにテクニカル指標を計算して付加

        BT (PrecomputeEngine) と同じ計算経路を使い、ma_alignment 等の
        構造系・SMC 系指標も含めた完全な指標セットを bot に渡す。
        失敗時は旧 calc_indicators_multi_tf にフォールバック。
        """
        from autotrader.calculator.precompute import PrecomputeEngine
        from autotrader.core.enums import Timeframe as _TF

        try:
            engine = PrecomputeEngine()
            out: dict[str, pd.DataFrame] = {}
            for tf_str, df in data.items():
                try:
                    tf = _TF(tf_str)
                    out[tf_str] = engine.precompute(
                        df, symbol, tf, use_cache=False,
                    )
                except Exception as e:
                    logger.warning(
                        "[%s] %s: PrecomputeEngine 失敗 (%s) → 旧経路",
                        symbol, tf_str, e,
                    )
                    out[tf_str] = (
                        calc_indicators_multi_tf({tf_str: df}).get(tf_str, df)
                    )
            return out
        except Exception as e:
            logger.error(
                "[%s] _calc_indicators 全体失敗 (%s) → 旧経路",
                symbol, e,
            )
            return calc_indicators_multi_tf(data)
