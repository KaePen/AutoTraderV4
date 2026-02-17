"""エントリータイムフレーム解決モジュール

モード別にエントリー判断を行う時間足を決定する。
"""

from __future__ import annotations

from dataclasses import dataclass

from autotrader.core.enums import TradingStrategyMode


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


# モード別エントリー設定
MODE_ENTRY_CONFIGS: dict[TradingStrategyMode, EntryConfig] = {
    TradingStrategyMode.SCALPING: EntryConfig(
        primary_tf="M5",
        entry_tf="M1",
        confirm_tfs=["M15"],
        min_score_threshold=3.0,
    ),
    TradingStrategyMode.DAY_TRADE: EntryConfig(
        primary_tf="M15",
        entry_tf="M5",
        confirm_tfs=["H1", "H4"],
        min_score_threshold=4.0,
    ),
    TradingStrategyMode.SWING: EntryConfig(
        primary_tf="H4",
        entry_tf="H1",
        confirm_tfs=["D1"],
        min_score_threshold=5.0,
    ),
}


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

    モードに応じたエントリー判断を行う。

    判断ロジック:
    1. モードからentry_tfを決定
    2. entry_tf足確定時のみエントリー判断
    3. primary_tf/confirm_tfsの方向整合性をチェック
    4. スコア閾値で最終判断
    """

    def __init__(self) -> None:
        """初期化"""
        self._configs = MODE_ENTRY_CONFIGS

    def get_entry_config(
        self,
        mode: TradingStrategyMode,
    ) -> EntryConfig:
        """モード別エントリー設定を取得

        Args:
            mode: トレーディングモード

        Returns:
            EntryConfig: エントリー設定
        """
        return self._configs.get(mode, self._configs[TradingStrategyMode.DAY_TRADE])

    def should_check_entry(
        self,
        mode: TradingStrategyMode,
        completed_tf: str,
    ) -> bool:
        """エントリーチェックすべきかを判定

        Args:
            mode: トレーディングモード
            completed_tf: 確定した時間足

        Returns:
            bool: entry_tf足確定時はTrue
        """
        config = self.get_entry_config(mode)
        return completed_tf == config.entry_tf

    def resolve(
        self,
        mode: TradingStrategyMode,
        completed_tf: str,
        tf_directions: dict[str, str],
        tf_scores: dict[str, float],
    ) -> EntryDecision:
        """エントリー判定を解決

        Args:
            mode: トレーディングモード
            completed_tf: 確定した時間足
            tf_directions: TF別方向 (BUY/SELL/HOLD)
            tf_scores: TF別スコア

        Returns:
            EntryDecision: エントリー判定結果
        """
        config = self.get_entry_config(mode)

        # entry_tf足確定時のみ判断
        if completed_tf != config.entry_tf:
            return EntryDecision(
                should_enter=False,
                entry_tf=None,
                direction="HOLD",
                score=0.0,
                reasoning=f"entry_tf({config.entry_tf})未確定",
            )

        # 方向の取得
        entry_direction = tf_directions.get(config.entry_tf, "HOLD")
        primary_direction = tf_directions.get(config.primary_tf, "HOLD")

        # HOLDなら見送り
        if entry_direction == "HOLD":
            return EntryDecision(
                should_enter=False,
                entry_tf=config.entry_tf,
                direction="HOLD",
                score=0.0,
                reasoning="entry_tfがHOLD",
            )

        # primary_tfとの整合性チェック
        if primary_direction != "HOLD" and primary_direction != entry_direction:
            return EntryDecision(
                should_enter=False,
                entry_tf=config.entry_tf,
                direction="HOLD",
                score=0.0,
                reasoning=f"方向不一致: entry={entry_direction}, primary={primary_direction}",
            )

        # confirm_tfsの整合性チェック
        confirm_aligned = 0
        confirm_conflict = 0
        for tf in config.confirm_tfs:
            direction = tf_directions.get(tf, "HOLD")
            if direction == entry_direction:
                confirm_aligned += 1
            elif direction != "HOLD":
                confirm_conflict += 1

        # 過半数の確認TFが逆方向ならスキップ
        if confirm_conflict > confirm_aligned and len(config.confirm_tfs) > 0:
            return EntryDecision(
                should_enter=False,
                entry_tf=config.entry_tf,
                direction="HOLD",
                score=0.0,
                reasoning=f"confirm_tf逆方向優勢: aligned={confirm_aligned}, conflict={confirm_conflict}",
            )

        # スコア計算
        entry_score = tf_scores.get(config.entry_tf, 0.0)
        primary_score = tf_scores.get(config.primary_tf, 0.0)

        # 重み付けスコア
        weighted_score = entry_score * 2.0 + primary_score * 3.0
        for tf in config.confirm_tfs:
            weighted_score += tf_scores.get(tf, 0.0) * 1.5

        # 正規化（最大で10点程度に収める）
        total_weight = 2.0 + 3.0 + len(config.confirm_tfs) * 1.5
        normalized_score = weighted_score / total_weight * 5.0

        # 閾値判定
        if normalized_score < config.min_score_threshold:
            return EntryDecision(
                should_enter=False,
                entry_tf=config.entry_tf,
                direction=entry_direction,
                score=normalized_score,
                reasoning=f"スコア不足: {normalized_score:.2f} < {config.min_score_threshold}",
            )

        # エントリー決定
        return EntryDecision(
            should_enter=True,
            entry_tf=config.entry_tf,
            direction=entry_direction,
            score=normalized_score,
            reasoning=f"{mode.value}モード: score={normalized_score:.2f}, aligned_confirm={confirm_aligned}",
        )

    def get_all_required_tfs(
        self,
        mode: TradingStrategyMode,
    ) -> list[str]:
        """モードで必要な全時間足を取得

        Args:
            mode: トレーディングモード

        Returns:
            list[str]: 必要な時間足リスト（重複なし）
        """
        config = self.get_entry_config(mode)
        tfs = {config.primary_tf, config.entry_tf}
        tfs.update(config.confirm_tfs)
        return list(tfs)
