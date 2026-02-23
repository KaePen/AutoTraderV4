"""時間足別パラメータプリセット

各時間足に最適化されたトレードパラメータを提供。
短期足（M1/M5）はノイズ対策を強化し、長期足（H1）は既存の最適化済み値を使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import Timeframe


@dataclass(frozen=True)
class TimeframePreset:
    """時間足別の最適化パラメータ

    各時間足の特性に合わせたトレードパラメータを定義。

    Attributes:
        timeframe: 対象時間足
        min_signals: シグナル発生に必要な最小シグナル数
        signal_margin: 買い/売りの差分マージン
        adx_threshold: ADX閾値（トレンド強度フィルター）
        rsi_oversold: RSI売られすぎ閾値
        rsi_overbought: RSI買われすぎ閾値
        sl_atr_mult: ストップロスのATR倍率
        tp_atr_mult: テイクプロフィットのATR倍率
        cooldown_bars: シグナル発生後のクールダウン足数
        min_atr_pips: 最小ATR（ノイズ除去用、pips単位）
        max_spread_atr_ratio: スプレッド/ATR上限比率
        mtf_layers: MTF分析に使用する上位時間足のリスト
    """

    timeframe: Timeframe
    min_signals: int
    signal_margin: int
    adx_threshold: float
    rsi_oversold: float
    rsi_overbought: float
    sl_atr_mult: float
    tp_atr_mult: float
    cooldown_bars: int
    min_atr_pips: float
    max_spread_atr_ratio: float
    mtf_layers: tuple[Timeframe, ...]

    @classmethod
    def for_m1(cls) -> TimeframePreset:
        """M1（1分足）用プリセット

        超短期足のため、ノイズ対策を強化。
        - ADX閾値を低く（トレンドが弱くても検出）
        - クールダウンを長く（連続エントリー抑制）
        - 最小ATRでノイズを除外
        - MTF: M1 + M5 + H1の3層

        Returns:
            TimeframePreset: M1用パラメータ
        """
        return cls(
            timeframe=Timeframe.M1,
            min_signals=3,
            signal_margin=2,
            adx_threshold=10.0,
            rsi_oversold=25.0,
            rsi_overbought=75.0,
            sl_atr_mult=1.5,
            tp_atr_mult=2.0,
            cooldown_bars=30,
            min_atr_pips=3.0,
            max_spread_atr_ratio=0.3,
            mtf_layers=(Timeframe.M5, Timeframe.H1),
        )

    @classmethod
    def for_m5(cls) -> TimeframePreset:
        """M5（5分足）用プリセット

        短期足のため、ノイズ対策を適用。
        - ADX閾値やや低め
        - クールダウン適度
        - MTF: M5 + H1 + H4の3層

        Returns:
            TimeframePreset: M5用パラメータ
        """
        return cls(
            timeframe=Timeframe.M5,
            min_signals=3,
            signal_margin=2,
            adx_threshold=15.0,
            rsi_oversold=28.0,
            rsi_overbought=72.0,
            sl_atr_mult=1.8,
            tp_atr_mult=2.5,
            cooldown_bars=12,
            min_atr_pips=5.0,
            max_spread_atr_ratio=0.25,
            mtf_layers=(Timeframe.H1, Timeframe.H4),
        )

    @classmethod
    def for_m15(cls) -> TimeframePreset:
        """M15（15分足）用プリセット

        中短期足。M5とH1の中間的なパラメータ。
        - MTF: M15 + H1 + H4の3層

        Returns:
            TimeframePreset: M15用パラメータ
        """
        return cls(
            timeframe=Timeframe.M15,
            min_signals=4,
            signal_margin=2,
            adx_threshold=18.0,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            sl_atr_mult=2.0,
            tp_atr_mult=3.0,
            cooldown_bars=8,
            min_atr_pips=7.0,
            max_spread_atr_ratio=0.2,
            mtf_layers=(Timeframe.H1, Timeframe.H4),
        )

    @classmethod
    def for_h1(cls) -> TimeframePreset:
        """H1（1時間足）用プリセット

        Walk-forward検証（2020-2024）で最適化済み。
        5年間すべて黒字、総利益¥559,121達成のパラメータ。
        - MTF: H1 + H4 + D1の3層

        Returns:
            TimeframePreset: H1用パラメータ
        """
        return cls(
            timeframe=Timeframe.H1,
            min_signals=4,
            signal_margin=2,
            adx_threshold=20.0,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            sl_atr_mult=2.0,
            tp_atr_mult=3.0,
            cooldown_bars=4,
            min_atr_pips=10.0,
            max_spread_atr_ratio=0.15,
            mtf_layers=(Timeframe.H4, Timeframe.D1),
        )

    @classmethod
    def for_h4(cls) -> TimeframePreset:
        """H4（4時間足）用プリセット

        中期足。スイングトレード向け。
        - MTF: H4 + D1 + W1の3層

        Returns:
            TimeframePreset: H4用パラメータ
        """
        return cls(
            timeframe=Timeframe.H4,
            min_signals=4,
            signal_margin=2,
            adx_threshold=22.0,
            rsi_oversold=32.0,
            rsi_overbought=68.0,
            sl_atr_mult=2.5,
            tp_atr_mult=4.0,
            cooldown_bars=3,
            min_atr_pips=15.0,
            max_spread_atr_ratio=0.1,
            mtf_layers=(Timeframe.D1, Timeframe.W1),
        )

    @classmethod
    def for_timeframe(cls, tf: Timeframe) -> TimeframePreset:
        """指定時間足のプリセットを取得

        既知5TFはファクトリメソッドから取得。
        未対応TFは最近接TFプリセットをベースに補間生成。

        Args:
            tf: 時間足

        Returns:
            TimeframePreset: 対応するプリセット
        """
        preset_map = {
            Timeframe.M1: cls.for_m1,
            Timeframe.M5: cls.for_m5,
            Timeframe.M15: cls.for_m15,
            Timeframe.H1: cls.for_h1,
            Timeframe.H4: cls.for_h4,
        }
        factory = preset_map.get(tf)
        if factory is not None:
            return factory()

        # 未対応TF: 最近接プリセットをベースに返す
        return cls._interpolate_preset(tf)

    @classmethod
    def _interpolate_preset(cls, tf: Timeframe) -> TimeframePreset:
        """最近接TFプリセットを元に補間生成

        Args:
            tf: 未対応の時間足

        Returns:
            TimeframePreset: 補間されたプリセット
        """
        from autotrader.config.tf_params_registry import (
            get_atr_multipliers,
        )

        target_min = tf.minutes()

        # 既知プリセットから最近接を選択
        known = [
            (Timeframe.M1, 1),
            (Timeframe.M5, 5),
            (Timeframe.M15, 15),
            (Timeframe.H1, 60),
            (Timeframe.H4, 240),
        ]
        # 最も分数が近いTFを選択
        closest_tf = min(
            known, key=lambda x: abs(x[1] - target_min)
        )[0]
        base = cls.for_timeframe(closest_tf)
        sl_mult, tp_mult = get_atr_multipliers(tf.value)

        # 上位時間足のMTFレイヤーを決定
        higher = Timeframe.get_higher_timeframes(tf)
        mtf_layers = tuple(higher[:2]) if higher else (Timeframe.D1,)

        return cls(
            timeframe=tf,
            min_signals=base.min_signals,
            signal_margin=base.signal_margin,
            adx_threshold=base.adx_threshold,
            rsi_oversold=base.rsi_oversold,
            rsi_overbought=base.rsi_overbought,
            sl_atr_mult=sl_mult,
            tp_atr_mult=tp_mult,
            cooldown_bars=base.cooldown_bars,
            min_atr_pips=base.min_atr_pips,
            max_spread_atr_ratio=base.max_spread_atr_ratio,
            mtf_layers=mtf_layers,
        )

    def is_atr_sufficient(self, atr_pips: float) -> bool:
        """ATRが最小閾値以上か判定

        Args:
            atr_pips: ATR値（pips単位）

        Returns:
            bool: 閾値以上ならTrue
        """
        return atr_pips >= self.min_atr_pips

    def is_spread_acceptable(
        self, spread_pips: float, atr_pips: float
    ) -> bool:
        """スプレッドがATR比率の許容範囲内か判定

        Args:
            spread_pips: スプレッド（pips単位）
            atr_pips: ATR値（pips単位）

        Returns:
            bool: 許容範囲内ならTrue
        """
        if atr_pips <= 0:
            return False
        ratio = spread_pips / atr_pips
        return ratio <= self.max_spread_atr_ratio

    def get_all_mtf_timeframes(self) -> tuple[Timeframe, ...]:
        """エントリー時間足を含む全MTF時間足を取得

        Returns:
            tuple[Timeframe, ...]: (エントリー足, 上位足1, 上位足2)
        """
        return (self.timeframe,) + self.mtf_layers
