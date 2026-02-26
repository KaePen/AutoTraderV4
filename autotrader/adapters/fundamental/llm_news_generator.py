"""ニュース分析LLMジェネレーター

ニュースCSVからシンボル関連記事を日次抽出し、
Map-Reduceパターンでバッチ分割→中間要約→統合分析を行い、
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

# Map-Reduceバッチサイズ（これ以下なら単一呼び出し）
_MAP_BATCH_SIZE = 12

# FX専門ソースの本文抜粋最大文字数
_FX_CONTENT_MAX = 300
_FX_CONTENT_REDUCED = 150

# コンテンツ有効判定の最小文字数
_MIN_USEFUL_CONTENT_LEN = 50


class LLMNewsGenerator(LLMGeneratorBase):
    """ニュース分析LLMジェネレーター

    news_rss_YYYY.csv からシンボル関連ニュースを日次抽出し、
    Map-ReduceパターンでLLM分析して
    llm_news_SYMBOL_YYYY.csv に出力する。

    記事数 <= _MAP_BATCH_SIZE: 単一LLM呼び出し
    記事数 > _MAP_BATCH_SIZE: バッチ分割→中間要約→統合分析

    Args:
        ollama_settings: Ollama接続設定
        retry_delay_seconds: リトライ待機秒数
        max_retries: LLM呼び出し最大リトライ回数
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
            max_prompt_tokens: 未使用（後方互換用）
        """
        super().__init__(
            ollama_settings=ollama_settings,
            retry_delay_seconds=retry_delay_seconds,
            max_retries=max_retries,
        )

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

            # 毎日保存（resume対応）
            self._write_csv(
                rows, NEWS_CSV_COLUMNS, output_path
            )

            if idx % 50 == 0 or idx == total_days:
                logger.info(
                    f"[NewsGen] {symbol}/{year}: "
                    f"{idx}/{total_days}日完了 "
                    f"(LLM:{llm_calls})"
                )
        logger.info(
            f"[NewsGen] 完了: {output_path} ({len(rows)}日)"
        )
        return output_path

    # ── フィルタリング・グループ化 ──

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

    # ── 分析ディスパッチ ──

    def _analyze_date(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        news_items: list[NewsItem],
    ) -> dict:
        """1日分のニュースをLLM分析（Map-Reduce対応）

        記事数が _MAP_BATCH_SIZE 以下なら単一呼び出し、
        超える場合はMap-Reduceパターンで分析する。

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

        if len(news_items) <= _MAP_BATCH_SIZE:
            return self._analyze_single(
                symbol, base, quote, target_date, news_items
            )
        return self._analyze_map_reduce(
            symbol, base, quote, target_date, news_items
        )

    def _analyze_single(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        news_items: list[NewsItem],
    ) -> dict:
        """少記事日の単一LLM分析

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 分析対象日
            news_items: 当日ニュース（<= _MAP_BATCH_SIZE件）

        Returns:
            dict: 分析結果
        """
        articles_text = self._format_articles_for_batch(
            news_items
        )
        prompt = self._build_single_prompt(
            symbol, base, quote, target_date,
            articles_text, len(news_items),
        )
        raw = self._call_ollama_with_retry(
            prompt, self._default_news_result_raw()
        )
        return self._build_final_result(raw)

    def _analyze_map_reduce(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        news_items: list[NewsItem],
    ) -> dict:
        """多記事日のMap-Reduce LLM分析

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 分析対象日
            news_items: 当日ニュース（> _MAP_BATCH_SIZE件）

        Returns:
            dict: 分析結果
        """
        batches = self._split_into_batches(
            news_items, _MAP_BATCH_SIZE
        )
        logger.debug(
            f"[NewsGen] Map-Reduce: {len(news_items)}件"
            f"→{len(batches)}バッチ"
        )

        # MAP フェーズ
        batch_summaries: list[dict] = []
        map_default = self._default_map_result()
        for i, batch in enumerate(batches):
            batch_text = self._format_articles_for_batch(
                batch
            )
            prompt = self._build_map_prompt(
                symbol, base, quote, target_date,
                batch_text, i + 1, len(batches),
            )
            result = self._call_ollama_with_retry(
                prompt, map_default
            )
            batch_summaries.append(result)

        # REDUCE フェーズ
        prompt = self._build_reduce_prompt(
            symbol, base, quote, target_date,
            batch_summaries, len(news_items),
        )
        raw = self._call_ollama_with_retry(
            prompt, self._default_news_result_raw()
        )
        return self._build_final_result(raw)

    # ── バッチ分割・記事フォーマット ──

    @staticmethod
    def _split_into_batches(
        items: list[NewsItem],
        batch_size: int,
    ) -> list[list[NewsItem]]:
        """記事リストをバッチに分割

        時系列順にソートしてからバッチ分割する。

        Args:
            items: ニュースアイテムリスト
            batch_size: バッチサイズ

        Returns:
            list[list[NewsItem]]: バッチリスト
        """
        sorted_items = sorted(
            items, key=lambda i: i.published_at
        )
        return [
            sorted_items[i:i + batch_size]
            for i in range(0, len(sorted_items), batch_size)
        ]

    def _format_articles_for_batch(
        self,
        items: list[NewsItem],
        content_max: int = _FX_CONTENT_MAX,
    ) -> str:
        """記事バッチをプロンプト用テキストに変換

        FX専門ソースは本文抜粋付き、一般ソースは見出し+snippet。

        Args:
            items: ニュースアイテムリスト
            content_max: 本文抜粋の最大文字数

        Returns:
            str: フォーマット済みテキスト
        """
        lines = []
        for item in sorted(
            items, key=lambda i: i.published_at
        ):
            line = f"- {item.source_name} | {item.title}"
            if item.source_name in FX_RSS_SOURCES:
                useful = self._get_useful_text(
                    item, content_max
                )
            else:
                # 一般ソースはsnippetのみ
                useful = self._get_useful_text(
                    item, _FX_CONTENT_REDUCED
                )
            if useful:
                line += f"\n  {useful}"
            lines.append(line)
        return "\n".join(lines) if lines else "（なし）"

    # ── プロンプト構築 ──

    def _build_single_prompt(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        articles_text: str,
        article_count: int,
    ) -> str:
        """少記事日用の単一プロンプトを構築

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 対象日
            articles_text: フォーマット済み記事テキスト
            article_count: 記事数

        Returns:
            str: プロンプト文字列
        """
        y = target_date.year
        m = target_date.month
        d = target_date.day
        return f"""{symbol}のニュース分析を行ってください。

対象: {symbol} ({base}/{quote})
日付: {y}年{m}月{d}日
記事数: {article_count}件

{articles_text}

分析指示:
1. {base}と{quote}に関するニュースの方向性を区別
2. 金融政策に関する言及（利上げ/利下げ観測等）を重視
3. 地政学リスク要因の有無と影響度を評価
4. リスク選好/回避の傾向を判断
5. センチメントの一貫性に基づき確信度を設定

以下のJSONを返してください:
{{"sentiment_score": 0.0, "sentiment_confidence": 0.0, "macro_bias_score": 0.0, "policy_divergence_score": 0.0, "risk_appetite_score": 0.0, "geopolitical_risk_level": 0, "dominant_theme": "", "summary": ""}}

sentiment_score: {symbol}の総合センチメント。-1.0=弱気、+1.0=強気
sentiment_confidence: 確信度。0.0=低、1.0=高
macro_bias_score: マクロ経済バイアス。-1.0~+1.0
policy_divergence_score: 金融政策乖離。+は{base}引締め優位。-1.0~+1.0
risk_appetite_score: リスク選好度。+はリスクオン。-1.0~+1.0
geopolitical_risk_level: 地政学リスク。0=なし、1=低、2=中、3=高
dominant_theme: 支配的テーマ。日本語100文字以内
summary: 分析要約。日本語200文字以内"""

    def _build_map_prompt(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        batch_text: str,
        batch_num: int,
        total_batches: int,
    ) -> str:
        """Mapフェーズのプロンプトを構築

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 対象日
            batch_text: バッチ内記事テキスト
            batch_num: バッチ番号（1-based）
            total_batches: 全バッチ数

        Returns:
            str: プロンプト文字列
        """
        y = target_date.year
        m = target_date.month
        d = target_date.day
        return f"""{symbol}ニュースバッチ分析（{batch_num}/{total_batches}）

対象: {symbol} ({base}/{quote})
日付: {y}年{m}月{d}日

ニュース:
{batch_text}

以下のJSONで要約してください:
{{"sentiment_score": 0.0, "macro_bias_score": 0.0, "policy_divergence_score": 0.0, "risk_appetite_score": 0.0, "geopolitical_risk_level": 0, "key_themes": "", "summary": ""}}

sentiment_score: {symbol}センチメント。-1.0=弱気、+1.0=強気
macro_bias_score: マクロ経済バイアス。-1.0~+1.0
policy_divergence_score: 金融政策乖離。+は{base}引締め優位。-1.0~+1.0
risk_appetite_score: リスク選好度。+はリスクオン。-1.0~+1.0
geopolitical_risk_level: 地政学リスク。0=なし~3=高
key_themes: 主要テーマ。日本語50文字以内
summary: 要約。日本語100文字以内"""

    def _build_reduce_prompt(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        batch_summaries: list[dict],
        total_articles: int,
    ) -> str:
        """Reduceフェーズのプロンプトを構築

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 対象日
            batch_summaries: Map結果のリスト
            total_articles: 元記事の総数

        Returns:
            str: プロンプト文字列
        """
        y = target_date.year
        m = target_date.month
        d = target_date.day

        # バッチ要約をテキスト化
        summaries_block = ""
        for i, s in enumerate(batch_summaries, 1):
            sent = s.get("sentiment_score", 0.0)
            macro = s.get("macro_bias_score", 0.0)
            policy = s.get("policy_divergence_score", 0.0)
            risk = s.get("risk_appetite_score", 0.0)
            geo = s.get("geopolitical_risk_level", 0)
            themes = s.get("key_themes", "")
            summary = s.get("summary", "")
            summaries_block += (
                f"\nグループ{i}:\n"
                f"  センチメント: {sent}, "
                f"マクロ: {macro}, "
                f"政策: {policy}, "
                f"リスク選好: {risk}, "
                f"地政学: {geo}\n"
                f"  テーマ: {themes}\n"
                f"  要約: {summary}\n"
            )

        return f"""{symbol}ニュース総合分析

対象: {symbol} ({base}/{quote})
日付: {y}年{m}月{d}日
記事総数: {total_articles}件

以下は{len(batch_summaries)}グループの分析結果です:
{summaries_block}
これらを統合して以下のJSONで回答してください:
{{"sentiment_score": 0.0, "sentiment_confidence": 0.0, "macro_bias_score": 0.0, "policy_divergence_score": 0.0, "risk_appetite_score": 0.0, "geopolitical_risk_level": 0, "dominant_theme": "", "summary": ""}}

sentiment_score: {symbol}の総合センチメント。-1.0=弱気、+1.0=強気
sentiment_confidence: 確信度。グループ間の一致度を考慮。0.0=低、1.0=高
macro_bias_score: マクロ経済バイアス。-1.0~+1.0
policy_divergence_score: 金融政策乖離。+は{base}引締め優位。-1.0~+1.0
risk_appetite_score: リスク選好度。+はリスクオン。-1.0~+1.0
geopolitical_risk_level: 地政学リスク。0=なし、1=低、2=中、3=高
dominant_theme: 支配的テーマ。日本語100文字以内
summary: 分析要約。日本語200文字以内"""

    # ── 結果構築 ──

    def _build_final_result(self, data: dict) -> dict:
        """LLMレスポンスからニュース結果dictを構築

        Args:
            data: LLMレスポンスdict

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
            "session_detail": "{}",
        }

    # ── ユーティリティ ──

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

    @staticmethod
    def _get_useful_text(
        item: NewsItem,
        max_len: int,
    ) -> str | None:
        """記事から有用なテキストを取得

        フォールバック階層:
        1. content（50文字以上の場合のみ）
        2. snippet
        3. None（見出しのみ）

        Args:
            item: ニュースアイテム
            max_len: 最大文字数

        Returns:
            str | None: 有用テキスト（なければNone）
        """
        # content が有効な長さなら使用
        if (
            item.content
            and len(item.content.strip())
            >= _MIN_USEFUL_CONTENT_LEN
        ):
            text = item.content[:max_len].replace("\n", " ")
            return f"{text}..."

        # snippet にフォールバック
        if item.snippet and item.snippet.strip():
            text = item.snippet[:max_len].replace("\n", " ")
            return f"[snippet] {text}"

        return None

    # ── デフォルト値 ──

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
            "session_detail": "{}",
        }

    @staticmethod
    def _default_news_result_raw() -> dict:
        """LLMリトライ全失敗時の生デフォルト値

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
        }

    @staticmethod
    def _default_map_result() -> dict:
        """Mapフェーズ失敗時のデフォルト結果

        Returns:
            dict: デフォルト値辞書
        """
        return {
            "sentiment_score": 0.0,
            "macro_bias_score": 0.0,
            "policy_divergence_score": 0.0,
            "risk_appetite_score": 0.0,
            "geopolitical_risk_level": 0,
            "key_themes": "",
            "summary": "分析失敗",
        }
