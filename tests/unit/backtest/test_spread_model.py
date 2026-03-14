"""SpreadDistributionModel のユニットテスト"""

from __future__ import annotations

from datetime import datetime

import pytest

from autotrader.backtest.spread_model import (
    EconomicEvent,
    SpreadDistributionModel,
    SpreadModelConfig,
)


class TestSpreadDistributionModel:
    """SpreadDistributionModel のテスト"""

    def test_disabled(self) -> None:
        """無効化時は乗数1.0"""
        model = SpreadDistributionModel(
            SpreadModelConfig(enabled=False),
        )
        mult = model.get_spread_multiplier(
            datetime(2025, 1, 1, 12, 0),
            1.5,
        )
        assert mult == 1.0

    def test_no_events(self) -> None:
        """イベントなしでは乗数1.0"""
        model = SpreadDistributionModel(
            SpreadModelConfig(enabled=True),
        )
        mult = model.get_spread_multiplier(
            datetime(2025, 1, 1, 12, 0),
            1.5,
        )
        assert mult == 1.0

    def test_near_high_impact_event(self) -> None:
        """HIGHイベント直前はスプレッド拡大"""
        model = SpreadDistributionModel(
            SpreadModelConfig(
                enabled=True,
                event_multiplier_high=3.0,
                seed=42,
            ),
        )
        model.set_events([
            EconomicEvent(
                timestamp=datetime(2025, 1, 1, 12, 30),
                importance="HIGH",
                name="NFP",
            ),
        ])
        # イベント5分前
        mult = model.get_spread_multiplier(
            datetime(2025, 1, 1, 12, 25),
            1.5,
        )
        # 乗数は1.0より大きい
        assert mult > 1.0

    def test_far_from_event(self) -> None:
        """イベントから遠い場合は乗数1.0"""
        model = SpreadDistributionModel(
            SpreadModelConfig(
                enabled=True,
                event_window_minutes=30,
                seed=42,
            ),
        )
        model.set_events([
            EconomicEvent(
                timestamp=datetime(2025, 1, 1, 12, 30),
                importance="HIGH",
                name="NFP",
            ),
        ])
        # イベントの2時間前
        mult = model.get_spread_multiplier(
            datetime(2025, 1, 1, 10, 30),
            1.5,
        )
        assert mult == 1.0

    def test_low_impact_smaller_multiplier(self) -> None:
        """LOWイベントはHIGHより影響小"""
        model_low = SpreadDistributionModel(
            SpreadModelConfig(
                enabled=True,
                event_multiplier_low=1.5,
                event_multiplier_high=3.0,
                seed=42,
            ),
        )
        model_high = SpreadDistributionModel(
            SpreadModelConfig(
                enabled=True,
                event_multiplier_low=1.5,
                event_multiplier_high=3.0,
                seed=42,
            ),
        )
        ts = datetime(2025, 1, 1, 12, 25)
        event_ts = datetime(2025, 1, 1, 12, 30)

        model_low.set_events([
            EconomicEvent(
                timestamp=event_ts,
                importance="LOW",
            ),
        ])
        model_high.set_events([
            EconomicEvent(
                timestamp=event_ts,
                importance="HIGH",
            ),
        ])

        mult_low = model_low.get_spread_multiplier(ts, 1.5)
        mult_high = model_high.get_spread_multiplier(ts, 1.5)
        # 同じseedなのでランダム要素は同じ
        # HIGH乗数の方が大きい
        assert mult_high >= mult_low

    def test_get_adjusted_spread(self) -> None:
        """調整後スプレッド取得"""
        model = SpreadDistributionModel(
            SpreadModelConfig(enabled=False),
        )
        spread = model.get_adjusted_spread(
            datetime(2025, 1, 1, 12, 0),
            1.5,
        )
        assert spread == 1.5

    def test_reproducibility(self) -> None:
        """同じseedで再現性"""
        cfg = SpreadModelConfig(
            enabled=True,
            seed=123,
        )
        model1 = SpreadDistributionModel(cfg)
        model2 = SpreadDistributionModel(cfg)

        event = EconomicEvent(
            timestamp=datetime(2025, 1, 1, 12, 30),
            importance="HIGH",
        )
        model1.set_events([event])
        model2.set_events([event])

        ts = datetime(2025, 1, 1, 12, 25)
        mult1 = model1.get_spread_multiplier(ts, 1.5)
        mult2 = model2.get_spread_multiplier(ts, 1.5)
        assert mult1 == mult2

    def test_multiple_events(self) -> None:
        """複数イベント時は最近接を使用"""
        model = SpreadDistributionModel(
            SpreadModelConfig(
                enabled=True,
                event_window_minutes=30,
                seed=42,
            ),
        )
        model.set_events([
            EconomicEvent(
                timestamp=datetime(2025, 1, 1, 10, 0),
                importance="LOW",
            ),
            EconomicEvent(
                timestamp=datetime(2025, 1, 1, 14, 0),
                importance="HIGH",
            ),
        ])
        # 10時のイベントに近い
        mult_near_first = model.get_spread_multiplier(
            datetime(2025, 1, 1, 9, 55),
            1.5,
        )
        assert mult_near_first > 1.0

        # 両イベントから遠い
        mult_far = model.get_spread_multiplier(
            datetime(2025, 1, 1, 12, 0),
            1.5,
        )
        assert mult_far == 1.0
