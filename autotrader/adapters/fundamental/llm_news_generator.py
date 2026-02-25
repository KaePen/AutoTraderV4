"""ニュース分析LLMジェネレーター

ニュースCSVからシンボル関連記事を日次抽出し、
セッション別にLLM分析を行い、
llm_news_SYMBOL_YYYY.csv に出力する。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.llm_generator_base import (
    LLMGeneratorBase,
)
from autotrader.adapters.fundamental.news_schemas import (
    FX_RSS_SOURCES,
    NewsItem,
)
from autotrader.config.llm_settings import OllamaSettings

# ニュースCSVカラム定義
NEWS_CSV_COLUMNS = [
    "date",
    "article_count",
    "sentiment_score",
    "sentiment_confidence",
    "macro_bias_score",
    "policy_divergence_score",
    "risk_appetite_score",
    "geopolitical_risk_level",
    "dominant_theme",
    "summary",
    "session_detail",
]

# セッション時間帯定義（UTC基準、時の範囲 [start, end)）
SESSION_RANGES: dict[str, tuple[int, int]] = {
    "tokyo": (0, 8),
    "london": (8, 14),
    "ny": (14, 24),
}

# セッション名の表示用ラベル
_SESSION_LABELS: dict[str, str] = {
    "tokyo": "東京セッション（UTC 00:00-08:00）",
    "london": "ロンドンセッション（UTC 08:00-14:00）",
    "ny": "NYセッション（UTC 14:00-24:00）",
}

# FX専門ソースの本文抜粋最大文字数
_FX_CONTENT_MAX = 300
_FX_CONTENT_REDUCED = 150

# 各セッション最大記事数（最終フォールバック）
_SESSION_MAX_ARTICLES = 10


class LLMNewsGenerator(LLMGeneratorBase):
    """ニュース分析LLMジェネレーター

    news_rss_YYYY.csv / news/news_YYYY.csv からシンボル関連
    ニュースを日次抽出し、セッション別にLLM分析して
    llm_news_SYMBOL_YYYY.csv に出力する。

    Args:
        ollama_settings: Ollama接続設定
        retry_delay_seconds: リトライ待機秒数
        max_retries: LLM呼び出し最大リトライ回数
        max_prompt_tokens: プロンプト最大トークン見積もり
    """

    def __init__(
        self,
        ollama_settings: OllamaSettings | None = None,
        retry_delay_seconds: float = 2.0,
        max_retries: int = 3,
        max_prompt_tokens: int = 2500,
    ) -> None:
        """初期化

        Args:
            ollama_settings: Ollama接続設定
            retry_delay_seconds: リトライ待機秒数
            max_retries: LLM呼び出し最大リトライ回数
            max_prompt_tokens: プロンプト最大トークン見積もり
        """
        super().__init__(
            ollama_settings=ollama_settings,
            retry_delay_seconds=retry_delay_seconds,
            max_retries=max_retries,
        )
        self._max_prompt_tokens = max_prompt_tokens

    def generate_for_symbol_year(
        self,
        symbol: str,
        year: int,
        news_items: list[NewsItem],
        output_dir: str | Path = "data/fundamental",
        overwrite: bool = False,
    ) -> Path:
        """指定シンボル・年のニュースLLM CSVを生成

        Args:
            symbol: 対象シンボル（例: USDJPY）
            year: 対象年
            news_items: 全ニュースアイテムリスト
            output_dir: 出力ディレクトリ
            overwrite: 既存ファイル上書き

        Returns:
            Path: 生成したCSVパス
        """
        output_path = (
            Path(output_dir) / f"llm_news_{symbol}_{year}.csv"
        )

        # resume: 既存CSVから処理済み行を読み込み
        existing_rows: list[dict] = []
        resume_from: date | None = None
        if output_path.exists() and not overwrite:
            existing_rows = self._read_existing_csv(
                output_path, NEWS_CSV_COLUMNS
            )
            if existing_rows:
                last_date_str = existing_rows[-1].get("date")
                if last_date_str:
                    resume_from = date.fromisoformat(
                        last_date_str
                    )

        # 全日処理済みならスキップ
        full_range = self._generate_date_range(year)
        if resume_from and resume_from >= full_range[-1]:
            logger.info(
                f"[NewsGen] スキップ（完了済み）: "
                f"{output_path}"
            )
            return output_path

        base, quote = self.get_symbol_currencies(symbol)

        # 対象通貨・年のニュース抽出
        relevant = self._filter_news(
            news_items, (base, quote), year
        )

        # 日付ごとにグループ化
        daily_news = self._group_by_date(relevant)

        # resume位置を決定
        if resume_from:
            date_range = [
                d for d in full_range if d > resume_from
            ]
            rows: list[dict] = list(existing_rows)
            logger.info(
                f"[NewsGen] {symbol}/{year}: "
                f"resume {resume_from} から "
                f"残り{len(date_range)}日"
            )
        else:
            date_range = full_range
            rows = []

        logger.info(
            f"[NewsGen] {symbol}/{year}: "
            f"全{len(news_items)}件→{len(relevant)}件"
        )

        total_days = len(full_range)
        llm_calls = 0

        for target_date in date_range:
            idx = (target_date - full_range[0]).days + 1
            day_news = daily_news.get(target_date, [])
            result = self._analyze_date(
                symbol, base, quote, target_date, day_news
            )
            if day_news:
                llm_calls += 1

            result["date"] = target_date.isoformat()
            result["article_count"] = len(day_news)
            rows.append(result)

            if idx % 50 == 0 or idx == total_days:
                logger.info(
                    f"[NewsGen] {symbol}/{year}: "
                    f"{idx}/{total_days}日完了 "
                    f"(LLM:{llm_calls})"
                )

            # 50日ごとに中間保存（resume用）
            if idx % 50 == 0:
                self._write_csv(
                    rows, NEWS_CSV_COLUMNS, output_path
                )

        # 最終書き込み
        self._write_csv(rows, NEWS_CSV_COLUMNS, output_path)
        logger.info(
            f"[NewsGen] 完了: {output_path} ({len(rows)}日)"
        )
        return output_path

    def _filter_news(
        self,
        news_items: list[NewsItem],
        currencies: tuple[str, str],
        year: int,
    ) -> list[NewsItem]:
        """対象シンボル・年のニュースのみ抽出

        Args:
            news_items: 全ニュースアイテム
            currencies: (base, quote)
            year: 対象年

        Returns:
            list[NewsItem]: フィルタ済み
        """
        return [
            item
            for item in news_items
            if any(c in currencies for c in item.currencies)
            and item.published_at.year == year
        ]

    def _group_by_date(
        self,
        news_items: list[NewsItem],
    ) -> dict[date, list[NewsItem]]:
        """ニュースを日付ごとにグループ化

        Args:
            news_items: フィルタ済みニュース

        Returns:
            dict[date, list[NewsItem]]
        """
        result: dict[date, list[NewsItem]] = defaultdict(list)
        for item in news_items:
            result[item.published_at.date()].append(item)
        return dict(result)

    def _split_by_session(
        self,
        news_items: list[NewsItem],
    ) -> dict[str, list[NewsItem]]:
        """ニュースをセッション別にグループ化

        tokyo:  00:00-07:59 UTC
        london: 08:00-13:59 UTC
        ny:     14:00-23:59 UTC

        Args:
            news_items: 当日のニュースリスト

        Returns:
            dict[str, list[NewsItem]]
        """
        result: dict[str, list[NewsItem]] = {
            "tokyo": [],
            "london": [],
            "ny": [],
        }
        for item in news_items:
            hour = item.published_at.hour
            if hour < 8:
                result["tokyo"].append(item)
            elif hour < 14:
                result["london"].append(item)
            else:
                result["ny"].append(item)
        return result

    def _compress_for_prompt(
        self,
        session_groups: dict[str, list[NewsItem]],
    ) -> dict[str, str]:
        """セッション別ニュースをプロンプト用テキストに圧縮

        圧縮戦略:
        1. FX専門ソース記事: 全件（本文300文字まで）
        2. 一般ソース記事: ソースごと最大1件、見出しのみ
        3. トークン超過時: 一般ソース除外 + 本文150文字短縮
        4. それでも超過: 各セッション最大10件、見出しのみ

        Args:
            session_groups: セッション別ニュースリスト

        Returns:
            dict[str, str]: セッション別テキスト
        """
        session_texts: dict[str, str] = {}

        for session_name, items in session_groups.items():
            if not items:
                session_texts[session_name] = "（なし）"
                continue

            # FX専門ソースと一般ソースに分類
            fx_items = [
                i
                for i in items
                if i.source_name in FX_RSS_SOURCES
            ]
            general_items = [
                i
                for i in items
                if i.source_name not in FX_RSS_SOURCES
            ]

            # レベル1: FX全件（本文300字）+ 一般ソースごと1件
            text = self._format_session_articles(
                fx_items,
                general_items,
                content_max=_FX_CONTENT_MAX,
                include_general=True,
            )
            if self._estimate_tokens(text) <= self._max_prompt_tokens:
                session_texts[session_name] = text
                continue

            # レベル2: FX全件（本文150字）+ 一般除外
            text = self._format_session_articles(
                fx_items,
                [],
                content_max=_FX_CONTENT_REDUCED,
                include_general=False,
            )
            if self._estimate_tokens(text) <= self._max_prompt_tokens:
                session_texts[session_name] = text
                continue

            # レベル3: 先頭10件、見出しのみ
            limited = sorted(
                items, key=lambda i: i.published_at
            )[:_SESSION_MAX_ARTICLES]
            lines = []
            for item in limited:
                time_str = item.published_at.strftime("%H:%M")
                lines.append(
                    f"- {time_str} | {item.source_name} | "
                    f"{item.title}\n  [見出しのみ]"
                )
            session_texts[session_name] = "\n".join(lines)

        return session_texts

    def _format_session_articles(
        self,
        fx_items: list[NewsItem],
        general_items: list[NewsItem],
        content_max: int,
        include_general: bool,
    ) -> str:
        """セッション内記事をテキストに変換

        Args:
            fx_items: FX専門ソース記事
            general_items: 一般ソース記事
            content_max: 本文抜粋の最大文字数
            include_general: 一般ソースを含めるか

        Returns:
            str: フォーマット済みテキスト
        """
        lines = []

        # FX専門ソース: 全件（本文付き）
        for item in sorted(
            fx_items, key=lambda i: i.published_at
        ):
            time_str = item.published_at.strftime("%H:%M")
            line = (
                f"- {time_str} | {item.source_name} | "
                f"{item.title}"
            )
            if item.content:
                summary = item.content[:content_max].replace(
                    "\n", " "
                )
                line += f"\n  本文抜粋: {summary}..."
            else:
                line += "\n  [見出しのみ]"
            lines.append(line)

        # 一般ソース: ソースごと最大1件、見出しのみ
        if include_general and general_items:
            seen_sources: set[str] = set()
            for item in sorted(
                general_items, key=lambda i: i.published_at
            ):
                if item.source_name in seen_sources:
                    continue
                seen_sources.add(item.source_name)
                time_str = item.published_at.strftime("%H:%M")
                lines.append(
                    f"- {time_str} | {item.source_name} | "
                    f"{item.title}\n  [見出しのみ]"
                )

        return "\n".join(lines) if lines else "（なし）"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """テキストのトークン数を概算

        日本語/英語混在を考慮した簡易見積もり。
        1トークン ≒ 英語4文字 or 日本語1.5文字。
        安全マージンを取り、文字数/3 で概算する。

        Args:
            text: 対象テキスト

        Returns:
            int: 推定トークン数
        """
        if not text:
            return 0
        return max(1, len(text) // 3)

    def _analyze_date(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        news_items: list[NewsItem],
    ) -> dict:
        """1日分のニュースをLLM分析

        ニュースが0件の場合はLLM呼び出しをスキップし
        デフォルト値を返す。

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 分析対象日
            news_items: 当日ニュース

        Returns:
            dict: 分析結果
        """
        if not news_items:
            return self._default_news_result()

        # セッション別グループ化
        session_groups = self._split_by_session(news_items)

        # プロンプト用テキストに圧縮
        session_texts = self._compress_for_prompt(
            session_groups
        )
        session_counts = {
            s: len(items)
            for s, items in session_groups.items()
        }

        prompt = self._build_news_prompt(
            symbol,
            base,
            quote,
            target_date,
            session_texts,
            session_counts,
        )
        raw = self._call_ollama_with_retry(
            prompt, self._default_news_result_raw()
        )
        return self._build_news_result(raw, session_groups)

    def _build_news_prompt(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        session_texts: dict[str, str],
        session_counts: dict[str, int],
    ) -> str:
        """ニュース分析プロンプトを構築

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 対象日
            session_texts: セッション別テキスト
            session_counts: セッション別記事数

        Returns:
            str: プロンプト文字列
        """
        sessions_block = ""
        for session_key in ("tokyo", "london", "ny"):
            label = _SESSION_LABELS[session_key]
            count = session_counts.get(session_key, 0)
            text = session_texts.get(session_key, "（なし）")
            sessions_block += (
                f"\n## {label}のニュース（{count}件）\n"
                f"{text}\n"
            )

        return f"""あなたはFXトレードのニュースアナリストです。
以下のニュース記事群に基づき、{symbol}に対する市場センチメントを分析してください。

## 分析対象
- シンボル: {symbol} ({base}/{quote})
- 分析日: {target_date.year}年{target_date.month}月{target_date.day}日
{sessions_block}
## 分析指示
1. 各セッションのセンチメント傾向を個別に評価
2. {base}と{quote}に関するニュースの方向性を区別
3. 金融政策に関する言及（利上げ/利下げ観測等）を特に重視
4. 地政学リスク要因の有無と影響度を評価
5. リスク選好/回避の傾向を判断
6. センチメントの一貫性に基づき確信度を設定

## 出力形式（JSONのみで回答）
{{
  "sentiment_score": <-1.0~+1.0: 総合センチメント。+は{symbol}強気>,
  "sentiment_confidence": <0.0~1.0: 確信度>,
  "macro_bias_score": <-1.0~+1.0: マクロ経済バイアス>,
  "policy_divergence_score": <-1.0~+1.0: 金融政策乖離。+は{base}引締め優位>,
  "risk_appetite_score": <-1.0~+1.0: リスク選好度。+はリスクオン>,
  "geopolitical_risk_level": <0/1/2/3: 地政学リスク度>,
  "dominant_theme": "<支配的テーマ（日本語、100文字以内）>",
  "summary": "<分析要約（日本語、200文字以内）>",
  "session_sentiment": {{
    "tokyo": <-1.0~+1.0>,
    "london": <-1.0~+1.0>,
    "ny": <-1.0~+1.0>
  }}
}}"""

    def _build_news_result(
        self,
        data: dict,
        session_groups: dict[str, list[NewsItem]],
    ) -> dict:
        """LLMレスポンスからニュース結果dictを構築

        Args:
            data: LLMレスポンスdict
            session_groups: セッション別ニュース（件数取得用）

        Returns:
            dict: バリデーション済み結果
        """
        geo_risk = data.get("geopolitical_risk_level")
        if geo_risk is None or not isinstance(
            geo_risk, (int, float)
        ):
            geo_risk_val = 0
        else:
            geo_risk_val = max(0, min(3, int(geo_risk)))

        # session_detail を構築
        session_sentiment = data.get("session_sentiment", {})
        if not isinstance(session_sentiment, dict):
            session_sentiment = {}

        session_detail = {}
        for session_key in ("tokyo", "london", "ny"):
            session_detail[session_key] = {
                "count": len(
                    session_groups.get(session_key, [])
                ),
                "sentiment": self._clip_score(
                    session_sentiment.get(session_key)
                ),
            }

        return {
            "sentiment_score": self._clip_score(
                data.get("sentiment_score")
            ),
            "sentiment_confidence": self._clip(
                data.get("sentiment_confidence"),
                0.0,
                1.0,
                0.0,
            ),
            "macro_bias_score": self._clip_score(
                data.get("macro_bias_score")
            ),
            "policy_divergence_score": self._clip_score(
                data.get("policy_divergence_score")
            ),
            "risk_appetite_score": self._clip_score(
                data.get("risk_appetite_score")
            ),
            "geopolitical_risk_level": geo_risk_val,
            "dominant_theme": str(
                data.get("dominant_theme", "")
            )[:100],
            "summary": str(data.get("summary", ""))[:200],
            "session_detail": json.dumps(
                session_detail, ensure_ascii=False
            ),
        }

    @staticmethod
    def _default_news_result() -> dict:
        """ニュースなし日のデフォルト結果

        Returns:
            dict: デフォルト値辞書
        """
        return {
            "sentiment_score": 0.0,
            "sentiment_confidence": 0.0,
            "macro_bias_score": 0.0,
            "policy_divergence_score": 0.0,
            "risk_appetite_score": 0.0,
            "geopolitical_risk_level": 0,
            "dominant_theme": "",
            "summary": "関連ニュースなし",
            "session_detail": json.dumps(
                {
                    "tokyo": {"count": 0, "sentiment": 0.0},
                    "london": {"count": 0, "sentiment": 0.0},
                    "ny": {"count": 0, "sentiment": 0.0},
                },
                ensure_ascii=False,
            ),
        }

    @staticmethod
    def _default_news_result_raw() -> dict:
        """LLMリトライ全失敗時の生デフォルト値

        _build_news_result に渡されるため、
        LLMレスポンス形式に合わせる。

        Returns:
            dict: デフォルト値辞書
        """
        return {
            "sentiment_score": 0.0,
            "sentiment_confidence": 0.0,
            "macro_bias_score": 0.0,
            "policy_divergence_score": 0.0,
            "risk_appetite_score": 0.0,
            "geopolitical_risk_level": 0,
            "dominant_theme": "",
            "summary": "分析失敗",
            "session_sentiment": {
                "tokyo": 0.0,
                "london": 0.0,
                "ny": 0.0,
            },
        }
