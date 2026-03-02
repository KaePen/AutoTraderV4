"""エントリータイムフレーム解決モジュール

UNIVERSALモードで動的にエントリー判断を行う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autotrader.core.enums import TradingStrategyMode

if TYPE_CHECKING:
    from autotrader.decision.unified.config import UnifiedBotConfig


@dataclass(frozen=True)
class EntryConfig:
    """エントリー設定

    Attributes:
        primary_tf: 主要時間足（シグナル判断の基準）
        entry_tf: エントリー時間足（タイミング判断）
        confirm_tfs: 確認用時間足リスト
        min_score_threshold: 最小スコア閾値
    """

    primary_tf: str
    entry_tf: str
    confirm_tfs: list[str]
    min_score_threshold: float


@dataclass(frozen=True)
class EntryDecision:
    """エントリー判定結果

    Attributes:
        should_enter: エントリーすべきか
        entry_tf: エントリー時間足
        direction: 方向 (BUY/SELL/HOLD)
        score: スコア
        reasoning: 判断理由
    """

    should_enter: bool
    entry_tf: str | None
    direction: str
    score: float
    reasoning: str


class EntryTimeframeResolver:
    """エントリータイムフレーム解決器

    UNIVERSALモードで任意のTF確定時にエントリー判断を行う。
    """

    def __init__(
        self,
        bot_config: UnifiedBotConfig | None = None,
    ) -> None:
        """初期化

        Args:
            bot_config: ボット設定（Noneの場合デフォルト）
        """
        if bot_config is None:
            from autotrader.decision.unified.config import (
                UnifiedBotConfig,
            )
            bot_config = UnifiedBotConfig()
        _confirm = [
            tf for tf in bot_config.timeframes
            if tf not in {
                bot_config.default_primary_tf,
                bot_config.default_entry_tf,
            }
        ]
        self._config = EntryConfig(
            primary_tf=bot_config.default_primary_tf,
            entry_tf=bot_config.default_entry_tf,
            confirm_tfs=_confirm,
            min_score_threshold=4.0,
        )

    def get_entry_config(
        self,
        mode: TradingStrategyMode | None = None,
    ) -> EntryConfig:
        """エントリー設定を取得

        Args:
            mode: 互換性のため残存（未使用）

        Returns:
            EntryConfig: UNIVERSALエントリー設定
        """
        return self._config

    def should_check_entry(
        self,
        mode: TradingStrategyMode | None = None,
        completed_tf: str = "",
    ) -> bool:
        """エントリーチェックすべきかを判定

        UNIVERSALモードは任意のTF確定時に常にTrueを返す。

        Args:
            mode: 互換性のため残存（未使用）
            completed_tf: 確定した時間足（未使用）

        Returns:
            bool: 常にTrue
        """
        return True

    def resolve(
        self,
        mode: TradingStrategyMode | None = None,
        completed_tf: str = "",
        tf_directions: dict[str, str] | None = None,
        tf_scores: dict[str, float] | None = None,
    ) -> EntryDecision:
        """エントリー判定を解決（UNIVERSALモード固定）

        Args:
            mode: 互換性のため残存（未使用）
            completed_tf: 確定した時間足
            tf_directions: TF別方向 (BUY/SELL/HOLD)
            tf_scores: TF別スコア

        Returns:
            EntryDecision: エントリー判定結果
        """
        tf_directions = tf_directions or {}
        tf_scores = tf_scores or {}
        config = self._config

        # 任意TFでエントリーチェック
        entry_direction = tf_directions.get(completed_tf, "HOLD")
        if entry_direction == "HOLD":
            return EntryDecision(
                should_enter=False,
                entry_tf=completed_tf,
                direction="HOLD",
                score=0.0,
                reasoning=f"UNIVERSAL: {completed_tf}がHOLD",
            )

        entry_score = tf_scores.get(completed_tf, 0.0)
        if entry_score < config.min_score_threshold:
            return EntryDecision(
                should_enter=False,
                entry_tf=completed_tf,
                direction=entry_direction,
                score=entry_score,
                reasoning=(
                    f"スコア不足: "
                    f"{entry_score:.2f} < "
                    f"{config.min_score_threshold}"
                ),
            )

        return EntryDecision(
            should_enter=True,
            entry_tf=completed_tf,
            direction=entry_direction,
            score=entry_score,
            reasoning=(
                f"UNIVERSALモード: tf={completed_tf}, "
                f"score={entry_score:.2f}"
            ),
        )

    def get_all_required_tfs(
        self,
        mode: TradingStrategyMode | None = None,
    ) -> list[str]:
        """UNIVERSALモードで必要な全時間足を取得

        Args:
            mode: 互換性のため残存（未使用）

        Returns:
            list[str]: 必要な時間足リスト（重複なし）
        """
        config = self._config
        tfs = {config.primary_tf, config.entry_tf}
        tfs.update(config.confirm_tfs)
        return list(tfs)
