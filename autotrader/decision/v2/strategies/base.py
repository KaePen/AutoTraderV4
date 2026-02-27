"""V2戦略基底クラスモジュール。

全V2戦略が実装すべきインターフェースと
エントリーシグナルのデータ構造を定義する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from autotrader.core.enums import SignalType
from autotrader.decision.v2.market_context import MarketContext


@dataclass(frozen=True)
class V2EntrySignal:
    """V2エントリーシグナル。

    戦略が生成するエントリー提案。RiskManagerで
    検証された後にSignalエンティティに変換される。

    Attributes:
        direction: 売買方向(BUY/SELL)。
        confidence: 確信度(0.0〜1.0)。
        sl_price: ストップロス価格。
        tp_price: テイクプロフィット価格。
        reasoning: シグナル根拠の説明文。
        strategy_name: 生成元戦略の名前。
    """

    direction: SignalType
    confidence: float
    sl_price: float
    tp_price: float
    reasoning: str
    strategy_name: str


class V2StrategyBase(ABC):
    """V2戦略の抽象基底クラス。

    各戦略はevaluateメソッドを実装し、
    MarketContextからエントリーシグナルを生成する。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """戦略名。"""
        ...

    @abstractmethod
    def evaluate(
        self, ctx: MarketContext,
    ) -> V2EntrySignal | None:
        """市場コンテキストからエントリーシグナルを評価。

        Args:
            ctx: 現在の市場コンテキスト。

        Returns:
            エントリー条件を満たす場合V2EntrySignal、
            それ以外はNone。
        """
        ...
