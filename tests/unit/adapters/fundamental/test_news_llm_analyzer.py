"""NewsLLMAnalyzer のユニットテスト"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from autotrader.adapters.fundamental.news_llm_analyzer import (
    NewsLLMAnalyzer,
)
from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
    NewsSource,
)


def _make_item(idx: int = 0) -> NewsItem:
    """テスト用NewsItemを生成"""
    return NewsItem(
        news_id=f"gdelt_{idx:012d}",
        published_at=datetime(
            2024, 1, 1, tzinfo=timezone.utc
        ),
        title=f"Federal Reserve holds rates steady {idx}",
        source_name="reuters.com",
        source_url=f"https://reuters.com/{idx}",
        currencies=["USD", "JPY"],
        source_type=NewsSource.GDELT,
    )


class TestNewsLLMAnalyzerGetCurrentSentiment:
    """get_current_sentiment のテスト"""

    def test_キャッシュなしは0(self) -> None:
        """キャッシュがない場合は0.0を返す"""
        analyzer = NewsLLMAnalyzer()
        assert analyzer.get_current_sentiment("USDJPY") == 0.0

    def test_有効なキャッシュはスコアを返す(self) -> None:
        """TTL内のキャッシュはスコアを返す"""
        analyzer = NewsLLMAnalyzer(sentiment_ttl_hours=4)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=2
        )
        analyzer._cache["USDJPY"] = (0.5, expires_at)

        assert analyzer.get_current_sentiment("USDJPY") == 0.5

    def test_TTL切れは0を返す(self) -> None:
        """TTLが切れたキャッシュは0.0を返す"""
        analyzer = NewsLLMAnalyzer(sentiment_ttl_hours=4)
        # 過去の日時（TTL切れ）
        expires_at = datetime.now(timezone.utc) - timedelta(
            hours=1
        )
        analyzer._cache["USDJPY"] = (0.8, expires_at)

        assert analyzer.get_current_sentiment("USDJPY") == 0.0

    def test_TTL切れでキャッシュが削除される(self) -> None:
        """TTL切れのキャッシュが get 後に削除される"""
        analyzer = NewsLLMAnalyzer()
        expires_at = datetime.now(timezone.utc) - timedelta(
            hours=1
        )
        analyzer._cache["USDJPY"] = (0.8, expires_at)

        analyzer.get_current_sentiment("USDJPY")
        assert "USDJPY" not in analyzer._cache

    def test_異なるシンボルは独立(self) -> None:
        """シンボルが異なればキャッシュは独立"""
        analyzer = NewsLLMAnalyzer()
        expires = datetime.now(timezone.utc) + timedelta(
            hours=2
        )
        analyzer._cache["USDJPY"] = (0.5, expires)
        analyzer._cache["EURUSD"] = (-0.3, expires)

        assert analyzer.get_current_sentiment("USDJPY") == 0.5
        assert (
            analyzer.get_current_sentiment("EURUSD") == -0.3
        )
        assert analyzer.get_current_sentiment("GBPJPY") == 0.0


class TestNewsLLMAnalyzerParseScore:
    """_parse_score のテスト"""

    def setup_method(self) -> None:
        """セットアップ"""
        self.analyzer = NewsLLMAnalyzer()

    def test_正常JSON(self) -> None:
        """正常なJSONからスコアを抽出"""
        content = '{"sentiment_score": 0.7}'
        score = self.analyzer._parse_score(content)
        assert score == pytest.approx(0.7)

    def test_コードブロック内JSON(self) -> None:
        """コードブロック内のJSONを抽出"""
        content = (
            '```json\n{"sentiment_score": -0.4}\n```'
        )
        score = self.analyzer._parse_score(content)
        assert score == pytest.approx(-0.4)

    def test_テキスト内のJSON(self) -> None:
        """テキスト内のJSONを抽出"""
        content = (
            'Analysis complete. {"sentiment_score": 0.3}'
        )
        score = self.analyzer._parse_score(content)
        assert score == pytest.approx(0.3)

    def test_1より大きい値は1にクリップ(self) -> None:
        """1.0より大きいスコアは1.0にクリップ"""
        content = '{"sentiment_score": 1.5}'
        score = self.analyzer._parse_score(content)
        assert score == pytest.approx(1.0)

    def test_負1より小さい値はマイナス1にクリップ(
        self,
    ) -> None:
        """-1.0より小さいスコアは-1.0にクリップ"""
        content = '{"sentiment_score": -2.0}'
        score = self.analyzer._parse_score(content)
        assert score == pytest.approx(-1.0)

    def test_パース失敗は0を返す(self) -> None:
        """JSON解析失敗は0.0を返す"""
        content = "申し訳ありません、分析できません"
        score = self.analyzer._parse_score(content)
        assert score == pytest.approx(0.0)


class TestNewsLLMAnalyzerAnalyze:
    """analyze のテスト"""

    @pytest.mark.asyncio
    async def test_空リストは既存キャッシュを返す(
        self,
    ) -> None:
        """空のニュースリストは既存キャッシュを返す"""
        analyzer = NewsLLMAnalyzer()
        # 事前にキャッシュを設定
        expires = datetime.now(timezone.utc) + timedelta(
            hours=2
        )
        analyzer._cache["USDJPY"] = (0.4, expires)

        score = await analyzer.analyze([], "USDJPY")
        assert score == pytest.approx(0.4)

    @pytest.mark.asyncio
    async def test_ollama未インストール時は既存値を返す(
        self,
    ) -> None:
        """ollama が None の場合は既存キャッシュを返す"""
        import autotrader.adapters.fundamental.news_llm_analyzer as m
        original = m._ollama_module
        m._ollama_module = None  # type: ignore[assignment]
        try:
            analyzer = NewsLLMAnalyzer()
            score = await analyzer.analyze(
                [_make_item()], "USDJPY"
            )
            # キャッシュなし → 0.0
            assert score == pytest.approx(0.0)
        finally:
            m._ollama_module = original

    @pytest.mark.asyncio
    async def test_LLM成功時はキャッシュに保存(
        self,
    ) -> None:
        """LLM呼び出し成功時はスコアをキャッシュに保存"""
        mock_ollama = MagicMock()
        mock_response = MagicMock()
        mock_response.message.content = (
            '{"sentiment_score": 0.6}'
        )
        mock_ollama.Client.return_value.chat.return_value = (
            mock_response
        )

        import autotrader.adapters.fundamental.news_llm_analyzer as m
        original = m._ollama_module
        m._ollama_module = mock_ollama  # type: ignore[assignment]
        try:
            analyzer = NewsLLMAnalyzer()
            score = await analyzer.analyze(
                [_make_item()], "USDJPY"
            )
            assert score == pytest.approx(0.6)
            assert "USDJPY" in analyzer._cache
        finally:
            m._ollama_module = original


class TestNewsLLMAnalyzerBuildPrompt:
    """_build_prompt のテスト"""

    def setup_method(self) -> None:
        """セットアップ"""
        self.analyzer = NewsLLMAnalyzer()

    def test_シンボル含む(self) -> None:
        """プロンプトにシンボルが含まれる"""
        items = [_make_item()]
        prompt = self.analyzer._build_prompt(items, "USDJPY")
        assert "USDJPY" in prompt

    def test_ニュース見出し含む(self) -> None:
        """プロンプトにニュース見出しが含まれる"""
        items = [_make_item(0), _make_item(1)]
        prompt = self.analyzer._build_prompt(items, "USDJPY")
        assert "Federal Reserve holds rates steady" in prompt

    def test_最大10件のみ使用(self) -> None:
        """15件渡しても最大10件のみプロンプトに含む"""
        items = [_make_item(i) for i in range(15)]
        prompt = self.analyzer._build_prompt(items, "USDJPY")
        # 11件目以降は除外
        assert "Federal Reserve holds rates steady 10" not in prompt
        assert "Federal Reserve holds rates steady 9" in prompt
