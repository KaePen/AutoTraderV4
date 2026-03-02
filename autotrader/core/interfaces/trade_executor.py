"""トレード実行インターフェース"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from autotrader.core.entities import Position, Signal


@dataclass(frozen=True)
class ExecutionResult:
    """実行結果

    Attributes:
        success: 成功したか
        ticket: チケットID
        message: メッセージ
        exit_price: 約定価格（決済時のみ）
    """

    success: bool
    ticket: int | None = None
    message: str = ""
    exit_price: float | None = None


@runtime_checkable
class TradeExecutor(Protocol):
    """トレード実行Protocol"""

    def open_position(
        self,
        signal: Signal,
        volume: float,
    ) -> ExecutionResult:
        """ポジションを開く

        Args:
            signal: シグナル
            volume: ロット数

        Returns:
            ExecutionResult: 実行結果
        """
        ...

    def close_position(
        self,
        position: Position,
        reason: str,
    ) -> ExecutionResult:
        """ポジションを閉じる

        Args:
            position: ポジション
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        ...

    def modify_position(
        self,
        position: Position,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> ExecutionResult:
        """ポジションを修正

        Args:
            position: ポジション
            stop_loss: 新しいSL
            take_profit: 新しいTP

        Returns:
            ExecutionResult: 実行結果
        """
        ...

    def close_partial(
        self,
        position: Position,
        volume: float,
        reason: str,
    ) -> ExecutionResult:
        """ポジションを部分決済

        Args:
            position: ポジション
            volume: 決済ロット数
            reason: 決済理由

        Returns:
            ExecutionResult: 実行結果
        """
        ...

    def get_open_positions(
        self, symbol: str | None = None,
    ) -> list[Position]:
        """オープンポジションを取得

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[Position]: ポジションリスト
        """
        ...
