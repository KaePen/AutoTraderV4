"""ファンダメンタルデータスキーマ定義

経済イベント・ファンダメンタルコンテキストのデータクラス。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
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
        is_holiday: 休日イベントフラグ
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
    is_holiday: bool = False

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


@dataclass(frozen=True)
class FundamentalMemorySnapshot:
    """FundamentalMemory の不変スナップショット

    トレードロジックへの受け渡し用。
    FundamentalMemory.snapshot() で生成する。

    Attributes:
        event_bias: イベントバイアス蓄積値
        event_strength: イベント強度
        news_bias: ニュースバイアス蓄積値
        news_strength: ニュース強度
        composite_bias: 統合バイアス（加重平均）
        composite_confidence: 統合確信度
        disagreement: イベント/ニュース矛盾度
    """

    event_bias: float = 0.0
    event_strength: float = 0.0
    news_bias: float = 0.0
    news_strength: float = 0.0
    composite_bias: float = 0.0
    composite_confidence: float = 0.0
    disagreement: float = 0.0


class FundamentalMemory:
    """ファンダメンタルバイアスの蓄積記憶

    イベント/ニュースのバイアスをEMA方式で蓄積し、
    日次減衰で新陳代謝する。

    設計原則（破綻1修正）:
    - αは固定学習率。サプライズは信号強度に掛ける
    - 単一イベントで蓄積記憶が破壊されない

    Args:
        event_alpha: イベントEMA学習率
        news_alpha: ニュースEMA学習率
        event_daily_decay: イベント日次減衰率
        news_daily_decay: ニュース日次減衰率
    """

    def __init__(
        self,
        event_alpha: float = 0.25,
        news_alpha: float = 0.15,
        event_daily_decay: float = 0.95,
        news_daily_decay: float = 0.90,
    ) -> None:
        self._event_alpha = event_alpha
        self._news_alpha = news_alpha
        self._event_daily_decay = event_daily_decay
        self._news_daily_decay = news_daily_decay

        # 蓄積状態
        self.event_bias: float = 0.0
        self.event_strength: float = 0.0
        self.news_bias: float = 0.0
        self.news_strength: float = 0.0

        # 最終更新日（重複更新防止用）
        self.last_event_date: date | None = None
        self.last_news_date: date | None = None

    def update_event(
        self,
        direction_bias: float,
        surprise_score: float,
    ) -> None:
        """イベントでバイアスを更新

        signal = direction_bias × |surprise_score| として
        EMA方式で蓄積する。

        Args:
            direction_bias: 方向バイアス (-1~+1)
            surprise_score: サプライズスコア (-1~+1)
        """
        signal = direction_bias * abs(surprise_score)
        self.event_bias = (
            (1.0 - self._event_alpha) * self.event_bias
            + self._event_alpha * signal
        )
        # 強度は蓄積的に増加（上限1.0）
        self.event_strength = min(
            self.event_strength
            + abs(surprise_score) * 0.3,
            1.0,
        )

    def update_news(
        self,
        sentiment_score: float,
        confidence: float,
    ) -> None:
        """ニュースでバイアスを更新

        signal = sentiment_score × confidence として
        EMA方式で蓄積。低信頼度ニュースは自動的に
        弱い信号になる。

        Args:
            sentiment_score: センチメント (-1~+1)
            confidence: LLM信頼度 (0~1)
        """
        signal = sentiment_score * confidence
        self.news_bias = (
            (1.0 - self._news_alpha) * self.news_bias
            + self._news_alpha * signal
        )
        self.news_strength = min(
            self.news_strength + confidence * 0.1,
            1.0,
        )

    def apply_daily_decay(self, days: int = 1) -> None:
        """日次減衰を適用

        Args:
            days: 経過日数
        """
        event_factor = self._event_daily_decay ** days
        news_factor = self._news_daily_decay ** days

        self.event_strength *= event_factor
        self.news_strength *= news_factor

        # 十分小さくなったらリセット
        _RESET_THRESHOLD = 0.01
        if self.event_strength < _RESET_THRESHOLD:
            self.event_bias = 0.0
            self.event_strength = 0.0
        if self.news_strength < _RESET_THRESHOLD:
            self.news_bias = 0.0
            self.news_strength = 0.0

    @property
    def composite_bias(self) -> float:
        """統合バイアス（イベント+ニュースの強度加重平均）"""
        total = self.event_strength + self.news_strength
        if total < 0.01:
            return 0.0
        return (
            self.event_bias * self.event_strength
            + self.news_bias * self.news_strength
        ) / total

    @property
    def composite_confidence(self) -> float:
        """統合確信度"""
        return min(
            self.event_strength + self.news_strength, 1.0,
        )

    @property
    def disagreement(self) -> float:
        """イベントとニュースの矛盾度

        両ソースとも十分な強度がある場合のみ計算。
        """
        if (
            self.event_strength < 0.1
            or self.news_strength < 0.1
        ):
            return 0.0
        return abs(self.event_bias - self.news_bias)

    def snapshot(self) -> FundamentalMemorySnapshot:
        """不変スナップショットを生成

        Returns:
            FundamentalMemorySnapshot: 不変コピー
        """
        return FundamentalMemorySnapshot(
            event_bias=self.event_bias,
            event_strength=self.event_strength,
            news_bias=self.news_bias,
            news_strength=self.news_strength,
            composite_bias=self.composite_bias,
            composite_confidence=self.composite_confidence,
            disagreement=self.disagreement,
        )


# ── イベントLLM分析レコード ──────────────────────────

# インパクトレベル別重み（合成時に使用）
IMPACT_WEIGHT: dict[str, float] = {
    "high": 3.0,
    "medium": 1.0,
    "low": 0.3,
}

# 影響度の最小閾値（これ未満は無視）
INFLUENCE_THRESHOLD = 0.05

# 過去イベント検索の最大時間（時間）
MAX_LOOKBACK_HOURS = 72


@dataclass(frozen=True)
class EventLLMRecord:
    """イベントLLM分析結果（CSV1行に対応）

    Attributes:
        event_time: イベント発表時刻（UTC）
        currency: 対象通貨
        event_name: イベント名称
        impact: インパクトレベル
        surprise_score: サプライズスコア
        direction_bias: 方向バイアス
        convergence_hours: 影響収束推定時間
        expected_volatility: ボラティリティ倍率
        trade_caution_level: 取引注意度
        is_holiday: 休日イベントフラグ
    """

    event_time: datetime
    currency: str
    event_name: str
    impact: str
    surprise_score: float
    direction_bias: float
    convergence_hours: float
    expected_volatility: float
    trade_caution_level: int
    is_holiday: bool


def compute_influence(
    elapsed_hours: float,
    convergence_hours: float,
    decay_coefficient: float = 2.0,
) -> float:
    """時間減衰による残存影響度を計算

    指数減衰モデル: exp(-decay_coeff * elapsed / convergence)
    convergence_hours の約35%で影響半減。

    Args:
        elapsed_hours: イベントからの経過時間
        convergence_hours: 影響収束推定時間
        decay_coefficient: 減衰係数（大きいほど急速に減衰）

    Returns:
        float: 残存影響度 (0.0~1.0)
    """
    if convergence_hours <= 0:
        return 0.0
    if elapsed_hours < 0:
        return 0.0
    if elapsed_hours >= convergence_hours:
        return 0.0
    ratio = elapsed_hours / convergence_hours
    return math.exp(-decay_coefficient * ratio)
