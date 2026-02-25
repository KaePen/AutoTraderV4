"""ファンダメンタルデータスキーマ定義

経済イベント・ファンダメンタルコンテキストのデータクラス。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ImpactLevel(str, Enum):
    """経済イベントの影響度レベル"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EventSource(str, Enum):
    """経済イベントのデータソース"""

    MT5 = "MT5"
    FOREX_FACTORY = "forex_factory"
    GDELT = "gdelt"
    RSS = "rss"


@dataclass(frozen=True)
class EconomicEvent:
    """経済イベントデータ

    Args:
        event_id: イベントUUID
        event_time: イベント予定時刻（UTC）
        currency: 通貨コード（USD, JPY等）
        symbol: 関連シンボル（USDJPY等、Noneの場合あり）
        event_name: イベント名称
        impact: 影響度レベル
        actual: 実績値（発表後のみ）
        forecast: 予測値
        previous: 前回値
        source: データソース
        fetched_at: データ取得時刻
    """

    event_id: str
    event_time: datetime
    currency: str
    event_name: str
    impact: ImpactLevel
    source: EventSource
    fetched_at: datetime
    symbol: str | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None

    @property
    def is_released(self) -> bool:
        """実績値が発表済みかどうか

        Returns:
            bool: 発表済みならTrue
        """
        return self.actual is not None

    @property
    def surprise_magnitude(self) -> float | None:
        """実績と予測の乖離度を計算

        Returns:
            float | None: 乖離度（実績なし・予測なしはNone）
        """
        if self.actual is None or self.forecast is None:
            return None
        if self.forecast == 0:
            return None
        return (self.actual - self.forecast) / abs(self.forecast)

    def minutes_until(self, now: datetime) -> float:
        """現在時刻からイベントまでの分数

        Args:
            now: 現在時刻（UTC）

        Returns:
            float: イベントまでの分数（過去は負）
        """
        delta = self.event_time - now
        return delta.total_seconds() / 60


@dataclass(frozen=True)
class FundamentalContext:
    """ファンダメンタルコンテキスト（Phase 2）

    イベントLLMデータから計算された、トレードロジック向け
    消費者寄りインターフェース。

    全フィールドはデフォルト値がニュートラル（影響なし）。

    Phase 2 新フィールド:
        event_caution_level: 最大注意度 (0/1/2)
        is_holiday: 休日フラグ
        liquidity_factor: 流動性係数 (1.0=通常)
        volatility_multiplier: ボラ倍率 (1.0=通常)
        active_event_count: 影響中イベント数
        direction_bias: 合成方向バイアス (-1.0~+1.0)
        surprise_score: 合成サプライズ (-1.0~+1.0)
        convergence_progress: 収束進捗 (0.0~1.0)

    後方互換フィールド（live系で使用、Phase 3で移行）:
        macro_bias_score: マクロバイアス
        macro_bias_summary: マクロバイアス要約
        post_event_bias_score: 指標後バイアス
        post_event_summary: 指標後バイアス要約
        sentiment_score: センチメントスコア
    """

    # --- ガード系（HardGuard / Runner 向け） ---
    has_high_impact_within_30min: bool = False
    event_caution_level: int = 0
    is_holiday: bool = False

    # --- 流動性・ボラティリティ系 ---
    liquidity_factor: float = 1.0
    volatility_multiplier: float = 1.0
    active_event_count: int = 0

    # --- 方向性系 ---
    direction_bias: float = 0.0
    surprise_score: float = 0.0

    # --- ポジション管理系 ---
    convergence_progress: float = 1.0

    # --- 直近イベント情報 ---
    upcoming_events: list[dict] = field(default_factory=list)

    # --- 後方互換フィールド（Phase 3で移行予定） ---
    macro_bias_score: float = 0.0
    macro_bias_summary: str = ""
    post_event_bias_score: float = 0.0
    post_event_summary: str = ""
    sentiment_score: float = 0.0

    @classmethod
    def neutral(cls) -> FundamentalContext:
        """ニュートラルなコンテキストを生成

        Returns:
            FundamentalContext: ニュートラルコンテキスト
        """
        return cls()

    def to_prompt_section(self) -> str:
        """プロンプト用のコンテキストセクションを生成

        Returns:
            str: プロンプト用文字列
        """
        lines = [
            "## ファンダメンタルコンテキスト",
            f"- 方向バイアス: {self.direction_bias:+.2f}",
            f"- 流動性: {self.liquidity_factor:.2f}",
            f"- ボラ倍率: {self.volatility_multiplier:.2f}",
            f"- 注意度: {self.event_caution_level}",
        ]
        if self.is_holiday:
            lines.append("- 休日影響あり")
        if self.upcoming_events:
            lines.append("- 直近イベント:")
            for ev in self.upcoming_events[:3]:
                lines.append(
                    f"  - {ev.get('name', '不明')} "
                    f"({ev.get('minutes_until', 0):.0f}分後)"
                )
        if self.has_high_impact_within_30min:
            lines.append(
                "WARNING: 30分以内に高インパクト指標あり"
            )
        return "\n".join(lines)
