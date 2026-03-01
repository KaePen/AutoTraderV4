"""マーケットデータサービス

ローソク足データの取得・テクニカル指標計算・リアルタイム
tick価格更新を担当する。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import pandas as pd

from autotrader.adapters.mt5.exceptions import MT5DataError
from autotrader.calculator.technical.batch import (
    TechnicalIndicatorBatch,
)
from autotrader.core.enums import Timeframe
from autotrader.core.event_bus import event_bus

logger = logging.getLogger(__name__)


class MarketDataService:
    """マーケットデータサービス

    Attributes:
        _data_provider: MT5データプロバイダ
        _bot: 統合トレードボット参照
        _last_tick_data: 直近tickキャッシュ
        _last_mt5_tick_ms: 最終tickのms時刻
    """

    def __init__(
        self,
        data_provider: object,
        bot: object,
    ) -> None:
        """初期化

        Args:
            data_provider: MT5データプロバイダ
            bot: 統合トレードボット（_market_dataアクセス用）
        """
        self._data_provider = data_provider
        self._bot = bot
        self._last_tick_data: dict | None = None
        self._last_mt5_tick_ms: int = 0

    @property
    def last_tick_data(self) -> dict | None:
        """直近tickキャッシュ"""
        return self._last_tick_data

    @last_tick_data.setter
    def last_tick_data(self, value: dict | None) -> None:
        """直近tickキャッシュ設定"""
        self._last_tick_data = value

    @property
    def last_mt5_tick_ms(self) -> int:
        """最終tickのms時刻"""
        return self._last_mt5_tick_ms

    @last_mt5_tick_ms.setter
    def last_mt5_tick_ms(self, value: int) -> None:
        """最終tickのms時刻設定"""
        self._last_mt5_tick_ms = value

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
            all_data = self.calc_indicators(all_data)
            self._bot.set_market_data(all_data)
            logger.info(
                "全TFデータ設定完了: %d時間足",
                len(all_data),
            )

    async def update_market_data(
        self,
        symbol: str,
    ) -> None:
        """最新ローソク足データを取得してTradeBotに設定

        時間足確定を待たずリアルタイム評価するため、全TFの最後の
        バーのclose/high/lowを現在のtick価格で上書きしてから
        インジケータを再計算する。

        Args:
            symbol: 通貨ペアシンボル
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
        # _tick_price_update()が0.1秒毎にキャッシュするため追加API不要
        # インジケータ・アナリティクスは同じ1秒サイクルで同期して更新される
        tick = self._last_tick_data
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
            all_data = self.calc_indicators(all_data)
            self._bot.set_market_data(all_data)

    def calc_indicators(
        self,
        data: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        """生OHLCVデータにテクニカル指標を計算して付加

        Args:
            data: 時間足別生OHLCVデータ

        Returns:
            dict[str, pd.DataFrame]: 指標付きデータ
        """
        calc = TechnicalIndicatorBatch()
        result: dict[str, pd.DataFrame] = {}
        for tf, df in data.items():
            try:
                result[tf] = calc.calculate_basic(df.copy())
            except Exception as e:
                logger.warning("指標計算失敗: %s %s", tf, e)
                result[tf] = df
        return result

    async def tick_price_update(
        self,
        symbol: str,
    ) -> None:
        """軽量tick処理: MT5のbid/askを取得して価格をbroadcast

        ローソク足取得・指標計算を行わない高速版。
        前回と同じtickであればbroadcastをスキップする。

        Args:
            symbol: 通貨ペアシンボル
        """
        try:
            tick = await self._data_provider.get_tick_fast(symbol)
        except MT5DataError:
            return

        if not tick:
            return

        tick_ms = int(tick.get("time_msc", 0))
        if tick_ms <= self._last_mt5_tick_ms:
            return  # 新しいtickなし

        self._last_mt5_tick_ms = tick_ms
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))

        # 1秒サイクルのフル処理で使うためキャッシュ
        self._last_tick_data = tick

        event_bus.publish_nowait(
            "price.updated",
            {
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "time_ms": tick_ms,
            },
        )

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

    def get_indicators(
        self,
        timeframe: str,
    ) -> dict | None:
        """計算済み指標取得（public API）

        Args:
            timeframe: 時間足文字列

        Returns:
            dict | None: 指標辞書（データなしの場合は空dict）
        """
        return self.extract_indicators(timeframe)

    def extract_indicators(
        self,
        timeframe: str,
    ) -> dict:
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
