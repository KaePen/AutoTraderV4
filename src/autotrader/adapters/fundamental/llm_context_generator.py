"""LLMによるファンダメンタルコンテキスト事前生成

Ollamaを使って月次バッチで経済指標データを分析し、
バックテスト用のマクロバイアスCSVを事前生成する。

生成ファイル: data/fundamental/llm_context_SYMBOL_YYYY.csv
"""

from __future__ import annotations

import csv
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import ollama as _ollama_module
except ImportError:
    _ollama_module = None  # type: ignore[assignment]

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings

# LLMコンテキストCSVカラム定義
_LLM_CSV_COLUMNS = [
    "period_start",
    "macro_bias_score",
    "macro_bias_summary",
    "post_event_bias_score",
    "post_event_summary",
    "sentiment_score",
]

# シンボル→通貨ペア（基軸・決済）
_SYMBOL_CURRENCIES: dict[str, tuple[str, str]] = {
    "USDJPY": ("USD", "JPY"),
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "AUDUSD": ("AUD", "USD"),
    "NZDUSD": ("NZD", "USD"),
    "USDCHF": ("USD", "CHF"),
    "USDCAD": ("USD", "CAD"),
    "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
    "AUDJPY": ("AUD", "JPY"),
    "CADJPY": ("CAD", "JPY"),
    "CHFJPY": ("CHF", "JPY"),
    "EURGBP": ("EUR", "GBP"),
    "GBPCHF": ("GBP", "CHF"),
}

# インパクト表記
_IMPACT_LABELS: dict[ImpactLevel, str] = {
    ImpactLevel.HIGH: "高インパクト",
    ImpactLevel.MEDIUM: "中インパクト",
    ImpactLevel.LOW: "低インパクト",
}


class LLMContextGenerator:
    """LLMによる月次バッチファンダメンタルコンテキスト生成器

    経済指標CSVを読み込み、月ごとにLLM分析を実行して
    バックテスト用コンテキストCSVを生成する。

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
    ) -> None:
        """初期化

        Args:
            ollama_settings: Ollama接続設定
            retry_delay_seconds: リトライ待機秒数
            max_retries: LLM呼び出し最大リトライ回数
        """
        self._settings = ollama_settings or OllamaSettings()
        self._retry_delay = retry_delay_seconds
        self._max_retries = max_retries

    def generate_for_symbol_year(
        self,
        symbol: str,
        year: int,
        events: list[EconomicEvent],
        output_dir: str | Path = "data/fundamental",
        overwrite: bool = False,
    ) -> Path:
        """指定シンボル・年のLLMコンテキストCSVを生成

        Args:
            symbol: 対象シンボル（例: USDJPY）
            year: 対象年
            events: 全経済イベントリスト
            output_dir: 出力ディレクトリ
            overwrite: 既存ファイルを上書きするか

        Returns:
            Path: 生成したCSVファイルのパス
        """
        output_path = Path(output_dir) / f"llm_context_{symbol}_{year}.csv"

        if output_path.exists() and not overwrite:
            logger.info(
                f"[LLMContext] スキップ（既存）: {output_path}"
            )
            return output_path

        currencies = _SYMBOL_CURRENCIES.get(symbol)
        if not currencies:
            raise ValueError(
                f"未対応シンボル: {symbol}. "
                f"対応: {list(_SYMBOL_CURRENCIES.keys())}"
            )
        base_currency, quote_currency = currencies

        # シンボル関連通貨のイベントのみ抽出
        relevant_events = [
            ev for ev in events
            if ev.currency in currencies
        ]
        logger.info(
            f"[LLMContext] {symbol}/{year}: "
            f"全{len(events)}件→{len(relevant_events)}件"
        )

        # 月ごとにグループ化
        monthly_events = self._group_by_month(relevant_events, year)

        # 月次LLM分析実行
        output_dir_path = Path(output_dir)
        output_dir_path.mkdir(parents=True, exist_ok=True)

        rows: list[dict] = []
        for month in range(1, 13):
            month_events = monthly_events.get(month, [])
            period_start = datetime(
                year, month, 1, tzinfo=timezone.utc
            )
            logger.info(
                f"[LLMContext] {symbol} {year}-{month:02d}: "
                f"{len(month_events)}件を分析中..."
            )

            result = self._analyze_month(
                symbol=symbol,
                base_currency=base_currency,
                quote_currency=quote_currency,
                year=year,
                month=month,
                events=month_events,
            )
            rows.append({
                "period_start": period_start.isoformat(),
                **result,
            })

        # CSV書き込み
        with open(
            output_path, "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=_LLM_CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        logger.info(
            f"[LLMContext] 完了: {output_path} ({len(rows)}ヶ月)"
        )
        return output_path

    def _group_by_month(
        self,
        events: list[EconomicEvent],
        year: int,
    ) -> dict[int, list[EconomicEvent]]:
        """イベントを月ごとにグループ化

        Args:
            events: 経済イベントリスト
            year: 対象年

        Returns:
            dict[int, list[EconomicEvent]]: 月→イベントリスト
        """
        result: dict[int, list[EconomicEvent]] = defaultdict(list)
        for ev in events:
            ev_year = ev.event_time.year
            if ev_year != year:
                continue
            result[ev.event_time.month].append(ev)
        return dict(result)

    def _analyze_month(
        self,
        symbol: str,
        base_currency: str,
        quote_currency: str,
        year: int,
        month: int,
        events: list[EconomicEvent],
    ) -> dict[str, float | str]:
        """1ヶ月分のイベントをLLMで分析

        Args:
            symbol: 対象シンボル
            base_currency: 基軸通貨
            quote_currency: 決済通貨
            year: 年
            month: 月
            events: そのシンボル関連の月次イベントリスト

        Returns:
            dict: {macro_bias_score, macro_bias_summary,
                   post_event_bias_score, post_event_summary,
                   sentiment_score}
        """
        if not events:
            return {
                "macro_bias_score": 0.0,
                "macro_bias_summary": "データなし（イベント0件）",
                "post_event_bias_score": 0.0,
                "post_event_summary": "データなし",
                "sentiment_score": 0.0,
            }

        # イベントを時系列順にソート
        sorted_events = sorted(events, key=lambda e: e.event_time)

        # 発表済みイベントのみ抽出（予実乖離分析のため）
        released = [
            ev for ev in sorted_events if ev.is_released
        ]

        # 直近の高インパクト指標（月末3件）
        high_impact = [
            ev for ev in sorted_events
            if ev.impact == ImpactLevel.HIGH and ev.is_released
        ]
        recent_high = high_impact[-3:] if high_impact else []

        prompt = self._build_prompt(
            symbol=symbol,
            base_currency=base_currency,
            quote_currency=quote_currency,
            year=year,
            month=month,
            released_events=released,
            recent_high_impact=recent_high,
        )

        for attempt in range(self._max_retries):
            try:
                result = self._call_ollama(prompt)
                return result
            except Exception as e:
                if attempt < self._max_retries - 1:
                    logger.warning(
                        f"[LLMContext] リトライ {attempt + 1}"
                        f"/{self._max_retries}: {e}"
                    )
                    time.sleep(self._retry_delay)
                else:
                    logger.error(
                        f"[LLMContext] LLM呼び出し失敗: {e}"
                    )
                    return {
                        "macro_bias_score": 0.0,
                        "macro_bias_summary": f"分析失敗: {e}",
                        "post_event_bias_score": 0.0,
                        "post_event_summary": "分析失敗",
                        "sentiment_score": 0.0,
                    }
        # 到達しない
        return {
            "macro_bias_score": 0.0,
            "macro_bias_summary": "未知エラー",
            "post_event_bias_score": 0.0,
            "post_event_summary": "未知エラー",
            "sentiment_score": 0.0,
        }

    def _build_prompt(
        self,
        symbol: str,
        base_currency: str,
        quote_currency: str,
        year: int,
        month: int,
        released_events: list[EconomicEvent],
        recent_high_impact: list[EconomicEvent],
    ) -> str:
        """LLM分析プロンプトを構築

        Args:
            symbol: 対象シンボル
            base_currency: 基軸通貨
            quote_currency: 決済通貨
            year: 年
            month: 月
            released_events: 発表済みイベントリスト
            recent_high_impact: 直近高インパクト指標

        Returns:
            str: LLMプロンプト
        """
        events_text = self._format_events(released_events)
        recent_text = self._format_events(recent_high_impact)

        return f"""あなたはFXトレードのファンダメンタルアナリストです。
以下の経済指標データに基づいて、{symbol}のマクロバイアスを分析してください。

## 分析対象
- シンボル: {symbol} ({base_currency}/{quote_currency})
- 期間: {year}年{month}月

## 発表済み経済指標（{len(released_events)}件）
{events_text}

## 直近の高インパクト指標（最大3件）
{recent_text}

## 分析指示
1. 各指標の実績vs予測の乖離（サプライズ）を評価
2. {base_currency}と{quote_currency}の相対的な強弱を判断
3. 月全体のマクロトレンドを総合評価

## 出力形式（JSONのみで回答）
{{
  "macro_bias_score": <-1.0から+1.0（+は{symbol}強気/{base_currency}高、-は{symbol}弱気/{base_currency}安）>,
  "macro_bias_summary": "<マクロバイアスの説明（日本語、50文字以内）>",
  "post_event_bias_score": <-1.0から+1.0（直近の高インパクト指標後の短期バイアス）>,
  "post_event_summary": "<短期バイアスの説明（日本語、50文字以内）>",
  "sentiment_score": <-1.0から+1.0（市場全体のセンチメント）>
}}"""

    def _format_events(
        self, events: list[EconomicEvent]
    ) -> str:
        """イベントリストをプロンプト用テキストに変換

        Args:
            events: 経済イベントリスト

        Returns:
            str: フォーマット済みテキスト
        """
        if not events:
            return "（なし）"

        lines = []
        for ev in events:
            impact_label = _IMPACT_LABELS.get(
                ev.impact, ev.impact.value
            )
            date_str = ev.event_time.strftime("%m/%d %H:%M")
            actual = (
                f"{ev.actual:.2f}" if ev.actual is not None
                else "未発表"
            )
            forecast = (
                f"{ev.forecast:.2f}" if ev.forecast is not None
                else "予測なし"
            )
            surprise = ev.surprise_magnitude
            surprise_str = (
                f"{surprise:+.1%}" if surprise is not None
                else ""
            )
            lines.append(
                f"- {date_str} [{impact_label}] "
                f"{ev.currency} {ev.event_name}: "
                f"実績={actual} 予測={forecast} "
                f"{surprise_str}"
            )
        return "\n".join(lines)

    def _call_ollama(
        self, prompt: str
    ) -> dict[str, float | str]:
        """OllamaでLLM推論を実行

        Args:
            prompt: プロンプト文字列

        Returns:
            dict: {macro_bias_score, macro_bias_summary,
                   post_event_bias_score, post_event_summary,
                   sentiment_score}

        Raises:
            ValueError: JSON解析失敗時
            RuntimeError: Ollama接続失敗時
        """
        if _ollama_module is None:
            raise RuntimeError(
                "ollama パッケージが必要です: "
                "pip install ollama"
            )
        client = _ollama_module.Client(host=self._settings.host)
        response = client.chat(
            model=self._settings.model,
            messages=[{"role": "user", "content": prompt}],
            format="json",
            options={
                "temperature": self._settings.temperature,
                "num_ctx": self._settings.num_ctx,
                "num_gpu": self._settings.num_gpu,
                "num_thread": self._settings.num_thread,
            },
        )

        # ChatResponse オブジェクト（属性アクセス）
        content = response.message.content
        return self._parse_response(content)

    def _parse_response(
        self, content: str
    ) -> dict[str, float | str]:
        """LLMレスポンスをパース

        JSONブロック抽出 → フォールバックとして正規表現を使用。

        Args:
            content: LLMのレスポンス文字列

        Returns:
            dict: パース済みスコア辞書

        Raises:
            ValueError: パース失敗時
        """
        # まず直接パースを試みる
        try:
            return self._build_result(json.loads(content))
        except json.JSONDecodeError:
            pass

        # ```json ... ``` コードブロックを抽出
        json_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            content,
            re.DOTALL,
        )
        if json_match:
            try:
                return self._build_result(
                    json.loads(json_match.group(1))
                )
            except json.JSONDecodeError:
                pass

        # { ... } の最初の出現を抽出（非greedyで最小一致）
        # ネストしたJSONは想定しないためシンプルな非greedy正規表現を使用
        brace_match = re.search(
            r"\{[^{}]*\}", content, re.DOTALL
        )
        if brace_match:
            try:
                return self._build_result(
                    json.loads(brace_match.group(0))
                )
            except json.JSONDecodeError:
                pass

        raise ValueError(
            f"JSON解析失敗\nコンテンツ: {content[:200]}"
        )

        raise ValueError(
            f"パースエラー（到達不可コード）: {content[:100]}"
        )

    def _build_result(
        self, data: dict
    ) -> dict[str, float | str]:
        """パース済みdictからスコア辞書を構築

        Args:
            data: LLMレスポンスのパース済みdict

        Returns:
            dict: スコアと要約のdictionary
        """
        def _clip(
            val: float | None, default: float = 0.0
        ) -> float:
            """値を[-1.0, 1.0]にクリップ"""
            if val is None or not isinstance(
                val, (int, float)
            ):
                return default
            return max(-1.0, min(1.0, float(val)))

        return {
            "macro_bias_score": _clip(
                data.get("macro_bias_score")
            ),
            "macro_bias_summary": str(
                data.get("macro_bias_summary", "")
            )[:200],
            "post_event_bias_score": _clip(
                data.get("post_event_bias_score")
            ),
            "post_event_summary": str(
                data.get("post_event_summary", "")
            )[:200],
            "sentiment_score": _clip(
                data.get("sentiment_score")
            ),
        }
