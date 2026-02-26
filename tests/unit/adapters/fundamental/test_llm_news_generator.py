"""LLMNewsGenerator テスト"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from autotrader.adapters.fundamental.llm_news_generator import (
    NEWS_CSV_COLUMNS,
    LLMNewsGenerator,
    _MAP_BATCH_SIZE,
)
from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
    NewsSource,
)


def _make_news(
    source_name: str = "fxstreet.com",
    title: str = "USD rises on strong NFP",
    currencies: list[str] | None = None,
    published_at: datetime | None = None,
    content: str | None = None,
) -> NewsItem:
    """テスト用ニュースアイテム生成"""
    if currencies is None:
        currencies = ["USD", "JPY"]
    if published_at is None:
        published_at = datetime(
            2024, 1, 15, 10, 0, tzinfo=timezone.utc
        )
    return NewsItem(
        news_id=f"test_{hash(title)}",
        published_at=published_at,
        title=title,
        source_name=source_name,
        source_url="https://example.com",
        currencies=currencies,
        source_type=NewsSource.RSS,
        snippet="Tone:1.0",
        content=content,
    )


def _make_news_list(count: int) -> list[NewsItem]:
    """指定件数のテスト用ニュースリスト生成"""
    return [
        _make_news(
            title=f"News article {i}",
            published_at=datetime(
                2024, 1, 15, 0, i % 60,
                tzinfo=timezone.utc,
            ),
        )
        for i in range(count)
    ]


class TestFilterNews:
    """_filter_news のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_filter_by_currency(self) -> None:
        """通貨でフィルタ"""
        items = [
            _make_news(currencies=["USD", "JPY"]),
            _make_news(currencies=["EUR", "GBP"]),
            _make_news(currencies=["JPY"]),
        ]
        result = self.gen._filter_news(
            items, ("USD", "JPY"), 2024
        )
        assert len(result) == 2

    def test_filter_by_year(self) -> None:
        """年でフィルタ"""
        items = [
            _make_news(
                published_at=datetime(
                    2024, 3, 1, tzinfo=timezone.utc
                )
            ),
            _make_news(
                published_at=datetime(
                    2023, 12, 31, tzinfo=timezone.utc
                )
            ),
        ]
        result = self.gen._filter_news(
            items, ("USD", "JPY"), 2024
        )
        assert len(result) == 1


class TestGroupByDate:
    """_group_by_date のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_groups_correctly(self) -> None:
        """日付でグループ化"""
        items = [
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 5, 0, tzinfo=timezone.utc
                )
            ),
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 16, 0, tzinfo=timezone.utc
                )
            ),
            _make_news(
                published_at=datetime(
                    2024, 1, 16, 10, 0, tzinfo=timezone.utc
                )
            ),
        ]
        result = self.gen._group_by_date(items)
        assert len(result) == 2
        assert len(result[date(2024, 1, 15)]) == 2


class TestSplitIntoBatches:
    """_split_into_batches のテスト"""

    def test_exact_division(self) -> None:
        """割り切れる場合"""
        items = _make_news_list(12)
        batches = LLMNewsGenerator._split_into_batches(
            items, 4
        )
        assert len(batches) == 3
        assert all(len(b) == 4 for b in batches)

    def test_remainder(self) -> None:
        """余りがある場合"""
        items = _make_news_list(7)
        batches = LLMNewsGenerator._split_into_batches(
            items, 3
        )
        assert len(batches) == 3
        assert len(batches[0]) == 3
        assert len(batches[1]) == 3
        assert len(batches[2]) == 1

    def test_sorted_by_time(self) -> None:
        """時系列順にソート"""
        items = [
            _make_news(
                title="late",
                published_at=datetime(
                    2024, 1, 15, 23, 0, tzinfo=timezone.utc
                ),
            ),
            _make_news(
                title="early",
                published_at=datetime(
                    2024, 1, 15, 1, 0, tzinfo=timezone.utc
                ),
            ),
        ]
        batches = LLMNewsGenerator._split_into_batches(
            items, 10
        )
        assert batches[0][0].title == "early"
        assert batches[0][1].title == "late"

    def test_single_item(self) -> None:
        """1件のみ"""
        items = _make_news_list(1)
        batches = LLMNewsGenerator._split_into_batches(
            items, 12
        )
        assert len(batches) == 1
        assert len(batches[0]) == 1


class TestFormatArticlesForBatch:
    """_format_articles_for_batch のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_fx_source_with_content(self) -> None:
        """FXソースは本文抜粋付き"""
        items = [
            _make_news(
                source_name="fxstreet.com",
                title="FX news",
                content="A" * 100,
            ),
        ]
        result = self.gen._format_articles_for_batch(items)
        assert "fxstreet.com" in result
        assert "FX news" in result
        assert "..." in result  # content 抜粋

    def test_general_source_snippet(self) -> None:
        """一般ソースはsnippetフォールバック"""
        items = [
            _make_news(
                source_name="random.com",
                title="General news",
            ),
        ]
        result = self.gen._format_articles_for_batch(items)
        assert "[snippet]" in result

    def test_empty_returns_nashi(self) -> None:
        """空リスト"""
        result = self.gen._format_articles_for_batch([])
        assert result == "（なし）"


class TestEstimateTokens:
    """_estimate_tokens のテスト"""

    def test_empty(self) -> None:
        """空文字列"""
        assert LLMNewsGenerator._estimate_tokens("") == 0

    def test_english(self) -> None:
        """英語テキスト"""
        result = LLMNewsGenerator._estimate_tokens(
            "a" * 300
        )
        assert result == 100

    def test_short_text(self) -> None:
        """短いテキスト（最低1）"""
        result = LLMNewsGenerator._estimate_tokens("ab")
        assert result >= 1


class TestAnalyzeDate:
    """_analyze_date のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator(
            retry_delay_seconds=0.0
        )

    def test_no_news_returns_default(self) -> None:
        """0件日 -> デフォルト"""
        with patch.object(
            self.gen, "_call_ollama_with_retry"
        ) as mock:
            result = self.gen._analyze_date(
                "USDJPY",
                "USD",
                "JPY",
                date(2024, 1, 1),
                [],
            )
        mock.assert_not_called()
        assert result["sentiment_score"] == 0.0
        assert result["session_detail"] == "{}"

    def test_few_news_single_call(self) -> None:
        """少記事 -> 単一呼び出し"""
        items = _make_news_list(5)
        llm_response = {
            "sentiment_score": 0.4,
            "sentiment_confidence": 0.7,
            "macro_bias_score": 0.3,
            "policy_divergence_score": 0.5,
            "risk_appetite_score": 0.1,
            "geopolitical_risk_level": 1,
            "dominant_theme": "FRB利下げ観測",
            "summary": "ドル買い優勢",
        }
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
            return_value=llm_response,
        ) as mock:
            result = self.gen._analyze_date(
                "USDJPY",
                "USD",
                "JPY",
                date(2024, 1, 15),
                items,
            )
        # 単一呼び出し: 1回のみ
        assert mock.call_count == 1
        assert result["sentiment_score"] == 0.4
        assert result["session_detail"] == "{}"

    def test_many_news_map_reduce(self) -> None:
        """多記事 -> Map-Reduce"""
        items = _make_news_list(_MAP_BATCH_SIZE + 5)
        map_response = {
            "sentiment_score": 0.3,
            "macro_bias_score": 0.2,
            "policy_divergence_score": 0.1,
            "risk_appetite_score": 0.0,
            "geopolitical_risk_level": 0,
            "key_themes": "テストテーマ",
            "summary": "テスト要約",
        }
        reduce_response = {
            "sentiment_score": 0.5,
            "sentiment_confidence": 0.8,
            "macro_bias_score": 0.3,
            "policy_divergence_score": 0.2,
            "risk_appetite_score": 0.1,
            "geopolitical_risk_level": 1,
            "dominant_theme": "統合テーマ",
            "summary": "統合要約",
        }
        # 2バッチ + 1 reduce = 3回
        responses = [map_response, map_response, reduce_response]
        with patch.object(
            self.gen,
            "_call_ollama_with_retry",
            side_effect=responses,
        ) as mock:
            result = self.gen._analyze_date(
                "USDJPY",
                "USD",
                "JPY",
                date(2024, 1, 15),
                items,
            )
        assert mock.call_count == 3
        assert result["sentiment_score"] == 0.5
        assert result["dominant_theme"] == "統合テーマ"


class TestBuildFinalResult:
    """_build_final_result のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_clips_scores(self) -> None:
        """スコアクリッピング"""
        data = {
            "sentiment_score": 1.5,
            "sentiment_confidence": 1.5,
            "macro_bias_score": -2.0,
            "geopolitical_risk_level": 5,
        }
        result = self.gen._build_final_result(data)
        assert result["sentiment_score"] == 1.0
        assert result["sentiment_confidence"] == 1.0
        assert result["macro_bias_score"] == -1.0
        assert result["geopolitical_risk_level"] == 3

    def test_session_detail_empty(self) -> None:
        """session_detail は空JSON"""
        data = {"sentiment_score": 0.3}
        result = self.gen._build_final_result(data)
        assert result["session_detail"] == "{}"

    def test_theme_truncation(self) -> None:
        """テーマ100文字制限"""
        data = {"dominant_theme": "あ" * 200}
        result = self.gen._build_final_result(data)
        assert len(result["dominant_theme"]) == 100

    def test_summary_truncation(self) -> None:
        """要約200文字制限"""
        data = {"summary": "い" * 300}
        result = self.gen._build_final_result(data)
        assert len(result["summary"]) == 200


class TestBuildPrompts:
    """プロンプトビルダーのテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_single_prompt_contains_symbol(self) -> None:
        """単一プロンプトにシンボル情報"""
        prompt = self.gen._build_single_prompt(
            "USDJPY", "USD", "JPY",
            date(2024, 1, 15),
            "- test article", 1,
        )
        assert "USDJPY" in prompt
        assert "sentiment_score" in prompt
        assert "test article" in prompt

    def test_map_prompt_contains_batch_info(self) -> None:
        """Mapプロンプトにバッチ情報"""
        prompt = self.gen._build_map_prompt(
            "USDJPY", "USD", "JPY",
            date(2024, 1, 15),
            "- test article", 2, 5,
        )
        assert "2/5" in prompt
        assert "key_themes" in prompt

    def test_reduce_prompt_contains_summaries(self) -> None:
        """Reduceプロンプトにバッチ要約"""
        summaries = [
            {
                "sentiment_score": 0.3,
                "macro_bias_score": 0.2,
                "policy_divergence_score": 0.1,
                "risk_appetite_score": 0.0,
                "geopolitical_risk_level": 0,
                "key_themes": "テーマA",
                "summary": "要約A",
            },
            {
                "sentiment_score": -0.2,
                "macro_bias_score": -0.1,
                "policy_divergence_score": 0.3,
                "risk_appetite_score": -0.5,
                "geopolitical_risk_level": 1,
                "key_themes": "テーマB",
                "summary": "要約B",
            },
        ]
        prompt = self.gen._build_reduce_prompt(
            "USDJPY", "USD", "JPY",
            date(2024, 1, 15),
            summaries, 24,
        )
        assert "グループ1" in prompt
        assert "グループ2" in prompt
        assert "テーマA" in prompt
        assert "テーマB" in prompt
        assert "24件" in prompt
        assert "sentiment_confidence" in prompt


class TestGenerateForSymbolYear:
    """generate_for_symbol_year のテスト"""

    def test_creates_csv_365_rows(
        self, tmp_path: Path
    ) -> None:
        """CSV生成 + 365行検証（2023年）"""
        gen = LLMNewsGenerator(retry_delay_seconds=0.0)
        items = [
            _make_news(
                published_at=datetime(
                    2023, 6, 15, 10, 0, tzinfo=timezone.utc
                )
            ),
        ]
        llm_response = {
            "sentiment_score": 0.3,
            "sentiment_confidence": 0.6,
            "macro_bias_score": 0.2,
            "policy_divergence_score": 0.1,
            "risk_appetite_score": 0.0,
            "geopolitical_risk_level": 0,
            "dominant_theme": "テスト",
            "summary": "テスト要約",
        }
        with patch.object(
            gen,
            "_call_ollama_with_retry",
            return_value=llm_response,
        ):
            path = gen.generate_for_symbol_year(
                "USDJPY", 2023, items, tmp_path
            )

        assert path.exists()
        import csv

        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 365
        assert set(rows[0].keys()) == set(NEWS_CSV_COLUMNS)

    def test_skips_existing_without_overwrite(
        self, tmp_path: Path
    ) -> None:
        """上書きなし -> スキップ"""
        gen = LLMNewsGenerator()
        existing = tmp_path / "llm_news_USDJPY_2024.csv"
        existing.write_text("header\n", encoding="utf-8")

        result = gen.generate_for_symbol_year(
            "USDJPY", 2024, [], tmp_path, overwrite=False
        )
        assert result == existing

    def test_unsupported_symbol_raises(
        self, tmp_path: Path
    ) -> None:
        """未対応シンボル -> ValueError"""
        gen = LLMNewsGenerator()
        with pytest.raises(ValueError, match="未対応シンボル"):
            gen.generate_for_symbol_year(
                "XXXYYY", 2024, [], tmp_path
            )
