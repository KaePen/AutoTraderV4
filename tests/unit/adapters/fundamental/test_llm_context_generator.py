"""LLMContextGenerator テスト"""

from __future__ import annotations

import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autotrader.adapters.fundamental.llm_context_generator import (
    LLMContextGenerator,
    _SYMBOL_CURRENCIES,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings


def make_event(
    currency: str,
    event_name: str,
    year: int,
    month: int,
    day: int,
    impact: ImpactLevel = ImpactLevel.HIGH,
    actual: float | None = None,
    forecast: float | None = None,
) -> EconomicEvent:
    """テスト用EconomicEventを生成"""
    return EconomicEvent(
        event_id=f"test_{event_name}_{day}",
        event_time=datetime(
            year, month, day, 14, 30, tzinfo=timezone.utc
        ),
        currency=currency,
        event_name=event_name,
        impact=impact,
        source=EventSource.MT5,
        fetched_at=datetime.now(timezone.utc),
        actual=actual,
        forecast=forecast,
        previous=None,
    )


class TestLLMContextGenerator:
    """LLMContextGeneratorのテスト"""

    @pytest.fixture
    def generator(self):
        """テスト用ジェネレーター（LLM呼び出しはモック）"""
        settings = OllamaSettings(
            model="test-model",
            temperature=0.1,
        )
        return LLMContextGenerator(
            ollama_settings=settings,
            retry_delay_seconds=0.0,
            max_retries=1,
        )

    @pytest.fixture
    def sample_events(self):
        """USDJPYのサンプルイベント（2024年1月）"""
        return [
            make_event(
                "USD", "Non-Farm Payroll", 2024, 1, 5,
                ImpactLevel.HIGH, actual=216000, forecast=175000
            ),
            make_event(
                "USD", "CPI", 2024, 1, 12,
                ImpactLevel.HIGH, actual=3.4, forecast=3.2
            ),
            make_event(
                "JPY", "Bank of Japan Decision", 2024, 1, 23,
                ImpactLevel.HIGH, actual=None, forecast=None
            ),
            make_event(
                "USD", "GDP", 2024, 2, 1,  # 2月イベント
                ImpactLevel.HIGH, actual=3.3, forecast=2.0
            ),
        ]

    def test_group_by_month_filters_by_year(
        self, generator, sample_events
    ):
        """月別グループ化が年でフィルタリングされること"""
        result = generator._group_by_month(sample_events, 2024)
        # 1月に3件（2月の1件は除外）
        assert 1 in result
        assert len(result[1]) == 3
        # 2月は対象年外のデータが1件あるが...2月のデータは入っている
        assert 2 in result
        assert len(result[2]) == 1

    def test_group_by_month_excludes_other_years(
        self, generator, sample_events
    ):
        """他年のイベントは除外されること"""
        result = generator._group_by_month(sample_events, 2023)
        assert len(result) == 0

    def test_format_events_empty(self, generator):
        """イベントなしの場合は（なし）と返すこと"""
        result = generator._format_events([])
        assert result == "（なし）"

    def test_format_events_with_events(
        self, generator, sample_events
    ):
        """イベントがある場合は各イベントの情報を含むこと"""
        events = [sample_events[0]]  # NFP
        result = generator._format_events(events)
        assert "Non-Farm Payroll" in result
        assert "USD" in result
        assert "実績" in result

    def test_format_events_shows_surprise(
        self, generator, sample_events
    ):
        """実績>予測の場合にサプライズ率を表示すること"""
        event = make_event(
            "USD", "NFP", 2024, 1, 5,
            ImpactLevel.HIGH, actual=200000, forecast=100000
        )
        result = generator._format_events([event])
        # サプライズ率 +100.0% が含まれるはず
        assert "+" in result or "100" in result

    def test_parse_response_valid_json(self, generator):
        """正常なJSONレスポンスをパースできること"""
        content = json.dumps({
            "macro_bias_score": 0.5,
            "macro_bias_summary": "USD強気",
            "post_event_bias_score": 0.3,
            "post_event_summary": "NFP好調",
            "sentiment_score": 0.2,
        })
        result = generator._parse_response(content)
        assert result["macro_bias_score"] == 0.5
        assert result["macro_bias_summary"] == "USD強気"
        assert result["post_event_bias_score"] == 0.3
        assert result["sentiment_score"] == 0.2

    def test_parse_response_clips_to_range(self, generator):
        """スコアが[-1.0, 1.0]にクリップされること"""
        content = json.dumps({
            "macro_bias_score": 2.5,
            "macro_bias_summary": "強すぎ",
            "post_event_bias_score": -3.0,
            "post_event_summary": "弱すぎ",
            "sentiment_score": 1.5,
        })
        result = generator._parse_response(content)
        assert result["macro_bias_score"] == 1.0
        assert result["post_event_bias_score"] == -1.0
        assert result["sentiment_score"] == 1.0

    def test_parse_response_json_in_code_block(self, generator):
        """コードブロック内のJSONをパースできること"""
        content = """```json
{
  "macro_bias_score": -0.3,
  "macro_bias_summary": "JPY強気",
  "post_event_bias_score": -0.1,
  "post_event_summary": "BOJ据え置き",
  "sentiment_score": -0.2
}
```"""
        result = generator._parse_response(content)
        assert result["macro_bias_score"] == -0.3
        assert result["macro_bias_summary"] == "JPY強気"

    def test_parse_response_invalid_json_raises(self, generator):
        """不正なJSONは ValueError を発生させること"""
        with pytest.raises(ValueError, match="JSON解析失敗"):
            generator._parse_response("not valid json")

    def test_parse_response_missing_fields_uses_defaults(
        self, generator
    ):
        """フィールドが欠如している場合はデフォルト値を使用すること"""
        content = json.dumps({
            "macro_bias_score": 0.4,
        })
        result = generator._parse_response(content)
        assert result["macro_bias_score"] == 0.4
        assert result["post_event_bias_score"] == 0.0
        assert result["sentiment_score"] == 0.0

    @patch(
        "autotrader.adapters.fundamental"
        ".llm_context_generator.LLMContextGenerator._call_ollama"
    )
    def test_analyze_month_empty_events_no_llm_call(
        self, mock_call, generator
    ):
        """イベントなしの場合はLLMを呼ばないこと"""
        result = generator._analyze_month(
            symbol="USDJPY",
            base_currency="USD",
            quote_currency="JPY",
            year=2024,
            month=1,
            events=[],
        )
        mock_call.assert_not_called()
        assert result["macro_bias_score"] == 0.0
        assert "データなし" in result["macro_bias_summary"]

    @patch(
        "autotrader.adapters.fundamental"
        ".llm_context_generator.LLMContextGenerator._call_ollama"
    )
    def test_analyze_month_calls_llm_with_events(
        self, mock_call, generator, sample_events
    ):
        """イベントがある場合にLLMを呼ぶこと"""
        mock_call.return_value = {
            "macro_bias_score": 0.6,
            "macro_bias_summary": "USD強気テスト",
            "post_event_bias_score": 0.3,
            "post_event_summary": "短期強気",
            "sentiment_score": 0.4,
        }
        # 1月のUSBイベントのみ
        jan_events = [
            ev for ev in sample_events
            if ev.event_time.month == 1
        ]
        result = generator._analyze_month(
            symbol="USDJPY",
            base_currency="USD",
            quote_currency="JPY",
            year=2024,
            month=1,
            events=jan_events,
        )
        mock_call.assert_called_once()
        assert result["macro_bias_score"] == 0.6

    @patch(
        "autotrader.adapters.fundamental"
        ".llm_context_generator.LLMContextGenerator._call_ollama"
    )
    def test_generate_for_symbol_year_creates_csv(
        self, mock_call, generator, sample_events
    ):
        """generate_for_symbol_year がCSVを生成すること"""
        mock_call.return_value = {
            "macro_bias_score": 0.3,
            "macro_bias_summary": "テスト",
            "post_event_bias_score": 0.1,
            "post_event_summary": "テスト",
            "sentiment_score": 0.2,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = generator.generate_for_symbol_year(
                symbol="USDJPY",
                year=2024,
                events=sample_events,
                output_dir=tmpdir,
                overwrite=True,
            )
            assert output_path.exists()

            # CSVの内容確認
            with open(output_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)

            # 12ヶ月分の行があること
            assert len(rows) == 12

            # 1月のスコアが正しいこと
            jan_row = rows[0]
            assert jan_row["period_start"].startswith(
                "2024-01-01"
            )
            assert float(jan_row["macro_bias_score"]) == 0.3

    @patch(
        "autotrader.adapters.fundamental"
        ".llm_context_generator.LLMContextGenerator._call_ollama"
    )
    def test_generate_skips_existing_without_overwrite(
        self, mock_call, generator, sample_events
    ):
        """上書きなしの場合は既存ファイルをスキップすること"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 既存ファイルを作成
            output_path = Path(tmpdir) / "llm_context_USDJPY_2024.csv"
            output_path.write_text("dummy content")

            result = generator.generate_for_symbol_year(
                symbol="USDJPY",
                year=2024,
                events=sample_events,
                output_dir=tmpdir,
                overwrite=False,
            )
            # LLMを呼ばないこと
            mock_call.assert_not_called()
            assert result == output_path

    def test_unsupported_symbol_raises(
        self, generator, sample_events
    ):
        """未対応シンボルは ValueError を発生させること"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError, match="未対応シンボル"):
                generator.generate_for_symbol_year(
                    symbol="INVALID",
                    year=2024,
                    events=sample_events,
                    output_dir=tmpdir,
                )


class TestLLMContextInBacktestProvider:
    """BacktestFundamentalProviderのLLMコンテキスト機能テスト"""

    @pytest.fixture
    def provider(self):
        from autotrader.adapters.fundamental.backtest_provider import (
            BacktestFundamentalProvider,
        )
        return BacktestFundamentalProvider(event_guard_minutes=30)

    def make_llm_csv(
        self, rows: list[dict], path: Path | None = None
    ) -> Path:
        """テスト用LLMコンテキストCSVを生成"""
        if path is None:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv",
                delete=False, encoding="utf-8"
            ) as f:
                path = Path(f.name)

        with open(path, "w", encoding="utf-8", newline="") as f:
            fieldnames = [
                "period_start",
                "macro_bias_score",
                "macro_bias_summary",
                "post_event_bias_score",
                "post_event_summary",
                "sentiment_score",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_load_llm_context_csv_returns_count(self, provider):
        """LLMコンテキストCSVの読み込み件数が正しいこと"""
        csv_path = self.make_llm_csv([
            {
                "period_start": "2024-01-01T00:00:00+00:00",
                "macro_bias_score": "0.3",
                "macro_bias_summary": "USD強気",
                "post_event_bias_score": "0.1",
                "post_event_summary": "短期強気",
                "sentiment_score": "0.2",
            },
            {
                "period_start": "2024-02-01T00:00:00+00:00",
                "macro_bias_score": "-0.2",
                "macro_bias_summary": "JPY強気",
                "post_event_bias_score": "-0.1",
                "post_event_summary": "短期弱気",
                "sentiment_score": "-0.1",
            },
        ])

        count = provider.load_llm_context_csv(csv_path, "USDJPY")
        assert count == 2

    def test_get_context_uses_llm_scores(self, provider):
        """get_context がLLMスコアを使用すること"""
        csv_path = self.make_llm_csv([
            {
                "period_start": "2024-01-01T00:00:00+00:00",
                "macro_bias_score": "0.75",
                "macro_bias_summary": "USD強気テスト",
                "post_event_bias_score": "0.5",
                "post_event_summary": "短期テスト",
                "sentiment_score": "0.3",
            },
        ])
        provider.load_llm_context_csv(csv_path, "USDJPY")

        # 1月15日のコンテキストを取得
        ctx = provider.get_context(
            current_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            symbol="USDJPY",
        )
        assert ctx.macro_bias_score == 0.75
        assert ctx.macro_bias_summary == "USD強気テスト"
        assert ctx.post_event_bias_score == 0.5
        assert ctx.sentiment_score == 0.3

    def test_get_context_before_first_period_returns_default(
        self, provider
    ):
        """最初の期間より前はデフォルト値を返すこと"""
        csv_path = self.make_llm_csv([
            {
                "period_start": "2024-01-01T00:00:00+00:00",
                "macro_bias_score": "0.5",
                "macro_bias_summary": "テスト",
                "post_event_bias_score": "0.3",
                "post_event_summary": "テスト",
                "sentiment_score": "0.2",
            },
        ])
        provider.load_llm_context_csv(csv_path, "USDJPY")

        # 2023年12月（最初の期間より前）
        ctx = provider.get_context(
            current_time=datetime(2023, 12, 1, tzinfo=timezone.utc),
            symbol="USDJPY",
        )
        assert ctx.macro_bias_score == 0.0

    def test_get_context_without_llm_csv_returns_neutral(
        self, provider
    ):
        """LLMコンテキストなしでもニュートラルを返すこと"""
        ctx = provider.get_context(
            current_time=datetime(2024, 1, 15, tzinfo=timezone.utc),
            symbol="USDJPY",
        )
        assert ctx.macro_bias_score == 0.0
        assert ctx.sentiment_score == 0.0

    def test_get_context_uses_correct_symbol(self, provider):
        """シンボルが異なる場合は別のLLMコンテキストを使用すること"""
        # USDJPY用
        csv_usd = self.make_llm_csv([
            {
                "period_start": "2024-01-01T00:00:00+00:00",
                "macro_bias_score": "0.8",
                "macro_bias_summary": "USD強気",
                "post_event_bias_score": "0.4",
                "post_event_summary": "",
                "sentiment_score": "0.3",
            },
        ])
        # EURUSD用
        csv_eur = self.make_llm_csv([
            {
                "period_start": "2024-01-01T00:00:00+00:00",
                "macro_bias_score": "-0.5",
                "macro_bias_summary": "EUR弱気",
                "post_event_bias_score": "-0.2",
                "post_event_summary": "",
                "sentiment_score": "-0.3",
            },
        ])
        provider.load_llm_context_csv(csv_usd, "USDJPY")
        provider.load_llm_context_csv(csv_eur, "EURUSD")

        ts = datetime(2024, 1, 15, tzinfo=timezone.utc)
        ctx_usd = provider.get_context(ts, "USDJPY")
        ctx_eur = provider.get_context(ts, "EURUSD")

        assert ctx_usd.macro_bias_score == 0.8
        assert ctx_eur.macro_bias_score == -0.5

    def test_load_nonexistent_csv_returns_zero(self, provider):
        """存在しないCSVは0を返すこと"""
        count = provider.load_llm_context_csv(
            "/nonexistent/path.csv", "USDJPY"
        )
        assert count == 0

    def test_get_context_with_events_and_llm_context(
        self, provider
    ):
        """経済イベントとLLMコンテキストを両方読み込んだ場合、
        LLMスコアとイベント情報が正しく結合されること"""
        # LLMコンテキストCSVを作成
        llm_csv = self.make_llm_csv([
            {
                "period_start": "2024-01-01T00:00:00+00:00",
                "macro_bias_score": "0.6",
                "macro_bias_summary": "USD強気（経済強い）",
                "post_event_bias_score": "0.3",
                "post_event_summary": "NFP好調",
                "sentiment_score": "0.4",
            },
        ])
        provider.load_llm_context_csv(llm_csv, "USDJPY")

        # 経済イベントCSVを作成（30分以内に高インパクト指標あり）
        import tempfile
        event_time = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv",
            delete=False, encoding="utf-8"
        ) as f:
            event_csv = Path(f.name)
            import csv as _csv
            writer = _csv.DictWriter(f, fieldnames=[
                "event_id", "event_time", "currency",
                "event_name", "impact", "actual",
                "forecast", "previous",
            ])
            writer.writeheader()
            writer.writerow({
                "event_id": "test_001",
                "event_time": event_time.isoformat(),
                "currency": "USD",
                "event_name": "NFP",
                "impact": "high",
                "actual": "",
                "forecast": "175000",
                "previous": "180000",
            })
        provider.load_csv(event_csv)

        # 指標の15分前のコンテキスト取得
        ctx = provider.get_context(
            current_time=datetime(
                2024, 1, 15, 13, 45, tzinfo=timezone.utc
            ),
            symbol="USDJPY",
        )

        # LLMスコアが使われていること
        assert ctx.macro_bias_score == 0.6
        assert ctx.macro_bias_summary == "USD強気（経済強い）"
        assert ctx.sentiment_score == 0.4

        # 30分以内の高インパクト指標が検出されること
        assert ctx.has_high_impact_within_30min is True

        # upcoming_eventsにNFPが含まれること
        assert len(ctx.upcoming_events) > 0
        event_names = [ev["name"] for ev in ctx.upcoming_events]
        assert "NFP" in event_names
