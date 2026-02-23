"""動的TF選択モジュール

全TFのシグナル強度を評価し、最も強いシグナルを持つTFを
エントリーTFとして動的に選択する。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.config.tf_params_registry import (
    get_holding_minutes,
    get_tp_sl_ratio,
)
from autotrader.core.enums import SignalType, Timeframe


@dataclass(frozen=True)
class DynamicTFResult:
    """動的TF選択結果

    Attributes:
        selected_entry_tf: 選択されたエントリー時間足
        selected_primary_tf: 選択されたプライマリ時間足
        all_tf_scores: 全TFのスコア辞書
        max_holding_bars: 最大保有バー数
        tp_sl_ratio_range: TP/SL比率範囲
        selection_reason: 選択理由
    """

    selected_entry_tf: str
    selected_primary_tf: str
    all_tf_scores: dict[str, float]
    max_holding_bars: int
    tp_sl_ratio_range: tuple[float, float]
    selection_reason: str


class DynamicTFSelector:
    """動的TF選択クラス

    全TFのシグナル強度スコアを評価し、最強シグナルを持つ
    TFをエントリーTFとして動的に選択する。
    """

    # Timeframe enumから全TF階層を動的生成（W1除外）
    TF_HIERARCHY = [tf.value for tf in Timeframe.all_trading()]

    def _get_primary_tf(self, entry_tf: str) -> str:
        """エントリーTFの1つ上のTFを返す

        Args:
            entry_tf: エントリー時間足

        Returns:
            str: 1つ上の時間足
        """
        idx = (
            self.TF_HIERARCHY.index(entry_tf)
            if entry_tf in self.TF_HIERARCHY
            else 2
        )
        primary_idx = min(idx + 1, len(self.TF_HIERARCHY) - 1)
        return self.TF_HIERARCHY[primary_idx]

    def _minutes_to_bars(self, entry_tf: str, minutes: int) -> int:
        """分をバー数に変換

        Args:
            entry_tf: エントリー時間足
            minutes: 保有分数

        Returns:
            int: バー数
        """
        try:
            tf_min = Timeframe(entry_tf).minutes()
        except ValueError:
            tf_min = 15
        return max(1, minutes // tf_min)

    def select(
        self,
        tf_signals: dict,
        dominant_direction: SignalType | None = None,
    ) -> DynamicTFResult:
        """全TFスコアから最強TFを動的選択

        Args:
            tf_signals: 時間足別シグナル（TimeframeSignal型を持つdict）
            dominant_direction: 優勢方向（指定時はその方向のTFのみ評価）

        Returns:
            DynamicTFResult: 動的TF選択結果
        """
        if not tf_signals:
            return DynamicTFResult(
                selected_entry_tf="M15",
                selected_primary_tf="H1",
                all_tf_scores={},
                max_holding_bars=32,
                tp_sl_ratio_range=(1.1, 1.4),
                selection_reason="シグナルなし（デフォルト）",
            )

        tf_scores: dict[str, float] = {}
        for tf, signal in tf_signals.items():
            if not hasattr(signal, "direction") or not hasattr(
                signal, "buy_strength"
            ):
                continue

            if signal.direction == SignalType.HOLD:
                tf_scores[tf] = 0.0
                continue

            # 優勢方向が指定されている場合はその方向のシグナルのみ評価
            if dominant_direction and signal.direction != dominant_direction:
                tf_scores[tf] = 0.0
                continue

            # 方向に応じたstrengthを使用
            if dominant_direction == SignalType.BUY:
                tf_scores[tf] = signal.buy_strength
            elif dominant_direction == SignalType.SELL:
                tf_scores[tf] = signal.sell_strength
            else:
                # 方向未指定: シグナル方向に対応するstrengthを使用
                if signal.direction == SignalType.BUY:
                    tf_scores[tf] = signal.buy_strength
                elif signal.direction == SignalType.SELL:
                    tf_scores[tf] = signal.sell_strength
                else:
                    tf_scores[tf] = 0.0

        if not tf_scores or all(v == 0 for v in tf_scores.values()):
            return DynamicTFResult(
                selected_entry_tf="M15",
                selected_primary_tf="H1",
                all_tf_scores=tf_scores,
                max_holding_bars=32,
                tp_sl_ratio_range=(1.1, 1.4),
                selection_reason="有効シグナルなし（デフォルト）",
            )

        # 最高スコアのTFを選択（TF階層内のもののみ）
        hierarchy_scores = {
            tf: score
            for tf, score in tf_scores.items()
            if tf in self.TF_HIERARCHY
        }

        if not hierarchy_scores:
            best_tf = max(tf_scores, key=lambda t: tf_scores[t])
        else:
            best_tf = max(
                hierarchy_scores, key=lambda t: hierarchy_scores[t]
            )

        primary_tf = self._get_primary_tf(best_tf)
        holding_minutes = get_holding_minutes(best_tf)
        max_bars = self._minutes_to_bars(best_tf, holding_minutes)
        tp_sl = get_tp_sl_ratio(best_tf)

        return DynamicTFResult(
            selected_entry_tf=best_tf,
            selected_primary_tf=primary_tf,
            all_tf_scores=tf_scores,
            max_holding_bars=max_bars,
            tp_sl_ratio_range=tp_sl,
            selection_reason=(
                f"最強シグナルTF={best_tf}"
                f"(score={tf_scores.get(best_tf, 0):.2f})"
            ),
        )
