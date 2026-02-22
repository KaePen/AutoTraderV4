"""短期足用シグナル生成器

M1/M5などの短期足に特化したシグナル生成。
ノイズ対策フィルターと上位足トレンド方向制約を適用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from autotrader.config.timeframe_preset import TimeframePreset
from autotrader.core.enums import SignalType, Timeframe, TrendDirection

if TYPE_CHECKING:
    from autotrader.decision.signal_generator import SignalResult


@dataclass(frozen=True)
class NoiseFilterResult:
    """ノイズフィルター判定結果

    Attributes:
        passed: フィルター通過フラグ
        reason: 却下理由（通過時は空文字）
        atr_pips: ATR値（pips）
        spread_ratio: スプレッド/ATR比率
        volatility_spike: ボラティリティ急増フラグ
    """

    passed: bool
    reason: str
    atr_pips: float
    spread_ratio: float
    volatility_spike: bool


@dataclass(frozen=True)
class MTFTrendContext:
    """上位足トレンドコンテキスト

    Attributes:
        direction: トレンド方向
        strength: トレンド強度（0-1）
        aligned: エントリー足と上位足のトレンド一致フラグ
    """

    direction: TrendDirection
    strength: float
    aligned: bool


class ShortTermNoiseFilter:
    """短期足ノイズフィルター

    短期足特有のノイズを除去するためのフィルタリング。

    Args:
        preset: 時間足別プリセット
        volatility_spike_threshold: ボラティリティ急増閾値（ATR比率）
    """

    def __init__(
        self,
        preset: TimeframePreset,
        volatility_spike_threshold: float = 2.0,
    ) -> None:
        self.preset = preset
        self.volatility_spike_threshold = volatility_spike_threshold

    def check(
        self,
        atr_pips: float,
        spread_pips: float,
        prev_atr_pips: float | None = None,
    ) -> NoiseFilterResult:
        """ノイズフィルターを適用

        Args:
            atr_pips: 現在のATR（pips単位）
            spread_pips: 現在のスプレッド（pips単位）
            prev_atr_pips: 前足のATR（ボラ急増判定用）

        Returns:
            NoiseFilterResult: フィルター判定結果
        """
        # ATR最小閾値チェック
        if not self.preset.is_atr_sufficient(atr_pips):
            return NoiseFilterResult(
                passed=False,
                reason=f"ATR不足: {atr_pips:.1f} < {self.preset.min_atr_pips}",
                atr_pips=atr_pips,
                spread_ratio=spread_pips / atr_pips if atr_pips > 0 else 999,
                volatility_spike=False,
            )

        # スプレッド/ATR比率チェック
        spread_ratio = spread_pips / atr_pips if atr_pips > 0 else 999
        if not self.preset.is_spread_acceptable(spread_pips, atr_pips):
            return NoiseFilterResult(
                passed=False,
                reason=f"スプレッド過大: {spread_ratio:.2f} > "
                       f"{self.preset.max_spread_atr_ratio}",
                atr_pips=atr_pips,
                spread_ratio=spread_ratio,
                volatility_spike=False,
            )

        # ボラティリティ急増チェック
        if prev_atr_pips is not None and prev_atr_pips > 0:
            atr_change = atr_pips / prev_atr_pips
            if atr_change > self.volatility_spike_threshold:
                return NoiseFilterResult(
                    passed=False,
                    reason=f"ボラ急増: {atr_change:.2f}x",
                    atr_pips=atr_pips,
                    spread_ratio=spread_ratio,
                    volatility_spike=True,
                )

        return NoiseFilterResult(
            passed=True,
            reason="",
            atr_pips=atr_pips,
            spread_ratio=spread_ratio,
            volatility_spike=False,
        )


class ShortTermSignalGenerator:
    """短期足用シグナル生成器

    短期足（M1/M5）向けに以下の対策を適用:
    1. ノイズフィルター（ATR最小値、スプレッド比率）
    2. ボラティリティ急増時の見送り
    3. 上位足トレンド方向のみエントリー

    Args:
        preset: 時間足別プリセット
        base_generator: ベースとなるシグナル生成器
        require_mtf_alignment: 上位足トレンド一致を必須とするか
    """

    def __init__(
        self,
        preset: TimeframePreset,
        base_generator: "SignalGenerator",
        require_mtf_alignment: bool = True,
    ) -> None:

        self.preset = preset
        self.base_generator = base_generator
        self.require_mtf_alignment = require_mtf_alignment
        self.noise_filter = ShortTermNoiseFilter(preset)

        # 上位足データ（外部から設定）
        self._higher_tf_data: dict[Timeframe, pd.DataFrame] = {}

    def set_higher_tf_data(
        self, tf: Timeframe, data: pd.DataFrame
    ) -> None:
        """上位足データを設定

        Args:
            tf: 時間足
            data: OHLCVデータ
        """
        self._higher_tf_data[tf] = data

    def get_mtf_trend(self, current_time: pd.Timestamp) -> MTFTrendContext:
        """上位足のトレンド方向を取得

        Args:
            current_time: 現在時刻

        Returns:
            MTFTrendContext: トレンドコンテキスト
        """
        if not self._higher_tf_data:
            return MTFTrendContext(
                direction=TrendDirection.NEUTRAL,
                strength=0.0,
                aligned=True,
            )

        # 全上位足のトレンドを集計
        up_count = 0
        down_count = 0
        total_strength = 0.0
        valid_count = 0

        for tf in self.preset.mtf_layers:
            if tf not in self._higher_tf_data:
                continue

            df = self._higher_tf_data[tf]
            # 現在時刻以前のデータを取得
            mask = df.index <= current_time
            if not mask.any():
                continue

            row = df.loc[mask].iloc[-1]

            # トレンド方向を判定
            ma_alignment = row.get("ma_alignment")
            if ma_alignment is not None and not pd.isna(ma_alignment):
                if ma_alignment > 0.2:
                    up_count += 1
                    total_strength += ma_alignment
                elif ma_alignment < -0.2:
                    down_count += 1
                    total_strength += abs(ma_alignment)
                valid_count += 1

        if valid_count == 0:
            return MTFTrendContext(
                direction=TrendDirection.NEUTRAL,
                strength=0.0,
                aligned=True,
            )

        avg_strength = total_strength / valid_count

        # 多数決でトレンド方向を決定
        if up_count > down_count:
            direction = TrendDirection.UP
        elif down_count > up_count:
            direction = TrendDirection.DOWN
        else:
            direction = TrendDirection.NEUTRAL

        # 全層一致をチェック
        all_aligned = (up_count == valid_count) or (down_count == valid_count)

        return MTFTrendContext(
            direction=direction,
            strength=min(avg_strength, 1.0),
            aligned=all_aligned,
        )

    def generate(
        self,
        indicators: pd.Series,
        atr_pips: float,
        spread_pips: float,
        prev_atr_pips: float | None = None,
        current_time: pd.Timestamp | None = None,
    ) -> "SignalResult":
        """シグナルを生成

        ノイズフィルターとMTFアライメントを適用後、
        ベースジェネレータでシグナル判定。

        Args:
            indicators: 指標値
            atr_pips: ATR（pips単位）
            spread_pips: スプレッド（pips単位）
            prev_atr_pips: 前足のATR（オプション）
            current_time: 現在時刻（MTF参照用）

        Returns:
            SignalResult: シグナル生成結果
        """
        from autotrader.decision.signal_generator import (
            SignalResult,
            SignalStrength,
        )

        # ノイズフィルター
        noise_result = self.noise_filter.check(
            atr_pips, spread_pips, prev_atr_pips
        )
        if not noise_result.passed:
            return SignalResult(
                signal_type=SignalType.HOLD,
                strength=SignalStrength(0.0, 0.0),
                reasoning=f"ノイズフィルター: {noise_result.reason}",
            )

        # ベースシグナル生成
        base_result = self.base_generator.generate(indicators)

        # HOLDならそのまま返す
        if base_result.signal_type == SignalType.HOLD:
            return base_result

        # MTFアライメントチェック
        if current_time is not None and self.require_mtf_alignment:
            mtf_trend = self.get_mtf_trend(current_time)

            # 上位足トレンドと逆方向のシグナルは却下
            if mtf_trend.direction == TrendDirection.UP:
                if base_result.signal_type == SignalType.SELL:
                    return SignalResult(
                        signal_type=SignalType.HOLD,
                        strength=base_result.strength,
                        reasoning="MTF逆行: 上位足上昇中に売りシグナル",
                    )
            elif mtf_trend.direction == TrendDirection.DOWN:
                if base_result.signal_type == SignalType.BUY:
                    return SignalResult(
                        signal_type=SignalType.HOLD,
                        strength=base_result.strength,
                        reasoning="MTF逆行: 上位足下降中に買いシグナル",
                    )

            # 全層一致でボーナス情報を追加
            if mtf_trend.aligned:
                reasoning = (
                    f"{base_result.reasoning}; "
                    f"MTF全層一致（強度+{mtf_trend.strength:.2f}）"
                )
                return SignalResult(
                    signal_type=base_result.signal_type,
                    strength=base_result.strength,
                    reasoning=reasoning,
                )

        return base_result

    @classmethod
    def for_timeframe(
        cls,
        tf: Timeframe,
        base_generator: "SignalGenerator",
    ) -> "ShortTermSignalGenerator":
        """時間足に応じたジェネレータを作成

        Args:
            tf: 時間足
            base_generator: ベースシグナル生成器

        Returns:
            ShortTermSignalGenerator: 設定済みジェネレータ
        """
        preset = TimeframePreset.for_timeframe(tf)
        return cls(
            preset=preset,
            base_generator=base_generator,
            require_mtf_alignment=True,
        )
