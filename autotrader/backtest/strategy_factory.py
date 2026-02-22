"""戦略ファクトリーモジュール

高勝率バランス戦略（HighWinRateGenerator）を生成。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Any


@runtime_checkable
class SignalGeneratorProtocol(Protocol):
    """シグナルジェネレータプロトコル"""

    def set_higher_tf_data(self, timeframe: str, df: Any) -> None:
        """上位足データを設定"""
        ...

    def reset(self) -> None:
        """状態をリセット"""
        ...

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        """シグナル生成"""
        ...


@dataclass
class StrategyInfo:
    """戦略情報

    Attributes:
        name: 戦略名
        description: 説明
        default_volume: デフォルトボリューム
        recommended_timeframe: 推奨時間足
    """

    name: str
    description: str
    default_volume: float
    recommended_timeframe: str


class StrategyFactory:
    """戦略ファクトリー

    高勝率バランス戦略を生成。
    """

    # 戦略情報
    STRATEGY_INFO = StrategyInfo(
        name="high-win-rate",
        description="高勝率バランス戦略（HighWinRateGenerator）",
        default_volume=0.5,
        recommended_timeframe="H1",
    )

    @classmethod
    def list_strategies(cls) -> list[str]:
        """利用可能な戦略名リストを取得

        Returns:
            list[str]: 戦略名リスト
        """
        return ["high-win-rate"]

    @classmethod
    def get_info(cls, name: str) -> StrategyInfo | None:
        """戦略情報を取得

        Args:
            name: 戦略名

        Returns:
            StrategyInfo: 戦略情報
        """
        if name == "high-win-rate":
            return cls.STRATEGY_INFO
        return None

    @classmethod
    def create(
        cls,
        name: str = "high-win-rate",
        preset: str = "standard",
        timeframe: str = "H1",
    ) -> SignalGeneratorProtocol:
        """戦略インスタンスを生成

        Args:
            name: 戦略名（high-win-rateのみ対応）
            preset: 設定プリセット（未使用、互換性のため）
            timeframe: 時間足（未使用、互換性のため）

        Returns:
            シグナルジェネレータインスタンス
        """
        from autotrader.decision.high_win_rate_generator import (
            HighWinRateGenerator,
            HighWinRateConfig,
        )

        # バランス設定を使用
        config = HighWinRateConfig.balanced()
        return HighWinRateGenerator(config)
