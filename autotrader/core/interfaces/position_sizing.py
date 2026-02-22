"""ポジションサイジングインターフェース"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from autotrader.core.enums import MarketRegime


@dataclass(frozen=True)
class SizingContext:
    """サイジングコンテキスト

    ロット数計算に必要な情報を保持。

    Attributes:
        equity: 現在の有効証拠金
        sl_pips: SL距離（pips）
        confidence: シグナル確度（0-1）
        regime: 相場レジーム
        consecutive_losses: 連敗数
        current_dd_pct: 現在のドローダウン率（0-1）
        initial_equity: 初期資金（資金管理の基準）
        open_exposure_lot: 現在の合計オープンロット数
        open_same_direction_lot: 同方向オープンロット数
    """

    equity: float
    sl_pips: float
    confidence: float
    regime: MarketRegime
    consecutive_losses: int
    current_dd_pct: float
    initial_equity: float = 1_000_000.0
    open_exposure_lot: float = 0.0
    open_same_direction_lot: float = 0.0


@dataclass(frozen=True)
class SizingResult:
    """サイジング結果

    Attributes:
        lot: 算出ロット数
        risk_budget: リスク予算（通貨）
        risk_adjust: リスク調整係数
        reasoning: 算出理由
        blocked: 取引拒否（資金保護）
    """

    lot: float
    risk_budget: float
    risk_adjust: float
    reasoning: str
    blocked: bool = False


class PositionSizerProtocol(ABC):
    """ポジションサイザープロトコル"""

    @abstractmethod
    def calculate(self, context: SizingContext) -> SizingResult:
        """ロット数を計算

        Args:
            context: サイジングコンテキスト

        Returns:
            SizingResult: サイジング結果
        """
        ...
