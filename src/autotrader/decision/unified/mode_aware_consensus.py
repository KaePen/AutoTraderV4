"""モード対応コンセンサスモジュール

ALL/MAJORITY/WEIGHTEDを廃止し、単一コンセンサスルールに統合。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_selector import TradingPlan
from autotrader.decision.unified.timeframe_router import (
    TimeframeRole,
    TimeframeSet,
)


@dataclass(frozen=True)
class TimeframeSignal:
    """時間足別シグナル

    Attributes:
        direction: シグナル方向
        strength: シグナル強度（0-1）
        sl_pips: SL距離（pips）
        tp_pips: TP距離（pips）
    """

    direction: SignalType
    strength: float
    sl_pips: float
    tp_pips: float


@dataclass(frozen=True)
class ConsensusResult:
    """コンセンサス結果

    Attributes:
        direction: 最終シグナル方向
        score: 統合スコア
        threshold: 適用閾値
        aligned_tfs: 同方向TFリスト
        reasoning: 判断理由
    """

    direction: SignalType
    score: float
    threshold: float
    aligned_tfs: list[str]
    reasoning: str


@dataclass(frozen=True)
class ConsensusConfig:
    """コンセンサス設定

    Attributes:
        primary_weight: 主要TFの重み
        entry_weight: エントリーTFの重み
        confirm_weight: 確認TFの重み
        other_weight: その他TFの重み
        scalping_threshold: スキャルピングモードの閾値
        day_trade_threshold: デイトレードモードの閾値
        swing_threshold: スイングモードの閾値
    """

    primary_weight: float = 3.0
    entry_weight: float = 2.0
    confirm_weight: float = 1.5
    other_weight: float = 0.5
    scalping_threshold: float = 3.5
    day_trade_threshold: float = 4.5
    swing_threshold: float = 6.0


class ModeAwareScoreConsensus:
    """モード対応スコアコンセンサス

    TradingPlanに従って重み付けスコアを計算し、
    モード別閾値で判定する。

    計算手順:
    1. 各TFの方向を{-1, 0, +1}に正規化
    2. TF役割に応じた重みを付与
    3. 加重合計スコアを計算
    4. モード別閾値で判定
    """

    # 役割別デフォルト重み
    ROLE_WEIGHTS: dict[TimeframeRole, float] = {
        TimeframeRole.PRIMARY: 3.0,
        TimeframeRole.ENTRY: 2.0,
        TimeframeRole.CONFIRM: 1.5,
        TimeframeRole.MANAGE: 1.0,
        TimeframeRole.OTHER: 0.5,
    }

    # モード別役割重み
    # SCALPING: ENTRY(M1)重視で素早いエントリー
    # DAY_TRADE: PRIMARY(M15)とCONFIRM(H1,H4)のバランス
    # SWING: PRIMARY(H4)とCONFIRM(D1)重視で高品質シグナル
    ROLE_WEIGHTS_BY_MODE: dict[TradingStrategyMode, dict[TimeframeRole, float]] = {
        TradingStrategyMode.SCALPING: {
            TimeframeRole.PRIMARY: 2.0,
            TimeframeRole.ENTRY: 3.0,     # M1重視
            TimeframeRole.CONFIRM: 2.5,   # M15確認
            TimeframeRole.MANAGE: 1.0,
            TimeframeRole.OTHER: 0.2,
        },
        TradingStrategyMode.DAY_TRADE: {
            TimeframeRole.PRIMARY: 3.0,   # M15
            TimeframeRole.ENTRY: 2.5,     # M5
            TimeframeRole.CONFIRM: 2.0,   # H1, H4
            TimeframeRole.MANAGE: 1.5,
            TimeframeRole.OTHER: 0.3,
        },
        TradingStrategyMode.SWING: {
            TimeframeRole.PRIMARY: 3.5,   # H4重視
            TimeframeRole.ENTRY: 2.0,     # H1
            TimeframeRole.CONFIRM: 2.5,   # D1重視
            TimeframeRole.MANAGE: 1.5,
            TimeframeRole.OTHER: 0.3,
        },
    }

    def __init__(self, config: ConsensusConfig | None = None) -> None:
        """初期化

        Args:
            config: コンセンサス設定
        """
        self.config = config or ConsensusConfig()

        # 設定から重みを更新
        self.role_weights = {
            TimeframeRole.PRIMARY: self.config.primary_weight,
            TimeframeRole.ENTRY: self.config.entry_weight,
            TimeframeRole.CONFIRM: self.config.confirm_weight,
            TimeframeRole.MANAGE: 1.0,
            TimeframeRole.OTHER: self.config.other_weight,
        }

        self.mode_thresholds = {
            TradingStrategyMode.SCALPING: self.config.scalping_threshold,
            TradingStrategyMode.DAY_TRADE: self.config.day_trade_threshold,
            TradingStrategyMode.SWING: self.config.swing_threshold,
        }

    def consolidate(
        self,
        tf_signals: dict[str, TimeframeSignal],
        plan: TradingPlan,
    ) -> ConsensusResult:
        """シグナルを統合

        Args:
            tf_signals: 時間足別シグナル
            plan: トレーディングプラン

        Returns:
            ConsensusResult: コンセンサス結果
        """
        from autotrader.decision.unified.timeframe_router import TimeframeRouter

        router = TimeframeRouter()
        tf_set = router.route(plan)

        # 各TFのスコアを計算
        buy_score = 0.0
        sell_score = 0.0
        buy_tfs: list[str] = []
        sell_tfs: list[str] = []

        for tf, signal in tf_signals.items():
            weight = self._get_weight(tf, tf_set, plan.mode)
            direction_value = self._direction_to_value(signal.direction)

            # 強度を考慮したスコア
            weighted_score = weight * abs(direction_value) * signal.strength

            if direction_value > 0:
                buy_score += weighted_score
                buy_tfs.append(tf)
            elif direction_value < 0:
                sell_score += weighted_score
                sell_tfs.append(tf)

        # 最終スコアと方向を決定
        if buy_score > sell_score:
            final_score = buy_score
            aligned_tfs = buy_tfs
            direction = SignalType.BUY
        elif sell_score > buy_score:
            final_score = sell_score
            aligned_tfs = sell_tfs
            direction = SignalType.SELL
        else:
            final_score = 0.0
            aligned_tfs = []
            direction = SignalType.HOLD

        # 閾値判定
        threshold = self.mode_thresholds.get(plan.mode, 4.0)

        if final_score < threshold:
            reasoning = (
                f"スコア不足: {final_score:.2f} < 閾値{threshold:.2f} "
                f"({plan.mode.value})"
            )
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=final_score,
                threshold=threshold,
                aligned_tfs=aligned_tfs,
                reasoning=reasoning,
            )

        reasoning = (
            f"{direction.value}シグナル: スコア{final_score:.2f} >= "
            f"閾値{threshold:.2f}, 整合TF={aligned_tfs}"
        )

        return ConsensusResult(
            direction=direction,
            score=final_score,
            threshold=threshold,
            aligned_tfs=aligned_tfs,
            reasoning=reasoning,
        )

    def _get_weight(
        self,
        tf: str,
        tf_set: TimeframeSet,
        mode: TradingStrategyMode | None = None,
    ) -> float:
        """時間足の重みを取得

        Args:
            tf: 時間足
            tf_set: 時間足セット
            mode: トレーディングモード（指定時はモード別重みを使用）

        Returns:
            float: 重み
        """
        role = tf_set.get_role(tf)

        # モード別重みがあれば使用
        if mode is not None and mode in self.ROLE_WEIGHTS_BY_MODE:
            mode_weights = self.ROLE_WEIGHTS_BY_MODE[mode]
            return mode_weights.get(role, 0.3)

        return self.role_weights.get(role, self.config.other_weight)

    def _direction_to_value(self, direction: SignalType) -> int:
        """シグナル方向を数値に変換

        Args:
            direction: シグナル方向

        Returns:
            int: -1, 0, +1
        """
        if direction == SignalType.BUY:
            return 1
        elif direction == SignalType.SELL:
            return -1
        return 0

    def get_threshold_for_mode(self, mode: TradingStrategyMode) -> float:
        """モードの閾値を取得

        Args:
            mode: トレーディングモード

        Returns:
            float: 閾値
        """
        return self.mode_thresholds.get(mode, 4.0)

    def check_entry_conditions(
        self,
        tf_signals: dict[str, TimeframeSignal],
        plan: "TradingPlan",
        completed_tf: str,
    ) -> ConsensusResult:
        """エントリー条件をチェック

        entry_tf足確定時のみエントリー判断を行う。

        Args:
            tf_signals: 時間足別シグナル
            plan: トレーディングプラン
            completed_tf: 確定した時間足

        Returns:
            ConsensusResult: コンセンサス結果
        """
        # entry_tf足確定時のみ判断
        if completed_tf != plan.entry_tf:
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=0.0,
                threshold=self.get_threshold_for_mode(plan.mode),
                aligned_tfs=[],
                reasoning=f"entry_tf({plan.entry_tf})未確定",
            )

        # 通常のコンセンサス処理
        result = self.consolidate(tf_signals, plan)

        # entry_tfの方向と整合性チェック
        entry_signal = tf_signals.get(plan.entry_tf)
        if entry_signal is None:
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=result.score,
                threshold=result.threshold,
                aligned_tfs=result.aligned_tfs,
                reasoning="entry_tfシグナルなし",
            )

        # コンセンサスとentry_tfの方向が一致しない場合はスキップ
        if (
            result.direction != SignalType.HOLD
            and entry_signal.direction != SignalType.HOLD
            and result.direction != entry_signal.direction
        ):
            return ConsensusResult(
                direction=SignalType.HOLD,
                score=result.score,
                threshold=result.threshold,
                aligned_tfs=result.aligned_tfs,
                reasoning=f"コンセンサス({result.direction.value})とentry_tf({entry_signal.direction.value})不一致",
            )

        return result
