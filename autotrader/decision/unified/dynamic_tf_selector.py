"""動的TF選択モジュール

全TFのシグナル強度を評価し、最も強いシグナルを持つTFを
エントリーTFとして動的に選択する。
メジャーTFラダーを使い全TFロールを導出する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from autotrader.config.tf_params_registry import (
    get_holding_minutes,
    get_tp_sl_ratio,
)
from autotrader.core.enums import SignalType, Timeframe

if TYPE_CHECKING:
    from autotrader.decision.unified.config import UnifiedBotConfig


@dataclass(frozen=True)
class DynamicTFResult:
    """動的TF選択結果

    Attributes:
        selected_entry_tf: 選択されたエントリー時間足
        selected_primary_tf: 選択されたプライマリ時間足
        selected_manage_tf: 選択された管理時間足
        selected_regime_tf: 選択されたレジーム検出時間足
        selected_htf_alignment_tfs: 選択されたHTF整合チェック時間足
        all_tf_scores: 全TFのスコア辞書
        max_holding_bars: 最大保有バー数
        tp_sl_ratio_range: TP/SL比率範囲
        selection_reason: 選択理由
    """

    selected_entry_tf: str
    selected_primary_tf: str
    selected_manage_tf: str
    selected_regime_tf: str
    selected_htf_alignment_tfs: list[str]
    all_tf_scores: dict[str, float]
    max_holding_bars: int
    tp_sl_ratio_range: tuple[float, float]
    selection_reason: str


# メジャーTFラダー（トレーディングで意味のあるTFジャンプ）
MAJOR_TF_LADDER = ["M1", "M5", "M15", "H1", "H4", "D1"]


class DynamicTFSelector:
    """動的TF選択クラス

    全TFのシグナル強度スコアを評価し、最強シグナルを持つ
    TFをエントリーTFとして動的に選択する。
    メジャーTFラダーで全TFロール（primary, manage, regime, htf）を導出。
    """

    # Timeframe enumから全TF階層を動的生成（W1除外）
    TF_HIERARCHY = [tf.value for tf in Timeframe.all_trading()]

    def __init__(
        self,
        bot_config: UnifiedBotConfig | None = None,
    ) -> None:
        """初期化

        Args:
            bot_config: ボット設定（フォールバック値参照用）
        """
        self._bot_config = bot_config

    def _snap_to_ladder(self, tf: str) -> int:
        """任意のTFを最も近い（以下の）ラダー段に吸着

        Args:
            tf: 時間足文字列

        Returns:
            int: ラダーインデックス
        """
        if tf in MAJOR_TF_LADDER:
            return MAJOR_TF_LADDER.index(tf)
        # TF_HIERARCHYでの分数から最も近い以下のラダー段を選択
        try:
            tf_minutes = Timeframe(tf).minutes()
        except ValueError:
            return 2  # M15にフォールバック
        # ラダー各段の分数
        ladder_minutes = [
            Timeframe(t).minutes() for t in MAJOR_TF_LADDER
        ]
        # tf_minutes以下で最も大きいラダー段
        best_idx = 0
        for i, lm in enumerate(ladder_minutes):
            if lm <= tf_minutes:
                best_idx = i
        return best_idx

    def _derive_all_roles(
        self, entry_tf: str,
    ) -> dict[str, str | list[str]]:
        """entry_tfから全TFロールを導出

        メジャーTFラダーを使い、entry_tfの位置から
        primary/manage/regime/htf_alignmentを決定する。

        Args:
            entry_tf: エントリー時間足

        Returns:
            dict: primary_tf, manage_tf, regime_tf, htf_alignment_tfs
        """
        ladder = MAJOR_TF_LADDER
        entry_idx = self._snap_to_ladder(entry_tf)

        primary_idx = min(entry_idx + 1, len(ladder) - 1)
        regime_idx = min(entry_idx + 2, len(ladder) - 1)

        primary_tf = ladder[primary_idx]
        regime_tf = ladder[regime_idx]
        # htf_alignment: regime_tfより上の全TF（最低1つ保証）
        if regime_idx + 1 < len(ladder):
            htf_tfs = ladder[regime_idx + 1:]
        else:
            htf_tfs = [ladder[-1]]

        return {
            "primary_tf": primary_tf,
            "manage_tf": primary_tf,
            "regime_tf": regime_tf,
            "htf_alignment_tfs": htf_tfs,
        }

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

    def _get_fallback_result(
        self,
        tf_scores: dict[str, float],
        reason: str,
    ) -> DynamicTFResult:
        """フォールバック結果を生成（config参照）

        Args:
            tf_scores: TFスコア辞書
            reason: 選択理由

        Returns:
            DynamicTFResult: フォールバック結果
        """
        if self._bot_config is not None:
            _entry = self._bot_config.default_entry_tf
            _primary = self._bot_config.default_primary_tf
            _manage = self._bot_config.default_manage_tf
            _max_bars = self._bot_config.default_max_holding_bars
            _tp_sl = self._bot_config.default_tp_sl_ratio_range
            _regime = self._bot_config.regime_detection_tf
            _htf = list(self._bot_config.htf_alignment_tfs)
        else:
            _entry = "M15"
            _primary = "H1"
            _manage = "M15"
            _max_bars = 32
            _tp_sl = (1.1, 1.4)
            _regime = "H1"
            _htf = ["H4", "D1"]
        return DynamicTFResult(
            selected_entry_tf=_entry,
            selected_primary_tf=_primary,
            selected_manage_tf=_manage,
            selected_regime_tf=_regime,
            selected_htf_alignment_tfs=_htf,
            all_tf_scores=tf_scores,
            max_holding_bars=_max_bars,
            tp_sl_ratio_range=_tp_sl,
            selection_reason=reason,
        )

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
            return self._get_fallback_result(
                {}, "シグナルなし（デフォルト）",
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
            return self._get_fallback_result(
                tf_scores, "有効シグナルなし（デフォルト）",
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

        # メジャーTFラダーで全ロール導出
        roles = self._derive_all_roles(best_tf)
        holding_minutes = get_holding_minutes(best_tf)
        max_bars = self._minutes_to_bars(best_tf, holding_minutes)
        tp_sl = get_tp_sl_ratio(best_tf)

        return DynamicTFResult(
            selected_entry_tf=best_tf,
            selected_primary_tf=roles["primary_tf"],
            selected_manage_tf=roles["manage_tf"],
            selected_regime_tf=roles["regime_tf"],
            selected_htf_alignment_tfs=roles["htf_alignment_tfs"],
            all_tf_scores=tf_scores,
            max_holding_bars=max_bars,
            tp_sl_ratio_range=tp_sl,
            selection_reason=(
                f"最強シグナルTF={best_tf}"
                f"(score={tf_scores.get(best_tf, 0):.2f})"
            ),
        )
