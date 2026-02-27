"""DeterministicEventAnalyzer のテスト

LLMを使わない決定論的イベント分析クラスのテスト。
ヒューリスティック計算の正確性を検証する。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autotrader.adapters.fundamental.deterministic_event_analyzer import (
    DeterministicEventAnalyzer,
    _CCY_NAMES,
    _EVENT_JP,
    _INDICATOR_CATEGORIES,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)


def _make_event(
    event_name: str = "Non-Farm Employment Change",
    currency: str = "USD",
    impact: ImpactLevel = ImpactLevel.HIGH,
    actual: float | None = 250.0,
    forecast: float | None = 200.0,
    previous: float | None = 180.0,
    year: int = 2024,
    month: int = 1,
    day: int = 5,
) -> EconomicEvent:
    """テスト用イベントを生成"""
    return EconomicEvent(
        event_id="test_001",
        event_time=datetime(
            year, month, day, 13, 30,
            tzinfo=timezone.utc,
        ),
        currency=currency,
        event_name=event_name,
        impact=impact,
        source=EventSource.MT5,
        fetched_at=datetime.now(timezone.utc),
        actual=actual,
        forecast=forecast,
        previous=previous,
    )


class TestGetIndicatorCategory:
    """指標カテゴリ判定のテスト"""

    def test_monetary_policy(self) -> None:
        """金融政策指標は48時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "Federal Funds Rate"
            )
        )
        assert hours == 48.0
        assert cat == "金融政策"

    def test_employment(self) -> None:
        """雇用指標は36時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "Non-Farm Employment Change"
            )
        )
        assert hours == 36.0
        assert cat == "雇用"

    def test_gdp(self) -> None:
        """GDP指標は24時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "GDP q/q"
            )
        )
        assert hours == 24.0
        assert cat == "GDP"

    def test_inflation(self) -> None:
        """物価指標は20時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "CPI m/m"
            )
        )
        assert hours == 20.0
        assert cat == "物価"

    def test_trade_balance(self) -> None:
        """貿易収支は16時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "Trade Balance"
            )
        )
        assert hours == 16.0
        assert cat == "貿易収支"

    def test_retail_sales(self) -> None:
        """消費指標は12時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "Retail Sales m/m"
            )
        )
        assert hours == 12.0
        assert cat == "消費"

    def test_manufacturing(self) -> None:
        """製造業指標は10時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "ISM Manufacturing PMI"
            )
        )
        assert hours == 10.0
        assert cat == "製造業"

    def test_housing(self) -> None:
        """住宅指標は8時間ベース"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "Existing Home Sales"
            )
        )
        assert hours == 8.0
        assert cat == "住宅"

    def test_unknown_defaults_to_6h(self) -> None:
        """不明指標はデフォルト6時間"""
        hours, cat = (
            DeterministicEventAnalyzer._get_indicator_category(
                "Some Unknown Indicator"
            )
        )
        assert hours == 6.0
        assert cat == "その他"


class TestComputeConvergenceHours:
    """収束時間計算のテスト"""

    def test_high_impact_monetary(self) -> None:
        """HIGH金融政策: 48h × 1.0 + サプライズ調整"""
        event = _make_event(
            event_name="Federal Funds Rate",
            impact=ImpactLevel.HIGH,
        )
        hours = (
            DeterministicEventAnalyzer._compute_convergence_hours(
                event, surprise_score=0.5
            )
        )
        # 48 * 1.0 + 0.5 * 48 * 0.5 = 48 + 12 = 60
        assert hours == 60.0

    def test_medium_impact_employment(self) -> None:
        """MEDIUM雇用: 36h × 0.6 + サプライズ調整"""
        event = _make_event(
            event_name="Unemployment Rate",
            impact=ImpactLevel.MEDIUM,
        )
        hours = (
            DeterministicEventAnalyzer._compute_convergence_hours(
                event, surprise_score=0.0
            )
        )
        # 36 * 0.6 + 0 = 21.6
        assert hours == 21.6

    def test_low_impact_small_hours(self) -> None:
        """LOW: 小さな収束時間"""
        event = _make_event(
            event_name="GDP q/q",
            impact=ImpactLevel.LOW,
        )
        hours = (
            DeterministicEventAnalyzer._compute_convergence_hours(
                event, surprise_score=0.0
            )
        )
        # 24 * 0.2 + 0 = 4.8
        assert hours == 4.8

    def test_clipped_to_max_72(self) -> None:
        """最大72時間にクリップ"""
        event = _make_event(
            event_name="Federal Funds Rate",
            impact=ImpactLevel.HIGH,
        )
        hours = (
            DeterministicEventAnalyzer._compute_convergence_hours(
                event, surprise_score=1.0
            )
        )
        # 48 * 1.0 + 1.0 * 48 * 0.5 = 48 + 24 = 72
        assert hours <= 72.0

    def test_minimum_0_5(self) -> None:
        """最小0.5時間"""
        event = _make_event(
            event_name="Some Unknown Thing",
            impact=ImpactLevel.LOW,
        )
        hours = (
            DeterministicEventAnalyzer._compute_convergence_hours(
                event, surprise_score=0.0
            )
        )
        # 6 * 0.2 = 1.2 → >= 0.5
        assert hours >= 0.5


class TestComputeExpectedVolatility:
    """期待ボラティリティ計算のテスト"""

    def test_high_no_surprise(self) -> None:
        """HIGH・サプライズなし: 1.2"""
        event = _make_event(impact=ImpactLevel.HIGH)
        vol = (
            DeterministicEventAnalyzer._compute_expected_volatility(
                event, 0.0
            )
        )
        assert vol == 1.2

    def test_high_with_surprise(self) -> None:
        """HIGH・サプライズあり: 1.2 + 0.3*surprise"""
        event = _make_event(impact=ImpactLevel.HIGH)
        vol = (
            DeterministicEventAnalyzer._compute_expected_volatility(
                event, 0.5
            )
        )
        assert vol == pytest.approx(1.35)

    def test_medium_no_surprise(self) -> None:
        """MEDIUM・サプライズなし: 1.0"""
        event = _make_event(impact=ImpactLevel.MEDIUM)
        vol = (
            DeterministicEventAnalyzer._compute_expected_volatility(
                event, 0.0
            )
        )
        assert vol == 1.0

    def test_low_always_0_5(self) -> None:
        """LOW: 常に0.5"""
        event = _make_event(impact=ImpactLevel.LOW)
        vol = (
            DeterministicEventAnalyzer._compute_expected_volatility(
                event, 1.0
            )
        )
        assert vol == 0.5

    def test_max_2_0(self) -> None:
        """最大2.0にクリップ"""
        event = _make_event(impact=ImpactLevel.HIGH)
        vol = (
            DeterministicEventAnalyzer._compute_expected_volatility(
                event, 1.0
            )
        )
        # 1.2 + 0.3 = 1.5 → < 2.0
        assert vol <= 2.0


class TestComputeTradeCautionLevel:
    """取引注意度計算のテスト"""

    def test_high_returns_1(self) -> None:
        """HIGH → 注意(1)"""
        event = _make_event(impact=ImpactLevel.HIGH)
        assert (
            DeterministicEventAnalyzer._compute_trade_caution_level(
                event
            )
            == 1
        )

    def test_medium_returns_1(self) -> None:
        """MEDIUM → 注意(1)"""
        event = _make_event(impact=ImpactLevel.MEDIUM)
        assert (
            DeterministicEventAnalyzer._compute_trade_caution_level(
                event
            )
            == 1
        )

    def test_low_returns_0(self) -> None:
        """LOW → 通常(0)"""
        event = _make_event(impact=ImpactLevel.LOW)
        assert (
            DeterministicEventAnalyzer._compute_trade_caution_level(
                event
            )
            == 0
        )


class TestGenerateSummary:
    """サマリー生成のテスト"""

    def test_low_impact(self) -> None:
        """LOW: 簡潔なサマリー"""
        event = _make_event(impact=ImpactLevel.LOW)
        summary = DeterministicEventAnalyzer._generate_summary(
            "USDJPY", event, 0.1, 0.05
        )
        assert "低インパクト" in summary

    def test_high_positive_surprise(self) -> None:
        """HIGH・正サプライズ: 上昇圧力"""
        event = _make_event(
            event_name="Non-Farm Employment Change",
            impact=ImpactLevel.HIGH,
            actual=300.0,
            forecast=200.0,
        )
        summary = DeterministicEventAnalyzer._generate_summary(
            "USDJPY", event, 0.5, 0.4
        )
        assert "予想を上回り" in summary
        assert "上昇圧力" in summary

    def test_high_negative_surprise(self) -> None:
        """HIGH・負サプライズ: 下落圧力"""
        event = _make_event(
            event_name="Non-Farm Employment Change",
            impact=ImpactLevel.HIGH,
            actual=100.0,
            forecast=200.0,
        )
        summary = DeterministicEventAnalyzer._generate_summary(
            "USDJPY", event, -0.5, -0.4
        )
        assert "予想を下回り" in summary
        assert "下落圧力" in summary

    def test_max_200_chars(self) -> None:
        """200文字以内"""
        event = _make_event(impact=ImpactLevel.HIGH)
        summary = DeterministicEventAnalyzer._generate_summary(
            "USDJPY", event, 0.5, 0.4
        )
        assert len(summary) <= 200

    def test_volatility_warning_for_high(self) -> None:
        """HIGH: ボラティリティ注意喚起あり"""
        event = _make_event(impact=ImpactLevel.HIGH)
        summary = DeterministicEventAnalyzer._generate_summary(
            "USDJPY", event, 0.5, 0.4
        )
        assert "ボラティリティ" in summary


class TestAnalyzeEvent:
    """_analyze_event のテスト（LLM不使用を検証）"""

    def test_high_impact_no_llm(self) -> None:
        """HIGH でも LLM を呼ばない"""
        analyzer = DeterministicEventAnalyzer()
        event = _make_event(
            event_name="Non-Farm Employment Change",
            currency="USD",
            impact=ImpactLevel.HIGH,
            actual=300.0,
            forecast=200.0,
        )
        result = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )

        assert result["surprise_score"] != 0.0
        assert result["direction_bias"] != 0.0
        assert result["convergence_hours"] > 0
        assert result["expected_volatility"] > 0
        assert result["trade_caution_level"] == 1
        assert result["is_holiday"] is False
        assert len(result["summary"]) > 0

    def test_medium_impact_no_llm(self) -> None:
        """MEDIUM でも LLM を呼ばない"""
        analyzer = DeterministicEventAnalyzer()
        event = _make_event(
            event_name="CPI m/m",
            currency="USD",
            impact=ImpactLevel.MEDIUM,
            actual=0.4,
            forecast=0.3,
        )
        result = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )

        assert "convergence_hours" in result
        assert "expected_volatility" in result
        assert "summary" in result
        assert result["is_holiday"] is False

    def test_low_impact_consistent(self) -> None:
        """LOW: 親クラスと同じ結果"""
        analyzer = DeterministicEventAnalyzer()
        event = _make_event(
            event_name="Building Permits",
            currency="USD",
            impact=ImpactLevel.LOW,
            actual=1.5,
            forecast=1.4,
        )
        result = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )

        assert result["convergence_hours"] == 1.0
        assert result["expected_volatility"] == 0.5
        assert result["trade_caution_level"] == 0
        assert "低インパクト" in result["summary"]

    def test_holiday_event(self) -> None:
        """休日イベント: 固定値"""
        analyzer = DeterministicEventAnalyzer()
        event = _make_event(
            event_name="Bank Holiday",
            currency="USD",
            impact=ImpactLevel.LOW,
            actual=None,
            forecast=None,
        )
        result = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )

        assert result["surprise_score"] == 0.0
        assert result["direction_bias"] == 0.0
        assert result["is_holiday"] is True

    def test_all_csv_columns_present(self) -> None:
        """全CSVカラムが結果に含まれる"""
        from autotrader.adapters.fundamental.llm_event_generator import (
            EVENT_CSV_COLUMNS,
        )

        analyzer = DeterministicEventAnalyzer()
        event = _make_event(impact=ImpactLevel.HIGH)
        result = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )

        for col in EVENT_CSV_COLUMNS:
            assert col in result, f"カラム欠落: {col}"


class TestDeterministic:
    """決定論性（同一入力→同一出力）のテスト"""

    def test_same_input_same_output(self) -> None:
        """同一入力で同一出力を保証"""
        analyzer = DeterministicEventAnalyzer()
        event = _make_event(
            event_name="Non-Farm Employment Change",
            impact=ImpactLevel.HIGH,
            actual=300.0,
            forecast=200.0,
        )

        result1 = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )
        result2 = analyzer._analyze_event(
            "USDJPY", "USD", "JPY", event
        )

        assert result1 == result2

    def test_no_randomness(self) -> None:
        """ランダム要素がないことを確認（100回実行）"""
        analyzer = DeterministicEventAnalyzer()
        event = _make_event(
            impact=ImpactLevel.HIGH,
            actual=250.0,
            forecast=200.0,
        )

        results = set()
        for _ in range(100):
            result = analyzer._analyze_event(
                "USDJPY", "USD", "JPY", event
            )
            # dict → frozenset で比較
            results.add(
                frozenset(
                    (k, str(v)) for k, v in result.items()
                )
            )

        assert len(results) == 1, "結果にランダム性がある"
