"""FundamentalMemory テスト

EMA方式バイアス蓄積・減衰・新陳代謝の検証。
"""

from __future__ import annotations

import pytest

from autotrader.adapters.fundamental.schemas import (
    FundamentalMemory,
)


class TestFundamentalMemoryUpdateEvent:
    """イベント更新テスト"""

    def test_single_event_updates_bias(self) -> None:
        """単一イベントでバイアスが更新される"""
        mem = FundamentalMemory()
        mem.update_event(direction_bias=0.8, surprise_score=0.7)

        # signal = 0.8 * 0.7 = 0.56
        # bias = (1-0.25)*0 + 0.25*0.56 = 0.14
        assert mem.event_bias == pytest.approx(0.14, abs=0.01)
        assert mem.event_strength > 0.0

    def test_accumulated_memory_not_destroyed_by_single_surprise(
        self,
    ) -> None:
        """蓄積された記憶が単一サプライズで破壊されない（破綻1修正）"""
        mem = FundamentalMemory()

        # 2週間分のUSD買いバイアス蓄積をシミュレート
        for _ in range(14):
            mem.update_event(
                direction_bias=0.8, surprise_score=0.5,
            )

        bias_before = mem.event_bias
        assert bias_before > 0.3  # しっかり蓄積されている

        # 単一の逆方向サプライズ
        mem.update_event(
            direction_bias=-0.3, surprise_score=0.8,
        )

        # 記憶が完全に反転していないことを確認
        assert mem.event_bias > 0.0, (
            f"蓄積バイアス{bias_before:.3f}が"
            f"単一イベントで反転: {mem.event_bias:.3f}"
        )

    def test_multiple_events_converge(self) -> None:
        """同方向の複数イベントでバイアスが収束する"""
        mem = FundamentalMemory()
        for _ in range(20):
            mem.update_event(
                direction_bias=0.6, surprise_score=0.5,
            )
        # EMA収束値 ≈ signal = 0.6 * 0.5 = 0.3
        assert mem.event_bias == pytest.approx(0.3, abs=0.05)


class TestFundamentalMemoryUpdateNews:
    """ニュース更新テスト"""

    def test_high_confidence_news_has_impact(self) -> None:
        """高信頼度ニュースは影響を与える"""
        mem = FundamentalMemory()
        mem.update_news(
            sentiment_score=0.6, confidence=0.8,
        )
        # signal = 0.6 * 0.8 = 0.48
        # bias = 0.15 * 0.48 = 0.072
        assert abs(mem.news_bias) > 0.05

    def test_low_confidence_news_is_ignored(self) -> None:
        """低信頼度ニュース（憶測記事）はほぼ無視される"""
        mem = FundamentalMemory()
        mem.update_news(
            sentiment_score=0.5, confidence=0.2,
        )
        # signal = 0.5 * 0.2 = 0.1
        # bias = 0.15 * 0.1 = 0.015
        assert abs(mem.news_bias) < 0.02


class TestFundamentalMemoryDecay:
    """日次減衰テスト"""

    def test_event_strength_decays(self) -> None:
        """イベント強度が日次減衰する"""
        mem = FundamentalMemory()
        mem.update_event(direction_bias=0.8, surprise_score=0.7)
        initial_strength = mem.event_strength

        mem.apply_daily_decay(days=14)
        # 0.95^14 ≈ 0.488
        expected = initial_strength * (0.95 ** 14)
        assert mem.event_strength == pytest.approx(
            expected, abs=0.01,
        )

    def test_news_decays_faster_than_events(self) -> None:
        """ニュースはイベントより速く減衰する"""
        mem = FundamentalMemory()
        mem.update_event(direction_bias=0.5, surprise_score=0.5)
        mem.update_news(sentiment_score=0.5, confidence=0.8)

        event_str_0 = mem.event_strength
        news_str_0 = mem.news_strength

        mem.apply_daily_decay(days=7)

        event_ratio = mem.event_strength / event_str_0
        news_ratio = mem.news_strength / news_str_0

        # ニュースの方が速く減衰する
        assert news_ratio < event_ratio

    def test_very_small_strength_resets_to_zero(self) -> None:
        """十分小さい強度はゼロにリセットされる"""
        mem = FundamentalMemory()
        mem.update_event(direction_bias=0.8, surprise_score=0.1)

        mem.apply_daily_decay(days=200)
        assert mem.event_bias == 0.0
        assert mem.event_strength == 0.0


class TestFundamentalMemoryComposite:
    """統合バイアス計算テスト"""

    def test_composite_bias_weighted_average(self) -> None:
        """統合バイアスは強度加重平均"""
        mem = FundamentalMemory()
        mem.event_bias = 0.8
        mem.event_strength = 0.6
        mem.news_bias = -0.3
        mem.news_strength = 0.3

        # (0.8*0.6 + -0.3*0.3) / (0.6+0.3) = 0.39/0.9 ≈ 0.433
        assert mem.composite_bias == pytest.approx(
            0.433, abs=0.01,
        )

    def test_composite_bias_zero_when_no_data(self) -> None:
        """データなしの場合はゼロ"""
        mem = FundamentalMemory()
        assert mem.composite_bias == 0.0
        assert mem.composite_confidence == 0.0

    def test_disagreement_detects_conflict(self) -> None:
        """イベントとニュースの矛盾を検知する（破綻6修正）"""
        mem = FundamentalMemory()
        mem.event_bias = 0.8
        mem.event_strength = 0.5
        mem.news_bias = -0.5
        mem.news_strength = 0.5

        assert mem.disagreement == pytest.approx(1.3, abs=0.01)

    def test_disagreement_zero_when_one_source_weak(self) -> None:
        """片方のソースが弱い場合は矛盾なし"""
        mem = FundamentalMemory()
        mem.event_bias = 0.8
        mem.event_strength = 0.5
        mem.news_bias = -0.5
        mem.news_strength = 0.05  # 弱い

        assert mem.disagreement == 0.0


class TestFundamentalMemorySnapshot:
    """スナップショット（不変コピー）テスト"""

    def test_snapshot_creates_frozen_copy(self) -> None:
        """スナップショットは不変コピーを返す"""
        mem = FundamentalMemory()
        mem.update_event(direction_bias=0.5, surprise_score=0.5)
        snap = mem.snapshot()

        assert snap.event_bias == mem.event_bias
        assert snap.composite_bias == mem.composite_bias

    def test_snapshot_not_affected_by_later_updates(self) -> None:
        """スナップショット後の更新がスナップショットに影響しない"""
        mem = FundamentalMemory()
        mem.update_event(direction_bias=0.5, surprise_score=0.5)
        snap = mem.snapshot()
        bias_at_snapshot = snap.event_bias

        # 追加更新
        mem.update_event(direction_bias=-0.8, surprise_score=0.9)

        assert snap.event_bias == bias_at_snapshot
