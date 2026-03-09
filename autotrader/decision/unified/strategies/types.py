"""輻輳型アーキテクチャ用型定義

各戦略からの提案、エッジスコア、戦略コンテキスト等を定義。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from autotrader.core.enums import MarketRegime, SignalType

if TYPE_CHECKING:
    from autotrader.calculator.features.regime_detector import RegimeResult


class StrategyId(str, Enum):
    """戦略識別子"""

    SCALP = "scalp"
    SHORT_MID = "short_mid"
    SWING = "swing"
    NO_TRADE = "no_trade"


@dataclass(frozen=True)
class StrategyTimeframes:
    """戦略別タイムフレーム設定

    Attributes:
        primary_tf: 主要時間足（方向判定基準）
        entry_tf: エントリー時間足（タイミング基準）
        confirm_tfs: 確認用時間足
        htf_refs: HTF参照リスト
        htf_weight: HTFフィルター強度（0.0-1.0）
        tp_sl_ratio_range: TP/SL比率の範囲
    """

    primary_tf: str
    entry_tf: str
    confirm_tfs: tuple[str, ...]
    htf_refs: tuple[str, ...]
    htf_weight: float
    tp_sl_ratio_range: tuple[float, float]

    @property
    def all_tfs(self) -> tuple[str, ...]:
        """評価対象の全時間足を返す"""
        tfs = {self.primary_tf, self.entry_tf}
        tfs.update(self.confirm_tfs)
        tfs.update(self.htf_refs)
        return tuple(sorted(tfs, key=_tf_order))


def _tf_order(tf: str) -> int:
    """時間足のソート順序"""
    order = {"M1": 1, "M5": 2, "M15": 3, "M30": 4, "H1": 5, "H4": 6, "D1": 7}
    return order.get(tf, 99)


@dataclass(frozen=True)
class EdgeScoreComponents:
    """エッジスコア構成要素

    edge_score = base_confidence * score_margin_factor
                 * regime_fit_factor * cost_factor
                 * htf_conflict_factor * soft_guard_factor

    Attributes:
        base_confidence: 戦略内統合の確度（0.0-1.0）
        score_margin_factor: スコア差分係数（閾値からの超過量）
        regime_fit_factor: レジーム適合係数
        cost_factor: コスト係数（スプレッド/ATR等）
        htf_conflict_factor: HTF整合係数（1.0=整合、<1.0=不整合）
        soft_guard_factor: ソフトガード係数（1.0=ペナルティなし）
    """

    base_confidence: float
    score_margin_factor: float
    regime_fit_factor: float
    cost_factor: float
    htf_conflict_factor: float
    soft_guard_factor: float = 1.0

    @property
    def edge_score(self) -> float:
        """総合エッジスコアを計算

        Returns:
            float: エッジスコア（0.0-1.0）
        """
        raw = (
            self.base_confidence
            * self.score_margin_factor
            * self.regime_fit_factor
            * self.cost_factor
            * self.htf_conflict_factor
            * self.soft_guard_factor
        )
        return max(0.0, min(1.0, raw))


@dataclass(frozen=True)
class InStrategyConsensusResult:
    """戦略内コンセンサス結果

    Attributes:
        direction: 決定方向
        primary_tf: 主要時間足
        aligned_tfs: 同方向の時間足リスト
        total_score: 合計スコア
        buy_score: 買いスコア合計
        sell_score: 売りスコア合計
        confidence: 確度（0.0-1.0）
        reasoning: 判断理由
    """

    direction: SignalType
    primary_tf: str
    aligned_tfs: tuple[str, ...]
    total_score: float
    buy_score: float
    sell_score: float
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class ProposedTrade:
    """戦略からの提案トレード

    各戦略がevaluate()で返す提案。edge_scoreで選択される。

    Attributes:
        strategy_id: 戦略識別子
        direction: シグナル方向
        edge_score: 選択基準スコア（0.0-1.0）
        edge_components: エッジスコア構成要素
        consensus: 戦略内コンセンサス結果
        primary_tf: 主要時間足
        sl_pips: 損切りpips
        tp_pips: 利確pips
        reasoning: 判断理由
    """

    strategy_id: StrategyId
    direction: SignalType
    edge_score: float
    edge_components: EdgeScoreComponents
    consensus: InStrategyConsensusResult
    primary_tf: str
    sl_pips: float
    tp_pips: float
    reasoning: str

    @property
    def is_actionable(self) -> bool:
        """アクション可能か（HOLD以外）"""
        return self.direction != SignalType.HOLD


@dataclass(frozen=True)
class StrategyContext:
    """戦略評価コンテキスト

    全戦略で共有される市場状態情報。

    Attributes:
        regime_result: レジーム判定結果
        current_price: 現在価格
        spread_pips: スプレッド（pips）
        hour_utc: UTC時刻（0-23）
        has_open_position: ポジション保有中か
        current_strategy_id: 現在保有中のポジションの戦略ID
    """

    regime_result: RegimeResult
    current_price: float
    spread_pips: float
    hour_utc: int
    has_open_position: bool
    current_strategy_id: StrategyId | None = None

    @property
    def regime(self) -> MarketRegime:
        """現在のレジームを返す"""
        return self.regime_result.regime


@dataclass
class PoolEvaluationResult:
    """戦略プールの評価結果

    Attributes:
        proposals: 各戦略からの提案リスト
        evaluated_at: 評価時刻（ISO形式文字列）
        regime: 評価時のレジーム
    """

    proposals: list[ProposedTrade] = field(default_factory=list)
    evaluated_at: str = ""
    regime: MarketRegime = MarketRegime.RANGE

    @property
    def actionable_proposals(self) -> list[ProposedTrade]:
        """アクション可能な提案のみ返す"""
        return [p for p in self.proposals if p.is_actionable]

    @property
    def best_proposal(self) -> ProposedTrade | None:
        """最高edge_scoreの提案を返す"""
        actionable = self.actionable_proposals
        if not actionable:
            return None
        return max(actionable, key=lambda p: p.edge_score)


@dataclass(frozen=True)
class SelectionResult:
    """戦略選択結果

    Attributes:
        chosen: 選択された提案（なければNone）
        all_proposals: 全提案リスト
        reasoning: 選択理由
    """

    chosen: ProposedTrade | None
    all_proposals: tuple[ProposedTrade, ...]
    reasoning: str
