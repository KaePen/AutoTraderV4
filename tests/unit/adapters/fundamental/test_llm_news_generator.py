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


class TestSplitBySession:
    """_split_by_session のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_session_split(self) -> None:
        """セッション分割"""
        items = [
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 3, 0, tzinfo=timezone.utc
                ),
                title="tokyo",
            ),
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 10, 0, tzinfo=timezone.utc
                ),
                title="london",
            ),
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 16, 0, tzinfo=timezone.utc
                ),
                title="ny",
            ),
        ]
        result = self.gen._split_by_session(items)
        assert len(result["tokyo"]) == 1
        assert len(result["london"]) == 1
        assert len(result["ny"]) == 1

    def test_boundary_hours(self) -> None:
        """境界時間のテスト"""
        items = [
            # 00:00 -> tokyo
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 0, 0, tzinfo=timezone.utc
                ),
                title="midnight",
            ),
            # 07:59 -> tokyo
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 7, 59, tzinfo=timezone.utc
                ),
                title="early",
            ),
            # 08:00 -> london
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 8, 0, tzinfo=timezone.utc
                ),
                title="london_start",
            ),
            # 13:59 -> london
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 13, 59, tzinfo=timezone.utc
                ),
                title="london_end",
            ),
            # 14:00 -> ny
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 14, 0, tzinfo=timezone.utc
                ),
                title="ny_start",
            ),
            # 23:59 -> ny
            _make_news(
                published_at=datetime(
                    2024, 1, 15, 23, 59, tzinfo=timezone.utc
                ),
                title="ny_end",
            ),
        ]
        result = self.gen._split_by_session(items)
        assert len(result["tokyo"]) == 2
        assert len(result["london"]) == 2
        assert len(result["ny"]) == 2


class TestCompressForPrompt:
    """_compress_for_prompt のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator(max_prompt_tokens=2500)

    def test_fx_source_priority(self) -> None:
        """FX専門ソース優先"""
        items = {
            "tokyo": [
                _make_news(
                    source_name="fxstreet.com",
                    title="FX news",
                    content="Full article about USD",
                ),
                _make_news(
                    source_name="random.com",
                    title="General news",
                ),
            ],
            "london": [],
            "ny": [],
        }
        result = self.gen._compress_for_prompt(items)
        assert "本文抜粋" in result["tokyo"]
        assert "[見出しのみ]" in result["tokyo"]

    def test_empty_session(self) -> None:
        """空セッション"""
        items = {
            "tokyo": [],
            "london": [],
            "ny": [],
        }
        result = self.gen._compress_for_prompt(items)
        assert result["tokyo"] == "（なし）"


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
        assert result["sentiment_confidence"] == 0.0
        # session_detail はJSON文字列
        detail = json.loads(result["session_detail"])
        assert detail["tokyo"]["count"] == 0

    def test_with_news_calls_llm(self) -> None:
        """ニュースあり -> LLM呼び出し"""
        items = [_make_news()]
        llm_response = {
            "sentiment_score": 0.4,
            "sentiment_confidence": 0.7,
            "macro_bias_score": 0.3,
            "policy_divergence_score": 0.5,
            "risk_appetite_score": 0.1,
            "geopolitical_risk_level": 1,
            "dominant_theme": "FRB利下げ観測",
            "summary": "ドル買い優勢",
            "session_sentiment": {
                "tokyo": 0.2,
                "london": 0.4,
                "ny": 0.5,
            },
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
                date(2024, 1, 15),
                items,
            )
        assert result["sentiment_score"] == 0.4
        assert result["sentiment_confidence"] == 0.7
        detail = json.loads(result["session_detail"])
        assert detail["london"]["sentiment"] == 0.4


class TestBuildNewsResult:
    """_build_news_result のテスト"""

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
        session_groups = {
            "tokyo": [],
            "london": [],
            "ny": [],
        }
        result = self.gen._build_news_result(
            data, session_groups
        )
        assert result["sentiment_score"] == 1.0
        assert result["sentiment_confidence"] == 1.0
        assert result["macro_bias_score"] == -1.0
        assert result["geopolitical_risk_level"] == 3

    def test_session_detail_json(self) -> None:
        """session_detail JSON構造"""
        data = {
            "session_sentiment": {
                "tokyo": 0.2,
                "london": -0.1,
                "ny": 0.5,
            },
        }
        session_groups = {
            "tokyo": [_make_news(title="a")],
            "london": [
                _make_news(title="b"),
                _make_news(title="c"),
            ],
            "ny": [],
        }
        result = self.gen._build_news_result(
            data, session_groups
        )
        detail = json.loads(result["session_detail"])
        assert detail["tokyo"]["count"] == 1
        assert detail["tokyo"]["sentiment"] == 0.2
        assert detail["london"]["count"] == 2
        assert detail["ny"]["count"] == 0


class TestBuildNewsPrompt:
    """_build_news_prompt のテスト"""

    def setup_method(self) -> None:
        """テスト用インスタンス"""
        self.gen = LLMNewsGenerator()

    def test_contains_sessions(self) -> None:
        """プロンプトにセッション別ニュース"""
        session_texts = {
            "tokyo": "- 03:00 | fxstreet | test",
            "london": "（なし）",
            "ny": "（なし）",
        }
        session_counts = {
            "tokyo": 1,
            "london": 0,
            "ny": 0,
        }
        prompt = self.gen._build_news_prompt(
            "USDJPY",
            "USD",
            "JPY",
            date(2024, 1, 15),
            session_texts,
            session_counts,
        )
        assert "東京セッション" in prompt
        assert "ロンドンセッション" in prompt
        assert "NYセッション" in prompt
        assert "USDJPY" in prompt
        assert "sentiment_score" in prompt


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
            "session_sentiment": {
                "tokyo": 0.0,
                "london": 0.3,
                "ny": 0.0,
            },
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
