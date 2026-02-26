"""リアルタイムLLMニュース分析モジュール（ライブトレード用）

RSSフィードから収集したニュースアイテムを
Ollamaを使ってリアルタイムで分析し、
シンボルごとのセンチメントスコアをキャッシュする。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta

from loguru import logger

from autotrader.adapters.fundamental.news_schemas import (
    NewsItem,
)

try:
    import ollama as _ollama_module
except ImportError:
    _ollama_module = None  # type: ignore[assignment]

# LLMデフォルト設定
_DEFAULT_MODEL = "erwan2/DeepSeek-R1-Distill-Qwen-14B"
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
        # ollama.Client の遅延初期化（初回呼び出しで生成）
        self._client: object | None = None

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
            loop = asyncio.get_running_loop()
            score = await loop.run_in_executor(
                None,
                self._call_ollama_sync,
                news_items,
                symbol,
            )
        except Exception as e:
            logger.warning(f"[NewsLLM] {symbol} 分析失敗: {e}")
            return self.get_current_sentiment(symbol)

        # キャッシュ更新
        expires_at = datetime.now(UTC) + self._ttl
        self._cache[symbol] = (score, expires_at)
        logger.info(
            f"[NewsLLM] {symbol} センチメント: {score:+.2f} (TTL: {self._ttl})"
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
        if datetime.now(UTC) > expires_at:
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
                "ollama パッケージが必要です: pip install ollama"
            )

        # 遅延初期化: 初回呼び出し時のみ Client を生成
        if self._client is None:
            self._client = _ollama_module.Client(
                host=self._host, timeout=120.0,
            )

        prompt = self._build_prompt(news_items, symbol)
        # R1モデルはformat="json"と<think>が競合するため使わない
        response = self._client.chat(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "FXアナリストとして回答してください。"
                        "JSONのみで回答してください。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0.1},
        )
        content = response.message.content
        # R1モデル: <think>ブロックを除去
        cleaned = re.sub(
            r"<think>.*?</think>", "", content, flags=re.DOTALL,
        ).strip()
        return self._parse_score(cleaned or content)

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
        top_items = news_items[:10]
        headlines = "\n".join(f"- {item.title}" for item in top_items)

        # 本文抜粋セクション（上位5件、各500文字、合計3000文字上限）
        # フォールバック: content → snippet → 見出しのみ
        content_section = ""
        items_with_text = [
            item for item in top_items
            if self._get_useful_text(item)
        ][:5]
        if items_with_text:
            excerpts = []
            total = 0
            for item in items_with_text:
                excerpt = self._get_useful_text(item) or ""
                if total + len(excerpt) > 3000:
                    break
                excerpts.append(f"### {item.title}\n{excerpt}")
                total += len(excerpt)
            if excerpts:
                content_section = (
                    "\n\n## 記事本文（抜粋）\n"
                    + "\n\n".join(excerpts)
                )

        # タイトルのみ件数
        title_only = sum(
            1 for item in top_items
            if not self._get_useful_text(item)
        )
        title_note = ""
        if title_only > 0:
            title_note = (
                f"\n※ {title_only}件は本文未取得のため"
                "見出しのみ。見出しから判断してください。"
            )

        return f"""あなたはFXトレーダーのアシスタントです。
以下のニュース見出しと記事本文を分析して、{symbol}に対する
市場センチメントスコアを算出してください。
記事本文がないものは見出しのみで判断してください。

## ニュース見出し
{headlines}{title_note}{content_section}

## 出力形式（JSONのみで回答）
{{
  "sentiment_score": <-1.0から+1.0（+は強気/買い、-は弱気/売り）>
}}"""

    @staticmethod
    def _get_useful_text(
        item: NewsItem,
        max_len: int = 500,
    ) -> str | None:
        """記事から有用なテキストを取得

        フォールバック: content → snippet → None

        Args:
            item: ニュースアイテム
            max_len: 最大文字数

        Returns:
            str | None: 有用テキスト
        """
        if item.content and len(item.content.strip()) >= 50:
            return item.content[:max_len]
        if item.snippet and item.snippet.strip():
            return item.snippet[:max_len]
        return None

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
        brace_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if brace_match:
            try:
                data = json.loads(brace_match.group(0))
                score = data.get("sentiment_score", 0.0)
                return max(-1.0, min(1.0, float(score)))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        logger.warning(f"[NewsLLM] スコアパース失敗: {content[:100]}")
        return 0.0
