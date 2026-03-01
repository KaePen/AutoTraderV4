"""KeywordSentimentScorer のユニットテスト"""
from __future__ import annotations

from autotrader.adapters.fundamental.keyword_sentiment import (
    KeywordSentimentScorer,
    SentimentResult,
)


class TestKeywordSentimentScorer:
    """KeywordSentimentScorer テスト"""

    def setup_method(self):
        self.scorer = KeywordSentimentScorer()

    def test_bullish_usd_headlines(self):
        """USD強気ヘッドラインでスコアがプラス"""
        headlines = [
            "Fed hawkish stance boosts dollar",
            "Strong US employment data released",
        ]
        result = self.scorer.score(headlines, "USDJPY")
        assert result.score > 0
        assert result.bullish_count > 0
        assert result.headlines_used == 2

    def test_bearish_usd_headlines(self):
        """USD弱気ヘッドラインでスコアがマイナス"""
        headlines = [
            "Fed dovish signal weakens dollar",
            "US recession fears grow",
        ]
        result = self.scorer.score(headlines, "USDJPY")
        assert result.score < 0
        assert result.bearish_count > 0

    def test_mixed_headlines(self):
        """混合ヘッドラインでスコアは小さくなる"""
        headlines = [
            "Fed hawkish stance boosts dollar",
            "Fed dovish signal weakens dollar",
        ]
        result = self.scorer.score(headlines, "USDJPY")
        assert abs(result.score) < 0.5

    def test_empty_headlines(self):
        """空リストでスコアはゼロ"""
        result = self.scorer.score([], "USDJPY")
        assert result.score == 0.0
        assert result.headlines_used == 0

    def test_no_keyword_match(self):
        """マッチしないヘッドラインでスコアはゼロ"""
        headlines = [
            "Weather forecast for Tokyo tomorrow",
            "Movie release schedule announced",
        ]
        result = self.scorer.score(headlines, "USDJPY")
        assert result.score == 0.0
        assert result.bullish_count == 0
        assert result.bearish_count == 0

    def test_quote_currency_effect(self):
        """クオート通貨（JPY）の強気がペア弱気"""
        headlines = [
            "BOJ hawkish shift surprises markets",
        ]
        result = self.scorer.score(headlines, "USDJPY")
        # JPY bullish → USDJPY bearish
        assert result.score < 0

    def test_eurusd(self):
        """EURUSD でのスコアリング"""
        headlines = [
            "ECB hawkish stance on rates",
            "Fed dovish signal",
        ]
        result = self.scorer.score(headlines, "EURUSD")
        # EUR bullish + USD bearish → EURUSD bullish
        assert result.score > 0

    def test_score_clipping(self):
        """スコアは-1.0~+1.0にクリップ"""
        # 大量の一方向ヘッドライン
        headlines = [
            f"Fed hawkish {i}" for i in range(50)
        ]
        result = self.scorer.score(headlines, "USDJPY")
        assert -1.0 <= result.score <= 1.0

    def test_result_dataclass(self):
        """SentimentResult が正しい型"""
        result = self.scorer.score(
            ["Fed hawkish"], "USDJPY"
        )
        assert isinstance(result, SentimentResult)
        assert isinstance(result.score, float)
        assert isinstance(result.bullish_count, int)
        assert isinstance(result.bearish_count, int)
        assert isinstance(result.headlines_used, int)
