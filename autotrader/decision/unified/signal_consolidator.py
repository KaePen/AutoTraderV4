"""シグナル統合器"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from autotrader.core.enums import SignalType

from .config import ConsolidatorConfig

if TYPE_CHECKING:
    from .timeframe_evaluator import TimeframeSignal


@dataclass
class PortfolioState:
    """ポートフォリオ状態

    Attributes:
        daily_pnl: 日次損益
        open_positions: オープンポジション数
        last_trade_time: 最終取引時刻
        daily_trades: 日次取引回数
    """

    daily_pnl: float = 0.0
    open_positions: int = 0
    last_trade_time: datetime | None = None
    daily_trades: int = 0


@dataclass(frozen=True)
class ConsolidatedSignal:
    """統合シグナル

    Attributes:
        direction: シグナル方向
        confidence: 確度（0.0 ~ 1.0）
        primary_tf: 主要時間足（最も強いシグナルの時間足）
        aligned_tfs: 同方向の時間足リスト
        sl_pips: 損切りpips
        tp_pips: 利確pips
        rationale: 判断理由
        scores: 時間足別スコア詳細
    """

    direction: SignalType
    confidence: float
    primary_tf: str
    aligned_tfs: list[str]
    sl_pips: float
    tp_pips: float
    rationale: str
    scores: dict[str, float] = field(default_factory=dict)
    # ログ強化: レジーム/モード/コンセンサススコア
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    # TF別スコア内訳（ログ用）
    tf_score_breakdowns: dict[str, dict[str, float]] = field(
        default_factory=dict
    )
    # TF別方向（UI表示用: "BUY"|"SELL"|"HOLD"）
    tf_directions: dict[str, str] = field(default_factory=dict)
    # エントリールート識別（mode_selection_reason）
    strategy_id: str = ""
    # ログ品質強化: エントリー閾値/HTF整合/ペナルティ/トレンド強度
    entry_threshold: float = 0.0
    htf_alignment: float = 0.0
    penalty_total: float = 0.0
    penalty_breakdown: dict[str, float] = field(
        default_factory=dict
    )
    trend_strength: float = 0.0
    # BUY/SELL方向別スコア（双方向バー表示用）
    buy_score: float = 0.0
    sell_score: float = 0.0
    # ポジションサイジング結果
    lot: float | None = None

    @property
    def alignment_count(self) -> int:
        """一致時間足数

        Returns:
            int: 一致時間足数
        """
        return len(self.aligned_tfs)

    @property
    def is_strong_signal(self) -> bool:
        """強いシグナルかどうか

        Returns:
            bool: 確度0.6以上かつ3時間足以上一致
        """
        return self.confidence >= 0.6 and self.alignment_count >= 3


class SignalConsolidator:
    """シグナル統合器

    複数時間足のシグナルを統合し、最終的な取引判断を行う。
    """

    def __init__(self, config: ConsolidatorConfig | None = None):
        """初期化

        Args:
            config: 統合器設定
        """
        self.config = config or ConsolidatorConfig()

    def consolidate(
        self,
        tf_signals: dict[str, TimeframeSignal],
        portfolio_state: PortfolioState | None = None,
    ) -> ConsolidatedSignal:
        """全時間足シグナルを統合

        Args:
            tf_signals: 時間足別シグナル
            portfolio_state: ポートフォリオ状態

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        if not tf_signals:
            return self._create_hold_signal("シグナルなし")

        # 方向別に分類
        buy_signals = []
        sell_signals = []
        hold_signals = []

        for tf, signal in tf_signals.items():
            if signal.direction == SignalType.BUY:
                buy_signals.append((tf, signal))
            elif signal.direction == SignalType.SELL:
                sell_signals.append((tf, signal))
            else:
                hold_signals.append((tf, signal))

        # 過半数コンセンサスルール
        return self._apply_majority_rule(
            tf_signals, buy_signals, sell_signals
        )

    def _apply_majority_rule(
        self,
        tf_signals: dict[str, TimeframeSignal],
        buy_signals: list[tuple[str, TimeframeSignal]],
        sell_signals: list[tuple[str, TimeframeSignal]],
    ) -> ConsolidatedSignal:
        """過半数ルール

        過半数以上が同方向で、最小一致数を満たす場合にシグナル発行。

        Args:
            tf_signals: 時間足別シグナル
            buy_signals: 買いシグナルリスト
            sell_signals: 売りシグナルリスト

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        total = len(tf_signals)
        min_alignment = self.config.min_alignment
        majority = (total // 2) + 1

        buy_count = len(buy_signals)
        sell_count = len(sell_signals)

        if buy_count >= majority and buy_count >= min_alignment:
            return self._create_signal(
                SignalType.BUY,
                buy_signals,
                tf_signals,
                f"過半数買い({buy_count}/{total})",
            )
        elif sell_count >= majority and sell_count >= min_alignment:
            return self._create_signal(
                SignalType.SELL,
                sell_signals,
                tf_signals,
                f"過半数売り({sell_count}/{total})",
            )

        return self._create_hold_signal(
            f"過半数未達(BUY:{buy_count}/SELL:{sell_count})"
        )

    def _create_signal(
        self,
        direction: SignalType,
        aligned_signals: list[tuple[str, TimeframeSignal]],
        all_signals: dict[str, TimeframeSignal],
        rationale: str,
    ) -> ConsolidatedSignal:
        """シグナル作成

        Args:
            direction: 方向
            aligned_signals: 一致シグナルリスト
            all_signals: 全シグナル
            rationale: 判断理由

        Returns:
            ConsolidatedSignal: 統合シグナル
        """
        # 最も確度の高いシグナルを主要時間足とする
        primary_tf, primary_signal = max(
            aligned_signals, key=lambda x: x[1].confidence
        )

        # 確度計算（一致数と個別確度の平均）
        alignment_ratio = len(aligned_signals) / len(all_signals)
        avg_confidence = sum(
            min(s.confidence, 1.0) for _, s in aligned_signals
        ) / len(aligned_signals)
        confidence = min((alignment_ratio + avg_confidence) / 2, 1.0)

        # SL/TP計算（主要時間足ベース、一致時間足で調整）
        sl_pips, tp_pips = self._calculate_consolidated_sl_tp(
            aligned_signals, primary_signal
        )

        # スコア詳細
        scores = {
            tf: signal.buy_strength - signal.sell_strength
            for tf, signal in all_signals.items()
        }

        # 詳細理由
        tf_list = [tf for tf, _ in aligned_signals]
        detailed_rationale = (
            f"{rationale} | 主要TF:{primary_tf} | "
            f"一致TF:{','.join(tf_list)}"
        )

        return ConsolidatedSignal(
            direction=direction,
            confidence=confidence,
            primary_tf=primary_tf,
            aligned_tfs=tf_list,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            rationale=detailed_rationale,
            scores=scores,
        )

    def _create_hold_signal(self, rationale: str) -> ConsolidatedSignal:
        """HOLDシグナル作成

        Args:
            rationale: 判断理由

        Returns:
            ConsolidatedSignal: HOLDシグナル
        """
        return ConsolidatedSignal(
            direction=SignalType.HOLD,
            confidence=0.0,
            primary_tf="",
            aligned_tfs=[],
            sl_pips=0.0,
            tp_pips=0.0,
            rationale=rationale,
            scores={},
        )

    def _calculate_consolidated_sl_tp(
        self,
        aligned_signals: list[tuple[str, TimeframeSignal]],
        primary_signal: TimeframeSignal,
    ) -> tuple[float, float]:
        """統合SL/TP計算

        主要時間足をベースに、一致時間足の平均で調整。

        Args:
            aligned_signals: 一致シグナルリスト
            primary_signal: 主要シグナル

        Returns:
            tuple[float, float]: (SL pips, TP pips)
        """
        # 主要時間足の値を基準
        base_sl = primary_signal.sl_pips
        base_tp = primary_signal.tp_pips

        # 一致時間足の平均
        avg_sl = sum(s.sl_pips for _, s in aligned_signals) / len(
            aligned_signals
        )
        avg_tp = sum(s.tp_pips for _, s in aligned_signals) / len(
            aligned_signals
        )

        # 主要時間足70%、平均30%で加重平均
        sl_pips = base_sl * 0.7 + avg_sl * 0.3
        tp_pips = base_tp * 0.7 + avg_tp * 0.3

        return sl_pips, tp_pips
