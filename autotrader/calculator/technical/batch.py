"""テクニカルインジケータ一括計算モジュール

マルチタイムフレームのテクニカル指標を一括計算する。
"""

from __future__ import annotations

import pandas as pd


class TechnicalIndicatorBatch:
    """マルチタイムフレームインジケータ一括計算

    全時間足のインジケータを一括計算するクラス。
    """

    def __init__(
        self,
        sma_short: int = 20,
        sma_long: int = 50,
        rsi_period: int = 14,
        atr_period: int = 14,
        adx_period: int = 14,
        stoch_k: int = 14,
        stoch_d: int = 3,
        swing_lookback: int = 7,
        min_swing_distance: int = 10,
        max_swing_distance: int = 40,
    ):
        """初期化

        Args:
            sma_short: 短期SMA期間
            sma_long: 長期SMA期間
            rsi_period: RSI期間
            atr_period: ATR期間
            adx_period: ADX期間
            stoch_k: ストキャスティクスK期間
            stoch_d: ストキャスティクスD期間
            swing_lookback: スイング検出ルックバック
            min_swing_distance: 最小スイング距離
            max_swing_distance: 最大スイング距離
        """
        self._sma_short = sma_short
        self._sma_long = sma_long
        self._rsi_period = rsi_period
        self._atr_period = atr_period
        self._adx_period = adx_period
        self._stoch_k = stoch_k
        self._stoch_d = stoch_d
        self._swing_lookback = swing_lookback
        self._min_swing_distance = min_swing_distance
        self._max_swing_distance = max_swing_distance

    def calculate_all_timeframes(
        self,
        data_dict: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        """全時間足のインジケータを計算

        Args:
            data_dict: 時間足別データフレーム

        Returns:
            dict[str, pd.DataFrame]: インジケータ付きデータフレーム
        """
        result = {}
        for tf, df in data_dict.items():
            result[tf] = self.calculate_single(df.copy())
        return result

    def calculate_single(self, df: pd.DataFrame) -> pd.DataFrame:
        """単一時間足のインジケータを計算

        Args:
            df: OHLCVデータ

        Returns:
            pd.DataFrame: インジケータ付きデータ
        """
        import pandas_ta as ta

        from autotrader.calculator.features.divergence_features import (
            DivergenceDetector,
        )

        # SMA
        df["sma_20"] = ta.sma(df["close"], length=self._sma_short)
        df["sma_50"] = ta.sma(df["close"], length=self._sma_long)

        # RSI
        df["rsi_14"] = ta.rsi(df["close"], length=self._rsi_period)

        # MACD
        macd = ta.macd(df["close"])
        if macd is not None:
            cols = macd.columns.tolist()
            macd_cols = [
                c for c in cols
                if "MACD_" in c and "MACDs" not in c
                and "MACDh" not in c
            ]
            signal_cols = [c for c in cols if "MACDs" in c]
            hist_cols = [c for c in cols if "MACDh" in c]
            if macd_cols and signal_cols and hist_cols:
                df["macd"] = macd[macd_cols[0]]
                df["macd_signal"] = macd[signal_cols[0]]
                df["macd_histogram"] = macd[hist_cols[0]]
                df["macd_hist_slope"] = (
                    df["macd_histogram"].diff()
                )

        # Stochastic
        stoch = ta.stoch(
            df["high"],
            df["low"],
            df["close"],
            k=self._stoch_k,
            d=self._stoch_d,
        )
        if stoch is not None:
            k_cols = [
                c for c in stoch.columns if "STOCHk" in c
            ]
            if k_cols:
                df["stoch_k"] = stoch[k_cols[0]]

        # ATR
        df["atr_14"] = ta.atr(
            df["high"],
            df["low"],
            df["close"],
            length=self._atr_period,
        )

        # ADX
        adx = ta.adx(
            df["high"],
            df["low"],
            df["close"],
            length=self._adx_period,
        )
        if adx is not None:
            adx_cols = [
                c for c in adx.columns if c.startswith("ADX")
            ]
            if adx_cols:
                df["adx"] = adx[adx_cols[0]]

        # ダイバージェンス
        detector = DivergenceDetector(
            swing_lookback=self._swing_lookback,
            min_swing_distance=self._min_swing_distance,
            max_swing_distance=self._max_swing_distance,
        )
        div_df = detector.calculate_divergence_signal(
            df["close"],
            df["rsi_14"],
        )
        df["is_bullish_div"] = div_df["is_bullish_div"]
        df["is_bearish_div"] = div_df["is_bearish_div"]

        return df

    def calculate_basic(self, df: pd.DataFrame) -> pd.DataFrame:
        """基本インジケータのみ計算（高速版）

        ダイバージェンスを除いた基本インジケータのみを計算。

        Args:
            df: OHLCVデータ

        Returns:
            pd.DataFrame: インジケータ付きデータ
        """
        import pandas_ta as ta

        # SMA
        df["sma_20"] = ta.sma(
            df["close"], length=self._sma_short
        )
        df["sma_50"] = ta.sma(
            df["close"], length=self._sma_long
        )

        # RSI
        df["rsi_14"] = ta.rsi(
            df["close"], length=self._rsi_period
        )

        # MACD
        macd = ta.macd(df["close"])
        if macd is not None:
            cols = macd.columns.tolist()
            macd_cols = [
                c for c in cols
                if "MACD_" in c and "MACDs" not in c
                and "MACDh" not in c
            ]
            signal_cols = [c for c in cols if "MACDs" in c]
            hist_cols = [c for c in cols if "MACDh" in c]
            if macd_cols and signal_cols and hist_cols:
                df["macd"] = macd[macd_cols[0]]
                df["macd_signal"] = macd[signal_cols[0]]
                df["macd_histogram"] = macd[hist_cols[0]]

        # Stochastic（K+D両方）
        stoch = ta.stoch(
            df["high"],
            df["low"],
            df["close"],
            k=self._stoch_k,
            d=self._stoch_d,
        )
        if stoch is not None:
            k_cols = [
                c for c in stoch.columns if "STOCHk" in c
            ]
            d_cols = [
                c for c in stoch.columns if "STOCHd" in c
            ]
            if k_cols:
                df["stoch_k"] = stoch[k_cols[0]]
            if d_cols:
                df["stoch_d"] = stoch[d_cols[0]]

        # ATR
        df["atr_14"] = ta.atr(
            df["high"],
            df["low"],
            df["close"],
            length=self._atr_period,
        )

        # ATR移動平均（SoftGuard用）
        df["atr_ma_20"] = ta.sma(df["atr_14"], length=20)

        # ADX + DI
        adx = ta.adx(
            df["high"],
            df["low"],
            df["close"],
            length=self._adx_period,
        )
        if adx is not None:
            adx_cols = [
                c for c in adx.columns
                if c.startswith("ADX")
            ]
            dmp_cols = [
                c for c in adx.columns
                if c.startswith("DMP")
            ]
            dmn_cols = [
                c for c in adx.columns
                if c.startswith("DMN")
            ]
            if adx_cols:
                df["adx"] = adx[adx_cols[0]]
            if dmp_cols:
                df["plus_di"] = adx[dmp_cols[0]]
            if dmn_cols:
                df["minus_di"] = adx[dmn_cols[0]]

        # EMA（EMAクロス用）
        df["ema_12"] = ta.ema(df["close"], length=12)
        df["ema_26"] = ta.ema(df["close"], length=26)

        # Bollinger Bands（%B / 幅 / 上中下限）
        bb = ta.bbands(df["close"], length=20)
        if bb is not None:
            pb_cols = [c for c in bb.columns if "BBP" in c]
            bw_cols = [c for c in bb.columns if "BBB" in c]
            bbu_cols = [c for c in bb.columns if "BBU" in c]
            bbm_cols = [c for c in bb.columns if "BBM" in c]
            bbl_cols = [c for c in bb.columns if "BBL" in c]
            if pb_cols:
                df["bb_percent_b"] = bb[pb_cols[0]]
            if bw_cols:
                df["bb_width"] = bb[bw_cols[0]]
            if bbu_cols:
                df["bb_upper"] = bb[bbu_cols[0]]
            if bbm_cols:
                df["bb_middle"] = bb[bbm_cols[0]]
            if bbl_cols:
                df["bb_lower"] = bb[bbl_cols[0]]

        # MACDヒストグラムスロープ（前回差分）
        if "macd_histogram" in df.columns:
            df["macd_hist_slope"] = (
                df["macd_histogram"].diff()
            )

        return df
