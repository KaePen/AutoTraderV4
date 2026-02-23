"""トレーディングモード選択モジュール

UNIVERSALモード固定で全TFを動的評価するプランを返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autotrader.core.enums import TradingStrategyMode

if TYPE_CHECKING:
    from autotrader.decision.unified.config import UnifiedBotConfig


@dataclass(frozen=True)
class TradingPlan:
    """トレーディングプラン

    選択されたモードと参照時間足の設定。

    Attributes:
        mode: トレーディングモード
        primary_tf: 主要時間足（シグナル判断の基準）
        entry_tf: エントリー時間足（タイミング判断）
        confirm_tfs: 確認用時間足リスト
        manage_tf: 管理用時間足（ポジション管理）
        max_holding_bars: 最大保有バー数（primary_tf基準）
        tp_sl_ratio_range: TP/SL比率の推奨範囲
    """

    mode: TradingStrategyMode
    primary_tf: str
    entry_tf: str
    confirm_tfs: list[str]
    manage_tf: str
    max_holding_bars: int
    tp_sl_ratio_range: tuple[float, float]
    selection_reason: str = ""
    regime: str | None = None
    dynamic_entry_tf: str | None = None

    @property
    def all_tfs(self) -> list[str]:
        """使用する全時間足のリスト

        Returns:
            list[str]: 重複なしの時間足リスト
        """
        tfs = {self.primary_tf, self.entry_tf, self.manage_tf}
        tfs.update(self.confirm_tfs)
        return list(tfs)

    def get_recommended_tp_sl_ratio(self) -> float:
        """推奨TP/SL比率を取得

        Returns:
            float: 推奨比率（範囲の中央値）
        """
        return (
            self.tp_sl_ratio_range[0] + self.tp_sl_ratio_range[1]
        ) / 2

    @classmethod
    def create_universal(
        cls,
        config: UnifiedBotConfig | None = None,
    ) -> TradingPlan:
        """UnifiedBotConfigからUNIVERSALプランを生成

        唯一のTradingPlan生成手段。全モジュールはこのファクトリ
        メソッドを通じてプランを取得する。

        Args:
            config: ボット設定（Noneの場合はデフォルト）

        Returns:
            TradingPlan: UNIVERSALトレーディングプラン
        """
        if config is None:
            from autotrader.decision.unified.config import (
                UnifiedBotConfig,
            )
            config = UnifiedBotConfig()
        _confirm = [
            tf for tf in config.timeframes
            if tf not in {
                config.default_primary_tf,
                config.default_entry_tf,
            }
        ]
        return cls(
            mode=TradingStrategyMode.UNIVERSAL,
            primary_tf=config.default_primary_tf,
            entry_tf=config.default_entry_tf,
            confirm_tfs=_confirm,
            manage_tf=config.default_manage_tf,
            max_holding_bars=config.default_max_holding_bars,
            tp_sl_ratio_range=config.default_tp_sl_ratio_range,
            selection_reason="UNIVERSAL（動的TF選択）",
        )


@dataclass(frozen=True)
class ModeSelectorConfig:
    """モード選択設定（UNIVERSALモード固定のため予備設定）"""


class TradingModeSelector:
    """トレーディングモード選択器

    UNIVERSALモード固定で全TFを動的評価するプランを返す。
    """

    def __init__(
        self,
        config: ModeSelectorConfig | None = None,
        timeframes: list[str] | None = None,
        bot_config: UnifiedBotConfig | None = None,
    ) -> None:
        """初期化

        Args:
            config: モード選択設定（互換性のため残存）
            timeframes: 使用TFリスト（互換性のため残存）
            bot_config: ボット設定（推奨）
        """
        self.config = config or ModeSelectorConfig()
        self._bot_config = bot_config

    def select(
        self,
        *args: any,
        **kwargs: any,
    ) -> TradingPlan:
        """トレーディングプランを選択

        常にUNIVERSALプランを返す。

        Returns:
            TradingPlan: UNIVERSALトレーディングプラン
        """
        return self.get_plan_for_mode(TradingStrategyMode.UNIVERSAL)

    def get_plan_for_mode(
        self,
        mode: TradingStrategyMode,
    ) -> TradingPlan:
        """指定モードのプランを取得（UNIVERSAL固定）

        Args:
            mode: トレーディングモード（UNIVERSALのみ有効）

        Returns:
            TradingPlan: UNIVERSALトレーディングプラン
        """
        return TradingPlan.create_universal(self._bot_config)
