"""リアルタイムLLMニュース分析モジュール（ライブトレード用）

RSSフィードから収集したニュースアイテムを
Ollamaを使ってリアルタイムで分析し、
シンボルごとのセンチメントスコアをキャッシュする。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from loguru import logger

from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
)

try:
    import ollama as _ollama_module
except ImportError:
    _ollama_module = None  # type: ignore[assignment]

# LLMデフォルト設定
_DEFAULT_MODEL = "qwen3:14b"
_DEFAULT_HOST = "http://localhost:11434"
_DEFAULT_TTL_HOURS = 4


class NewsLLMAnalyzer:
    """リアルタイムLLMニュース分析器

    ニュース群からセンチメントスコアを算出し、
    TTL付きキャッシュで管理する。

    Args:
        model: 使用するOllamaモデル名
        host: OllamaホストURL
        sentiment_ttl_hours: センチメントスコアの有効期間（時間）
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        host: str = _DEFAULT_HOST,
        sentiment_ttl_hours: int = _DEFAULT_TTL_HOURS,
    ) -> None:
        """初期化

        Args:
            model: 使用するOllamaモデル名
            host: OllamaホストURL
            sentiment_ttl_hours: センチメントスコアの有効期間
        """
        self._model = model
        self._host = host
        self._ttl = timedelta(hours=sentiment_ttl_hours)

        # symbol → (score, expires_at) のキャッシュ
        self._cache: dict[str, tuple[float, datetime]] = {}

    async def analyze(
        self,
        news_items: list[NewsItem],
        symbol: str,
    ) -> float:
        """ニュース群からセンチメントスコアを算出

        結果は self._cache[symbol] にTTL付きでキャッシュする。

        Args:
            news_items: 分析するニュースアイテムリスト
            symbol: 対象シンボル（例: "USDJPY"）

        Returns:
            float: センチメントスコア（-1.0〜+1.0）
        """
        if not news_items:
            return self.get_current_sentiment(symbol)

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            score = await loop.run_in_executor(
                None,
                self._call_ollama_sync,
                news_items,
                symbol,
            )
        except Exception as e:
            logger.warning(
                f"[NewsLLM] {symbol} 分析失敗: {e}"
            )
            return self.get_current_sentiment(symbol)

        # キャッシュ更新
        expires_at = datetime.now(timezone.utc) + self._ttl
        self._cache[symbol] = (score, expires_at)
        logger.info(
            f"[NewsLLM] {symbol} センチメント: "
            f"{score:+.2f} (TTL: {self._ttl})"
        )
        return score

    def get_current_sentiment(self, symbol: str) -> float:
        """キャッシュから有効なセンチメントスコアを返す

        TTL切れの場合は0.0を返す。

        Args:
            symbol: 対象シンボル

        Returns:
            float: センチメントスコア（-1.0〜+1.0）
        """
        cached = self._cache.get(symbol)
        if cached is None:
            return 0.0

        score, expires_at = cached
        if datetime.now(timezone.utc) > expires_at:
            # TTL切れ
            del self._cache[symbol]
            return 0.0

        return score

    def _call_ollama_sync(
        self,
        news_items: list[NewsItem],
        symbol: str,
    ) -> float:
        """Ollamaでセンチメント分析を同期実行

        Args:
            news_items: ニュースアイテムリスト
            symbol: 対象シンボル

        Returns:
            float: センチメントスコア（-1.0〜+1.0）

        Raises:
            RuntimeError: Ollama未インストール時
        """
        if _ollama_module is None:
            raise RuntimeError(
                "ollama パッケージが必要です: "
                "pip install ollama"
            )

        prompt = self._build_prompt(news_items, symbol)
        client = _ollama_module.Client(host=self._host)
        response = client.chat(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={"temperature": 0.1},
        )
        content = response.message.content
        return self._parse_score(content)

    def _build_prompt(
        self,
        news_items: list[NewsItem],
        symbol: str,
    ) -> str:
        """センチメント分析プロンプトを構築

        Args:
            news_items: ニュースアイテムリスト（上位10件使用）
            symbol: 対象シンボル

        Returns:
            str: LLMプロンプト
        """
        headlines = "\n".join(
            f"- {item.title}"
            for item in news_items[:10]
        )
        return f"""あなたはFXトレーダーのアシスタントです。
以下のニュース見出しを分析して、{symbol}に対する
市場センチメントスコアを算出してください。

## ニュース見出し
{headlines}

## 出力形式（JSONのみで回答）
{{
  "sentiment_score": <-1.0から+1.0（+は強気/買い、-は弱気/売り）>
}}"""

    def _parse_score(self, content: str) -> float:
        """LLMレスポンスからセンチメントスコアを抽出

        Args:
            content: LLMのレスポンス文字列

        Returns:
            float: センチメントスコア（失敗時は0.0）
        """
        # 直接JSONパース
        try:
            data = json.loads(content)
            score = data.get("sentiment_score", 0.0)
            return max(-1.0, min(1.0, float(score)))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # コードブロック内のJSONを抽出
        json_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            content,
            re.DOTALL,
        )
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                score = data.get("sentiment_score", 0.0)
                return max(-1.0, min(1.0, float(score)))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # { ... } の最初の出現を抽出
        brace_match = re.search(
            r"\{[^{}]*\}", content, re.DOTALL
        )
        if brace_match:
            try:
                data = json.loads(brace_match.group(0))
                score = data.get("sentiment_score", 0.0)
                return max(-1.0, min(1.0, float(score)))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        logger.warning(
            f"[NewsLLM] スコアパース失敗: {content[:100]}"
        )
        return 0.0
