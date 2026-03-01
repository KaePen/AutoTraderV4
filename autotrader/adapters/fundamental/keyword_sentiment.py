"""キーワードベースの軽量センチメント分析

LLM不要でニュース見出しからセンチメントスコアを算出する。
通貨別 bullish/bearish キーワード辞書を使用。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SentimentResult:
    """キーワード分析結果

    Args:
        score: センチメントスコア (-1.0~+1.0)
        bullish_count: 強気キーワードヒット数
        bearish_count: 弱気キーワードヒット数
        headlines_used: 分析した見出し数
    """

    score: float
    bullish_count: int
    bearish_count: int
    headlines_used: int


# 通貨別のセンチメントキーワード
# キー: 通貨コード（"USD", "JPY" 等）
# 値: {"bullish": [...], "bearish": [...]}
_SENTIMENT_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "USD": {
        "bullish": [
            "fed hawkish",
            "rate hike",
            "strong dollar",
            "us economy strong",
            "employment growth",
            "inflation rising",
            "treasury yields rise",
            "dollar rally",
            "fed tightening",
        ],
        "bearish": [
            "fed dovish",
            "rate cut",
            "weak dollar",
            "us recession",
            "unemployment rise",
            "deflation",
            "treasury yields fall",
            "dollar decline",
            "fed easing",
        ],
    },
    "JPY": {
        "bullish": [
            "boj hawkish",
            "japan rate hike",
            "yen strengthen",
            "boj tightening",
            "yield curve control end",
            "japan inflation",
            "yen intervention buy",
        ],
        "bearish": [
            "boj dovish",
            "negative rates",
            "yen weaken",
            "boj easing",
            "japan deflation",
            "yen intervention sell",
            "safe haven flow",
        ],
    },
    "EUR": {
        "bullish": [
            "ecb hawkish",
            "ecb rate hike",
            "euro strengthen",
            "eurozone growth",
            "ecb tightening",
        ],
        "bearish": [
            "ecb dovish",
            "ecb rate cut",
            "euro weaken",
            "eurozone recession",
            "ecb easing",
        ],
    },
    "GBP": {
        "bullish": [
            "boe hawkish",
            "boe rate hike",
            "pound strengthen",
            "uk growth",
            "boe tightening",
        ],
        "bearish": [
            "boe dovish",
            "boe rate cut",
            "pound weaken",
            "uk recession",
            "boe easing",
            "brexit",
        ],
    },
}


class KeywordSentimentScorer:
    """キーワードベースの軽量センチメント分析器

    通貨別の bullish/bearish キーワード辞書でニュース見出しを
    マッチングし、スコアを算出する。
    """

    def __init__(self) -> None:
        """初期化"""
        self._keywords = _SENTIMENT_KEYWORDS

    def score(
        self,
        headlines: list[str],
        symbol: str,
    ) -> SentimentResult:
        """ヘッドラインからセンチメントスコアを算出

        Args:
            headlines: ニュース見出しリスト
            symbol: 通貨ペア (e.g. "USDJPY")

        Returns:
            SentimentResult: 分析結果
        """
        if not headlines:
            return SentimentResult(
                score=0.0,
                bullish_count=0,
                bearish_count=0,
                headlines_used=0,
            )

        base = symbol[:3].upper()
        quote = symbol[3:6].upper()

        bullish_total = 0
        bearish_total = 0

        for headline in headlines:
            lower = headline.lower()
            # ベース通貨キーワード
            base_kw = self._keywords.get(base, {})
            for kw in base_kw.get("bullish", []):
                if kw in lower:
                    bullish_total += 1
            for kw in base_kw.get("bearish", []):
                if kw in lower:
                    bearish_total += 1
            # クオート通貨キーワード（逆方向）
            quote_kw = self._keywords.get(quote, {})
            for kw in quote_kw.get("bullish", []):
                if kw in lower:
                    bearish_total += 1
            for kw in quote_kw.get("bearish", []):
                if kw in lower:
                    bullish_total += 1

        # スコア正規化
        n = len(headlines)
        raw = (bullish_total - bearish_total) / n
        clipped = max(-1.0, min(1.0, raw))

        return SentimentResult(
            score=clipped,
            bullish_count=bullish_total,
            bearish_count=bearish_total,
            headlines_used=n,
        )
