"""LLMEventGenerator テスト（イベント単位分析版）"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
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


class TestComputeSurpriseScore:
    """_compute_surprise_score のテスト"""

    def test_positive_surprise(self) -> None:
        """正のサプライズ（実績 > 予測）"""
        # actual=200, forecast=180 -> surprise=20/180≈0.111
        event = _make_event(actual=200.0, forecast=180.0)
        score = LLMEventGenerator._compute_surprise_score(
            event
        )
        assert 0.10 < score < 0.12

    def test_negative_surprise(self) -> None:
        """負のサプライズ（実績 < 予測）"""
        event = _make_event(actual=160.0, forecast=180.0)
        score = LLMEventGenerator._compute_surprise_score(
            event
        )
        assert score < 0

    def test_no_surprise_data(self) -> None:
        """実績/予測がない場合 -> 0.0"""
        event = _make_event(actual=None, forecast=None)
        score = LLMEventGenerator._compute_surprise_score(
            event
        )
        assert score == 0.0

    def test_clips_to_range(self) -> None:
        """極端なサプライズもクリップ"""
        # actual=1000, forecast=100 -> surprise=9.0 -> clip 1.0
        event = _make_event(actual=1000.0, forecast=100.0)
        score = LLMEventGenerator._compute_surprise_score(
            event
        )
        assert score == 1.0

    def test_negative_forecast_positive_actual(
        self,
    ) -> None:
        """予測が負・実績が正 -> 正のサプライズ"""
        event = _make_event(actual=0.5, forecast=-0.5)
        score = LLMEventGenerator._compute_surprise_score(
            event
        )
        # (0.5 - (-0.5)) / abs(-0.5) = 2.0 -> clip 1.0
        assert score == 1.0

    def test_negative_forecast_worse_actual(self) -> None:
        """予測が負・実績がさらに悪い -> 負のサプライズ"""
        event = _make_event(actual=-1.0, forecast=-0.5)
        score = LLMEventGenerator._compute_surprise_score(
            event
        )
        # (-1.0 - (-0.5)) / abs(-0.5) = -1.0
        assert score == -1.0


class TestComputeDirectionBias:
    """_compute_direction_bias のテスト"""

    def test_base_currency_positive_surprise(self) -> None:
        """基軸通貨の正のサプライズ -> 正のバイアス"""
        # USD指標（base）、実績>予測
        event = _make_event(
            currency="USD",
            impact=ImpactLevel.HIGH,
            actual=200.0,
            forecast=180.0,
        )
        bias = LLMEventGenerator._compute_direction_bias(
            event, "USD", "JPY"
        )
        # surprise≈0.111, dir=+1, pair=+, scale=0.8
        assert bias > 0

    def test_quote_currency_positive_surprise(self) -> None:
        """決済通貨の正のサプライズ -> 負のバイアス"""
        # JPY指標（quote）、実績>予測
        event = _make_event(
            currency="JPY",
            impact=ImpactLevel.HIGH,
            actual=200.0,
            forecast=180.0,
        )
        bias = LLMEventGenerator._compute_direction_bias(
            event, "USD", "JPY"
        )
        # JPY強 -> USDJPY下落
        assert bias < 0

    def test_inverse_indicator(self) -> None:
        """逆指標（失業率等）: 高い=通貨安"""
        event = _make_event(
            currency="USD",
            event_name="Unemployment Rate",
            impact=ImpactLevel.HIGH,
            actual=5.0,
            forecast=4.0,
        )
        bias = LLMEventGenerator._compute_direction_bias(
            event, "USD", "JPY"
        )
        # 失業率上昇 -> USD弱 -> USDJPY下落
        assert bias < 0

    def test_impact_scaling(self) -> None:
        """インパクトレベルでスケーリングが変わる"""
        event_high = _make_event(
            impact=ImpactLevel.HIGH,
            actual=200.0,
            forecast=180.0,
        )
        event_low = _make_event(
            impact=ImpactLevel.LOW,
            actual=200.0,
            forecast=180.0,
        )
        bias_high = LLMEventGenerator._compute_direction_bias(
            event_high, "USD", "JPY"
        )
        bias_low = LLMEventGenerator._compute_direction_bias(
            event_low, "USD", "JPY"
        )
        # HIGH(0.8) > LOW(0.3)
        assert abs(bias_high) > abs(bias_low)

    def test_no_surprise_zero_bias(self) -> None:
        """サプライズなし -> バイアス0"""
        event = _make_event(actual=None, forecast=None)
        bias = LLMEventGenerator._compute_direction_bias(
            event, "USD", "JPY"
        )
        assert bias == 0.0


class TestGetIndicatorDirection:
    """_get_indicator_direction のテスト"""

    def test_normal_indicator(self) -> None:
        """通常指標 -> +1"""
        assert (
            LLMEventGenerator._get_indicator_direction(
                "NFP"
            )
            == 1
        )

    def test_unemployment(self) -> None:
        """失業率 -> -1"""
        assert (
            LLMEventGenerator._get_indicator_direction(
                "Unemployment Rate"
            )
            == -1
        )

    def test_jobless_claims(self) -> None:
        """失業保険申請 -> -1"""
        assert (
            LLMEventGenerator._get_indicator_direction(
                "Initial Jobless Claims"
            )
            == -1
        )

    def test_claimant_count(self) -> None:
        """求職者手当申請 -> -1"""
        assert (
            LLMEventGenerator._get_indicator_direction(
                "Claimant Count Change"
            )
            == -1
        )


class TestAnalyzeEvent:
    """_analyze_event のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator(
            retry_delay_seconds=0.0
        )

    def test_high_impact_calls_llm(self) -> None:
        """HIGHインパクト -> LLM呼び出し+コード計算surprise"""
        event = _make_event(impact=ImpactLevel.HIGH)
        # LLMはconvergence等のみ返す
        llm_response = {
            "convergence_hours": 48.0,
            "expected_volatility": 1.8,
            "trade_caution_level": 1,
            "summary": "NFP大幅上振れ",
        }
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
            return_value=llm_response,
        ) as mock:
            result = self.gen._analyze_event(
                "USDJPY", "USD", "JPY", event
            )
        mock.assert_called_once()
        # surprise_scoreはコード計算値（≈0.111）
        assert 0.10 < result["surprise_score"] < 0.12
        # direction_biasもコード計算値
        assert result["direction_bias"] > 0
        assert result["event_name"] == "NFP"
        assert result["event_time"] is not None
        # LLM由来の値
        assert result["convergence_hours"] == 48.0
        assert result["expected_volatility"] == 1.8

    def test_high_impact_ignores_llm_surprise(
        self,
    ) -> None:
        """HIGHでLLMがsurprise_scoreを返してもコード計算値を使用"""
        event = _make_event(impact=ImpactLevel.HIGH)
        llm_response = {
            "surprise_score": 0.99,
            "direction_bias": -0.99,
            "convergence_hours": 12.0,
            "expected_volatility": 1.5,
            "trade_caution_level": 0,
            "summary": "テスト",
        }
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
            return_value=llm_response,
        ):
            result = self.gen._analyze_event(
                "USDJPY", "USD", "JPY", event
            )
        # LLMの0.99ではなくコード計算値（≈0.111）
        assert result["surprise_score"] < 0.2
        assert result["direction_bias"] > 0

    def test_medium_impact_calls_llm(self) -> None:
        """MEDIUMインパクト -> LLM呼び出し"""
        event = _make_event(impact=ImpactLevel.MEDIUM)
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
            return_value={
                "convergence_hours": 4.0,
                "expected_volatility": 1.0,
                "trade_caution_level": 0,
                "summary": "テスト",
            },
        ) as mock:
            self.gen._analyze_event(
                "USDJPY", "USD", "JPY", event
            )
        mock.assert_called_once()

    def test_low_impact_skips_llm(self) -> None:
        """LOWインパクト -> LLMスキップ"""
        event = _make_event(impact=ImpactLevel.LOW)
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
        ) as mock:
            result = self.gen._analyze_event(
                "USDJPY", "USD", "JPY", event
            )
        mock.assert_not_called()
        assert result["summary"] == "低インパクト指標"
        assert result["convergence_hours"] == 1.0

    def test_low_impact_with_surprise(self) -> None:
        """LOWインパクト + サプライズ -> コード計算"""
        # actual=200, forecast=180 -> surprise=0.111
        event = _make_event(
            impact=ImpactLevel.LOW,
            actual=200.0,
            forecast=180.0,
        )
        result = self.gen._analyze_event(
            "USDJPY", "USD", "JPY", event
        )
        assert result["surprise_score"] > 0
        assert abs(result["direction_bias"]) > 0

    def test_usd_holiday_skips_llm(self) -> None:
        """USD休日 -> LLMスキップ + 回避推奨"""
        event = _make_event(
            currency="USD",
            event_name="Bank Holiday",
            impact=ImpactLevel.HIGH,
            actual=None,
            forecast=None,
            previous=None,
        )
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
        ) as mock:
            result = self.gen._analyze_event(
                "USDJPY", "USD", "JPY", event
            )
        mock.assert_not_called()
        assert result["surprise_score"] == 0.0
        assert result["convergence_hours"] == 24.0
        assert result["expected_volatility"] == 0.2
        assert result["trade_caution_level"] == 2
        assert "米国" in result["summary"]

    def test_jpy_holiday_differs_from_usd(self) -> None:
        """JPY休日 -> USD休日より軽度の影響"""
        event = _make_event(
            currency="JPY",
            event_name="Bank Holiday",
            impact=ImpactLevel.HIGH,
            actual=None,
            forecast=None,
            previous=None,
        )
        result = self.gen._analyze_event(
            "USDJPY", "USD", "JPY", event
        )
        assert result["expected_volatility"] == 0.5
        assert result["trade_caution_level"] == 1
        assert result["convergence_hours"] == 12.0
        assert "日本" in result["summary"]

    def test_unknown_currency_holiday_fallback(
        self,
    ) -> None:
        """未知通貨の休日 -> フォールバック値"""
        result = LLMEventGenerator._holiday_result("SGD")
        assert result["expected_volatility"] == 0.4
        assert result["trade_caution_level"] == 1
        assert result["convergence_hours"] == 16.0
        assert result["is_holiday"] is True
        assert "流動性低下" in result["summary"]

    def test_output_contains_event_metadata(self) -> None:
        """出力にイベントメタデータが含まれる"""
        event = _make_event(impact=ImpactLevel.LOW)
        result = self.gen._analyze_event(
            "USDJPY", "USD", "JPY", event
        )
        assert result["currency"] == "USD"
        assert result["event_name"] == "NFP"
        assert result["actual"] == 200.0
        assert result["forecast"] == 180.0


class TestBuildEventResult:
    """_build_event_result のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_uses_code_computed_scores(self) -> None:
        """コード計算値がそのまま使われる"""
        data = {
            "convergence_hours": 12.0,
            "expected_volatility": 1.5,
            "trade_caution_level": 1,
            "summary": "テスト",
        }
        result = self.gen._build_event_result(
            data, 0.5, -0.3
        )
        assert result["surprise_score"] == 0.5
        assert result["direction_bias"] == -0.3
        assert result["convergence_hours"] == 12.0

    def test_clips_llm_fields(self) -> None:
        """LLM由来フィールドのクリッピング"""
        data = {
            "expected_volatility": 3.0,
            "convergence_hours": 100.0,
            "trade_caution_level": 5,
        }
        result = self.gen._build_event_result(
            data, 0.0, 0.0
        )
        assert result["expected_volatility"] == 2.0
        assert result["convergence_hours"] == 72.0
        assert result["trade_caution_level"] == 2

    def test_convergence_hours_lower_bound(self) -> None:
        """convergence_hours < 0.5 -> 0.5にクリップ"""
        data = {"convergence_hours": 0.3}
        result = self.gen._build_event_result(
            data, 0.0, 0.0
        )
        assert result["convergence_hours"] == 0.5

    def test_missing_fields_default(self) -> None:
        """欠落フィールド -> デフォルト"""
        result = self.gen._build_event_result(
            {}, 0.1, -0.2
        )
        assert result["surprise_score"] == 0.1
        assert result["direction_bias"] == -0.2
        assert result["expected_volatility"] == 1.0
        assert result["convergence_hours"] == 0.0
        assert result["trade_caution_level"] == 0
        assert result["is_holiday"] is False


class TestBuildEventPrompt:
    """_build_event_prompt のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMEventGenerator()

    def test_contains_required_sections(self) -> None:
        """プロンプト構造検証"""
        event = _make_event()
        prompt = self.gen._build_event_prompt(
            "USDJPY", "USD", "JPY", event,
            0.111, 0.089,
        )
        assert "USDJPY" in prompt
        assert "NFP" in prompt
        assert "convergence_hours" in prompt
        assert "200.00" in prompt
        assert "180.00" in prompt

    def test_no_surprise_score_in_json_template(
        self,
    ) -> None:
        """JSONテンプレートにsurprise_scoreがない"""
        event = _make_event()
        prompt = self.gen._build_event_prompt(
            "USDJPY", "USD", "JPY", event,
            0.111, 0.089,
        )
        # JSONテンプレート部分にはsurprise_scoreがない
        assert '"surprise_score"' not in prompt
        assert '"direction_bias"' not in prompt

    def test_contains_computed_values(self) -> None:
        """コード計算済み参考値が含まれる"""
        event = _make_event()
        prompt = self.gen._build_event_prompt(
            "USDJPY", "USD", "JPY", event,
            0.111, 0.089,
        )
        assert "+0.1110" in prompt
        assert "+0.0890" in prompt


class TestGenerateForSymbolYear:
    """generate_for_symbol_year のテスト"""

    def test_creates_csv_per_event(
        self, tmp_path: Path
    ) -> None:
        """1行=1イベントのCSV生成"""
        gen = LLMEventGenerator(retry_delay_seconds=0.0)
        events = [
            _make_event(
                event_name="NFP",
                impact=ImpactLevel.HIGH,
                event_time=datetime(
                    2023, 6, 15, 13, 30,
                    tzinfo=timezone.utc,
                ),
            ),
            _make_event(
                event_name="ISM",
                impact=ImpactLevel.MEDIUM,
                event_time=datetime(
                    2023, 6, 15, 15, 0,
                    tzinfo=timezone.utc,
                ),
            ),
            _make_event(
                event_name="Housing",
                impact=ImpactLevel.LOW,
                event_time=datetime(
                    2023, 6, 15, 16, 0,
                    tzinfo=timezone.utc,
                ),
            ),
        ]
        with patch.object(
            gen,
            "_call_ollama_with_retry",
            return_value={
                "convergence_hours": 12.0,
                "expected_volatility": 1.2,
                "trade_caution_level": 0,
                "summary": "テスト",
            },
        ):
            path = gen.generate_for_symbol_year(
                "USDJPY", 2023, events, tmp_path
            )

        assert path.exists()
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        # 3イベント = 3行
        assert len(rows) == 3
        assert set(rows[0].keys()) == set(
            EVENT_CSV_COLUMNS
        )
        # 時系列順
        assert rows[0]["event_name"] == "NFP"
        assert rows[2]["event_name"] == "Housing"

    def test_holiday_events_have_currency_defaults(
        self, tmp_path: Path
    ) -> None:
        """休日イベントは通貨別固定デフォルト値で出力"""
        gen = LLMEventGenerator(retry_delay_seconds=0.0)
        events = [
            _make_event(
                currency="USD",
                event_name="Bank Holiday",
                actual=None,
                forecast=None,
                previous=None,
            ),
            _make_event(
                currency="JPY",
                event_name="Bank Holiday",
                actual=None,
                forecast=None,
                previous=None,
                event_time=datetime(
                    2024, 1, 8, 0, 0,
                    tzinfo=timezone.utc,
                ),
            ),
        ]
        with patch.object(
            gen,
            "_call_ollama_with_retry",
        ) as mock:
            path = gen.generate_for_symbol_year(
                "USDJPY", 2024, events, tmp_path
            )
        mock.assert_not_called()
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        # USD休日: 回避推奨
        usd_row = rows[0]
        assert usd_row["currency"] == "USD"
        assert float(usd_row["expected_volatility"]) == 0.2
        assert int(usd_row["trade_caution_level"]) == 2
        assert usd_row["is_holiday"] == "True"
        # JPY休日: 注意レベル
        jpy_row = rows[1]
        assert jpy_row["currency"] == "JPY"
        assert float(jpy_row["expected_volatility"]) == 0.5
        assert int(jpy_row["trade_caution_level"]) == 1
        assert jpy_row["is_holiday"] == "True"

    def test_resume_skips_processed(
        self, tmp_path: Path
    ) -> None:
        """resume: 処理済みイベントをスキップ"""
        gen = LLMEventGenerator(retry_delay_seconds=0.0)

        # 既存CSV作成（1件処理済み）
        existing = tmp_path / "llm_events_USDJPY_2024.csv"
        with open(
            existing, "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=EVENT_CSV_COLUMNS
            )
            writer.writeheader()
            writer.writerow({
                "event_time": "2024-01-05T13:30:00+00:00",
                "currency": "USD",
                "event_name": "NFP",
                "impact": "high",
                "actual": "200.0",
                "forecast": "180.0",
                "previous": "175.0",
                "surprise_score": "0.5",
                "direction_bias": "0.4",
                "convergence_hours": "48.0",
                "expected_volatility": "1.5",
                "trade_caution_level": "1",
                "is_holiday": "False",
                "summary": "テスト",
            })

        events = [
            _make_event(
                event_name="NFP",
                event_time=datetime(
                    2024, 1, 5, 13, 30,
                    tzinfo=timezone.utc,
                ),
            ),
            _make_event(
                event_name="ISM",
                event_time=datetime(
                    2024, 1, 5, 15, 0,
                    tzinfo=timezone.utc,
                ),
            ),
        ]
        with patch.object(
            gen,
            "_call_ollama_with_retry",
            return_value={
                "convergence_hours": 4.0,
                "expected_volatility": 1.0,
                "trade_caution_level": 0,
                "summary": "ISMテスト",
            },
        ) as mock:
            gen.generate_for_symbol_year(
                "USDJPY", 2024, events, tmp_path
            )
        # 1件目はスキップ、2件目のみLLM呼び出し
        assert mock.call_count == 1

    def test_unsupported_symbol_raises(
        self, tmp_path: Path
    ) -> None:
        """未対応シンボル -> ValueError"""
        gen = LLMEventGenerator()
        with pytest.raises(
            ValueError, match="未対応シンボル"
        ):
            gen.generate_for_symbol_year(
                "XXXYYY", 2024, [], tmp_path
            )
