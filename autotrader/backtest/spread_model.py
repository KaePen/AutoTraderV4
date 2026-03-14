"""スプレッド分布モデル

指標発表時等のテールイベントをバックテストでシミュレーションする。
通常時は実スプレッドデータまたは固定値を使用し、
指標発表時はログ正規分布でテール部分をモデリングする。
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpreadModelConfig:
    """スプレッド分布モデル設定

    Attributes:
        enabled: モデル有効化
        event_multiplier_low: LOW重要度イベントのスプレッド乗数
        event_multiplier_med: MEDIUM重要度イベントのスプレッド乗数
        event_multiplier_high: HIGH重要度イベントのスプレッド乗数
        event_window_minutes: イベント前後のウィンドウ（分）
        lognormal_sigma: ログ正規分布のシグマ（テール幅）
        seed: 乱数シード（再現性用、None=ランダム）
    """

    enabled: bool = False
    event_multiplier_low: float = 1.5
    event_multiplier_med: float = 2.0
    event_multiplier_high: float = 3.0
    event_window_minutes: int = 30
    lognormal_sigma: float = 0.3
    seed: int | None = 42


@dataclass
class EconomicEvent:
    """経済指標イベント

    Attributes:
        timestamp: 発表時刻
        importance: 重要度（LOW/MEDIUM/HIGH）
        name: イベント名
    """

    timestamp: datetime
    importance: str = "LOW"
    name: str = ""


class SpreadDistributionModel:
    """スプレッド分布モデル

    経済指標発表時のスプレッド拡大をモデリングし、
    バックテストでよりリアルなスプレッド環境を再現する。
    """

    def __init__(
        self,
        config: SpreadModelConfig | None = None,
    ) -> None:
        self._config = config or SpreadModelConfig()
        self._events: list[EconomicEvent] = []
        self._rng = random.Random(self._config.seed)

    @property
    def config(self) -> SpreadModelConfig:
        """設定"""
        return self._config

    def set_events(
        self, events: list[EconomicEvent],
    ) -> None:
        """経済指標イベントリストを設定

        Args:
            events: イベントリスト（時系列順）
        """
        self._events = sorted(
            events, key=lambda e: e.timestamp,
        )

    def get_spread_multiplier(
        self,
        timestamp: datetime,
        base_spread_pips: float,
    ) -> float:
        """指定時刻のスプレッド乗数を取得

        Args:
            timestamp: 対象時刻
            base_spread_pips: 基本スプレッド（pips）

        Returns:
            float: スプレッド乗数（1.0=変更なし）
        """
        if not self._config.enabled:
            return 1.0

        if not self._events:
            return 1.0

        # 最も近いイベントを探す
        nearest_event = self._find_nearest_event(timestamp)
        if nearest_event is None:
            return 1.0

        # イベントからの距離（分）
        delta_min = abs(
            (timestamp - nearest_event.timestamp).total_seconds()
            / 60.0
        )

        if delta_min > self._config.event_window_minutes:
            return 1.0

        # 重要度別の基本乗数
        base_mult = self._get_importance_multiplier(
            nearest_event.importance,
        )

        # 距離による減衰（イベントに近いほど影響大）
        decay = 1.0 - (
            delta_min / self._config.event_window_minutes
        )
        decay = max(decay, 0.0)

        # ログ正規分布によるランダム変動
        lognormal_factor = self._rng.lognormvariate(
            0.0, self._config.lognormal_sigma,
        )

        # 最終乗数: 1.0 + (基本乗数-1.0) × 減衰 × ランダム変動
        multiplier = 1.0 + (base_mult - 1.0) * decay * lognormal_factor

        return max(multiplier, 1.0)

    def get_adjusted_spread(
        self,
        timestamp: datetime,
        base_spread_pips: float,
    ) -> float:
        """調整後スプレッドを取得

        Args:
            timestamp: 対象時刻
            base_spread_pips: 基本スプレッド（pips）

        Returns:
            float: 調整後スプレッド（pips）
        """
        mult = self.get_spread_multiplier(
            timestamp, base_spread_pips,
        )
        return base_spread_pips * mult

    def _find_nearest_event(
        self, timestamp: datetime,
    ) -> EconomicEvent | None:
        """最も近いイベントを探索

        Args:
            timestamp: 対象時刻

        Returns:
            EconomicEvent | None: 最近接イベント
        """
        if not self._events:
            return None

        nearest = None
        min_delta = float("inf")

        for event in self._events:
            delta = abs(
                (timestamp - event.timestamp).total_seconds()
            )
            if delta < min_delta:
                min_delta = delta
                nearest = event

            # イベント時刻がtimestampを超えたら打ち切り
            if event.timestamp > timestamp:
                break

        # ウィンドウ外なら None
        if (
            min_delta
            > self._config.event_window_minutes * 60
        ):
            return None

        return nearest

    def _get_importance_multiplier(
        self, importance: str,
    ) -> float:
        """重要度別のスプレッド乗数を取得

        Args:
            importance: 重要度（LOW/MEDIUM/HIGH）

        Returns:
            float: 基本乗数
        """
        importance_upper = importance.upper()
        if importance_upper == "HIGH":
            return self._config.event_multiplier_high
        if importance_upper in ("MEDIUM", "MED"):
            return self._config.event_multiplier_med
        return self._config.event_multiplier_low
