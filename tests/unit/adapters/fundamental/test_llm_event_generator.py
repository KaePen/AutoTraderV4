"""LLMEventGenerator テスト"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from autotrader.adapters.fundamental.llm_event_generator import (
    EVENT_CSV_COLUMNS,
    LLMEventGenerator,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)


def _make_event(
    currency: str = "USD",
    event_name: str = "NFP",
    impact: ImpactLevel = ImpactLevel.HIGH,
    event_time: datetime | None = None,
    actual: float | None = 200.0,
    forecast: float | None = 180.0,
    previous: float | None = 175.0,
) -> EconomicEvent:
    """テスト用イベント生成"""
    if event_time is None:
        event_time = datetime(
            2024, 1, 5, 14, 30, tzinfo=timezone.utc
        )
    return EconomicEvent(
        event_id=f"test_{event_name}",
        event_time=event_time,
        currency=currency,
        event_name=event_name,
        impact=impact,
        source=EventSource.MT5,
        fetched_at=datetime.now(timezone.utc),
        actual=actual,
        forecast=forecast,
        previous=previous,
    )


class TestFilterEvents:
    """_filter_events のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_filter_by_currency(self) -> None:
        """通貨でフィルタ"""
        events = [
            _make_event(currency="USD"),
            _make_event(currency="JPY"),
            _make_event(currency="EUR"),
        ]
        result = self.gen._filter_events(
            events, ("USD", "JPY"), 2024
        )
        assert len(result) == 2

    def test_filter_by_year(self) -> None:
        """年でフィルタ"""
        events = [
            _make_event(
                event_time=datetime(
                    2024, 1, 1, tzinfo=timezone.utc
                )
            ),
            _make_event(
                event_time=datetime(
                    2023, 12, 31, tzinfo=timezone.utc
                )
            ),
        ]
        result = self.gen._filter_events(
            events, ("USD", "JPY"), 2024
        )
        assert len(result) == 1


class TestGroupByDate:
    """_group_by_date のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_groups_correctly(self) -> None:
        """日付でグループ化"""
        events = [
            _make_event(
                event_time=datetime(
                    2024, 1, 5, 10, 0, tzinfo=timezone.utc
                )
            ),
            _make_event(
                event_time=datetime(
                    2024, 1, 5, 14, 0, tzinfo=timezone.utc
                )
            ),
            _make_event(
                event_time=datetime(
                    2024, 1, 6, 10, 0, tzinfo=timezone.utc
                )
            ),
        ]
        result = self.gen._group_by_date(events)
        assert len(result) == 2
        assert len(result[date(2024, 1, 5)]) == 2
        assert len(result[date(2024, 1, 6)]) == 1


class TestAnalyzeDate:
    """_analyze_date のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator(
            retry_delay_seconds=0.0
        )

    def test_no_events_returns_default(self) -> None:
        """0件日 -> デフォルト、LLM呼び出しなし"""
        with patch.object(
            self.gen, "_call_ollama_with_retry"
        ) as mock:
            result = self.gen._analyze_date(
                "USDJPY", "USD", "JPY", date(2024, 1, 1), []
            )
        mock.assert_not_called()
        assert result["net_surprise_score"] == 0.0
        assert result["trade_caution_level"] == 0

    def test_unreleased_high_impact_sets_caution(
        self,
    ) -> None:
        """未発表高インパクト -> 注意度設定"""
        events = [
            _make_event(actual=None, forecast=180.0),
        ]
        result = self.gen._analyze_date(
            "USDJPY", "USD", "JPY", date(2024, 1, 5), events
        )
        assert result["trade_caution_level"] == 1

    def test_two_unreleased_high_impact_max_caution(
        self,
    ) -> None:
        """未発表高インパクト2件 -> 回避推奨"""
        events = [
            _make_event(
                event_name="NFP", actual=None, forecast=180.0
            ),
            _make_event(
                event_name="CPI", actual=None, forecast=2.5
            ),
        ]
        result = self.gen._analyze_date(
            "USDJPY", "USD", "JPY", date(2024, 1, 5), events
        )
        assert result["trade_caution_level"] == 2

    def test_with_events_calls_llm(self) -> None:
        """イベントあり -> LLM呼び出し"""
        events = [_make_event()]
        llm_response = {
            "net_surprise_score": 0.5,
            "dominant_event_name": "NFP",
            "dominant_surprise_pct": 0.11,
            "expected_volatility": 1.5,
            "price_direction_bias": 0.6,
            "convergence_hours": 4.0,
            "trade_caution_level": 2,
            "summary": "NFP大幅上振れ",
        }
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
            return_value=llm_response,
        ):
            result = self.gen._analyze_date(
                "USDJPY",
                "USD",
                "JPY",
                date(2024, 1, 5),
                events,
            )
        assert result["net_surprise_score"] == 0.5
        assert result["dominant_event_name"] == "NFP"
        assert result["trade_caution_level"] == 2


class TestBuildEventResult:
    """_build_event_result のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_clips_scores(self) -> None:
        """スコアクリッピング"""
        data = {
            "net_surprise_score": 1.5,
            "price_direction_bias": -2.0,
            "expected_volatility": 3.0,
            "convergence_hours": 100.0,
            "trade_caution_level": 5,
        }
        result = self.gen._build_event_result(data)
        assert result["net_surprise_score"] == 1.0
        assert result["price_direction_bias"] == -1.0
        assert result["expected_volatility"] == 2.0
        assert result["convergence_hours"] == 72.0
        assert result["trade_caution_level"] == 2

    def test_convergence_hours_lower_bound(self) -> None:
        """convergence_hours < 0.5 -> 0.5にクリップ"""
        data = {"convergence_hours": 0.3}
        result = self.gen._build_event_result(data)
        assert result["convergence_hours"] == 0.5

    def test_missing_fields_default(self) -> None:
        """欠落フィールド -> デフォルト"""
        result = self.gen._build_event_result({})
        assert result["net_surprise_score"] == 0.0
        assert result["dominant_event_name"] == ""
        assert result["expected_volatility"] == 1.0
        assert result["convergence_hours"] == 0.0
        assert result["trade_caution_level"] == 0


class TestBuildEventPrompt:
    """_build_event_prompt のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_contains_required_sections(self) -> None:
        """プロンプト構造検証"""
        events = [_make_event()]
        prompt = self.gen._build_event_prompt(
            "USDJPY",
            "USD",
            "JPY",
            date(2024, 1, 5),
            events,
        )
        assert "USDJPY" in prompt
        assert "USD" in prompt
        assert "JPY" in prompt
        assert "2024年1月5日" in prompt
        assert "NFP" in prompt
        assert "net_surprise_score" in prompt


class TestFormatEvents:
    """_format_events_for_prompt のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_empty(self) -> None:
        """空リスト"""
        result = self.gen._format_events_for_prompt([])
        assert result == "（なし）"

    def test_includes_previous(self) -> None:
        """前回値も含める"""
        events = [_make_event(previous=175.0)]
        result = self.gen._format_events_for_prompt(events)
        assert "前回=175.00" in result


class TestGenerateForSymbolYear:
    """generate_for_symbol_year のテスト"""

    def test_creates_csv_365_rows(
        self, tmp_path: Path
    ) -> None:
        """CSV生成 + 365行検証（2023年）"""
        gen = LLMEventGenerator(retry_delay_seconds=0.0)
        events = [
            _make_event(
                event_time=datetime(
                    2023, 6, 15, 14, 0, tzinfo=timezone.utc
                )
            ),
        ]
        with patch.object(
            gen,
            "_call_ollama_with_retry",
            return_value={
                "net_surprise_score": 0.3,
                "dominant_event_name": "NFP",
                "dominant_surprise_pct": 0.1,
                "expected_volatility": 1.2,
                "price_direction_bias": 0.4,
                "convergence_hours": 3.0,
                "trade_caution_level": 1,
                "summary": "テスト",
            },
        ):
            path = gen.generate_for_symbol_year(
                "USDJPY", 2023, events, tmp_path
            )

        assert path.exists()
        import csv

        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 365
        # ヘッダー検証
        assert set(rows[0].keys()) == set(EVENT_CSV_COLUMNS)

    def test_skips_existing_without_overwrite(
        self, tmp_path: Path
    ) -> None:
        """上書きなし -> スキップ"""
        gen = LLMEventGenerator()
        existing = tmp_path / "llm_events_USDJPY_2024.csv"
        existing.write_text("header\n", encoding="utf-8")

        result = gen.generate_for_symbol_year(
            "USDJPY", 2024, [], tmp_path, overwrite=False
        )
        assert result == existing

    def test_unsupported_symbol_raises(
        self, tmp_path: Path
    ) -> None:
        """未対応シンボル -> ValueError"""
        gen = LLMEventGenerator()
        with pytest.raises(ValueError, match="未対応シンボル"):
            gen.generate_for_symbol_year(
                "XXXYYY", 2024, [], tmp_path
            )
