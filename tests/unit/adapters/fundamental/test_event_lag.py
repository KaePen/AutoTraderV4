"""LLM処理ラグのテスト

イベント発表後、LLM分析結果（surprise_score, direction_bias）が
利用可能になるまでの遅延をバックテストで正しくシミュレートできるか検証。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autotrader.adapters.fundamental.backtest_provider import (
    BacktestFundamentalProvider,
    EventLLMRecord,
)


def _make_record(
    event_time: datetime,
    direction_bias: float = 0.5,
    surprise_score: float = 0.8,
    impact: str = "high",
    convergence_hours: float = 24.0,
) -> EventLLMRecord:
    """テスト用EventLLMRecord生成"""
    return EventLLMRecord(
        event_time=event_time,
        currency="USD",
        event_name="NFP",
        impact=impact,
        surprise_score=surprise_score,
        direction_bias=direction_bias,
        convergence_hours=convergence_hours,
        expected_volatility=1.5,
        trade_caution_level=1,
        is_holiday=False,
    )


def _make_provider(
    records: list[EventLLMRecord],
    lag_seconds: int = 30,
) -> BacktestFundamentalProvider:
    """テスト用Provider構築"""
    provider = BacktestFundamentalProvider(
        post_event_lag_seconds=lag_seconds,
    )
    sym = "USDJPY"
    provider._event_llm_records[sym] = records
    provider._event_llm_ts[sym] = [
        r.event_time.timestamp() for r in records
    ]
    return provider


class TestPostEventLag:
    """LLM処理ラグのテスト"""

    def test_bias_zeroed_during_lag(self) -> None:
        """ラグ期間中はdirection_biasとsurprise_scoreが0"""
        event_time = datetime(
            2024, 1, 5, 13, 30, tzinfo=timezone.utc
        )
        rec = _make_record(
            event_time,
            direction_bias=0.7,
            surprise_score=0.9,
        )
        provider = _make_provider([rec], lag_seconds=30)

        # イベント発表10秒後（ラグ内）
        now = datetime(
            2024, 1, 5, 13, 30, 10, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        # bias/surpriseは0（ラグ中はLLM分析未完了）
        assert ctx.direction_bias == 0.0
        assert ctx.surprise_score == 0.0
        # 注意度やボラティリティは利用可能
        assert ctx.event_caution_level == 1
        assert ctx.volatility_multiplier > 1.0

    def test_bias_available_after_lag(self) -> None:
        """ラグ経過後はbias/surpriseが正常に反映"""
        event_time = datetime(
            2024, 1, 5, 13, 30, tzinfo=timezone.utc
        )
        rec = _make_record(
            event_time,
            direction_bias=0.7,
            surprise_score=0.9,
        )
        provider = _make_provider([rec], lag_seconds=30)

        # イベント発表60秒後（ラグ超過）
        now = datetime(
            2024, 1, 5, 13, 31, 0, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        assert ctx.direction_bias == pytest.approx(0.7, abs=0.01)
        assert ctx.surprise_score == pytest.approx(0.9, abs=0.01)

    def test_lag_zero_means_immediate(self) -> None:
        """lag=0では即座にbias/surpriseが利用可能"""
        event_time = datetime(
            2024, 1, 5, 13, 30, tzinfo=timezone.utc
        )
        rec = _make_record(
            event_time,
            direction_bias=0.5,
            surprise_score=0.6,
        )
        provider = _make_provider([rec], lag_seconds=0)

        # イベント発表1秒後
        now = datetime(
            2024, 1, 5, 13, 30, 1, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        assert ctx.direction_bias == pytest.approx(0.5, abs=0.01)
        assert ctx.surprise_score == pytest.approx(0.6, abs=0.01)

    def test_pre_event_fields_unaffected(self) -> None:
        """ラグ期間中でもevent_caution/volatility/convergenceは正常"""
        event_time = datetime(
            2024, 1, 5, 13, 30, tzinfo=timezone.utc
        )
        rec = _make_record(
            event_time,
            direction_bias=0.7,
            surprise_score=0.9,
            convergence_hours=24.0,
        )
        provider = _make_provider([rec], lag_seconds=60)

        # ラグ内（5秒後）
        now = datetime(
            2024, 1, 5, 13, 30, 5, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        # 事前情報は利用可能
        assert ctx.event_caution_level == 1
        assert ctx.volatility_multiplier > 1.0
        assert ctx.active_event_count == 1
        # convergence_progress は (1 - influence)
        # 発表直後なのでinfluenceは高く、progressは低い
        assert ctx.convergence_progress < 0.1

    def test_mixed_events_lag_boundary(self) -> None:
        """複数イベント: ラグ内/外が混在する場合"""
        # 古いイベント（ラグ超過済み）
        old_event = _make_record(
            datetime(2024, 1, 5, 12, 0, tzinfo=timezone.utc),
            direction_bias=-0.5,
            surprise_score=-0.6,
            convergence_hours=4.0,
        )
        # 新しいイベント（ラグ内）
        new_event = _make_record(
            datetime(2024, 1, 5, 13, 30, tzinfo=timezone.utc),
            direction_bias=0.8,
            surprise_score=0.9,
        )
        provider = _make_provider(
            [old_event, new_event], lag_seconds=30,
        )

        # 新イベント発表10秒後
        now = datetime(
            2024, 1, 5, 13, 30, 10, tzinfo=timezone.utc
        )
        ctx = provider.get_context(now, "USDJPY")

        # 古いイベントのbiasのみ反映（新イベントは0）
        # direction_bias は重み付き平均で古い方のbiasが寄与
        assert ctx.direction_bias < 0
