"""LLMジェネレーター共通基底クラス

Ollama接続・リトライ・JSONパース・CSV書き込みの
共通ロジックを提供する。

LLMEventGenerator / LLMNewsGenerator が継承して使用する。
"""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

try:
    import ollama as _ollama_module
except ImportError:
    _ollama_module = None  # type: ignore[assignment]

import logging

logger = logging.getLogger(__name__)

from autotrader.config.llm_settings import OllamaSettings
from autotrader.core.exceptions import (
    ConfigurationError,
    LLMResponseError,
    ValidationError,
)

# シンボル→通貨ペア（基軸・決済）の正規マッピング
SYMBOL_CURRENCIES: dict[str, tuple[str, str]] = {
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


class LLMGeneratorBase:
    """LLMジェネレーター共通基底クラス

    Ollama接続・リトライ・JSONパース・CSV書き込みの
    共通ロジックを提供する。

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

    # システムプロンプト: JSON出力を指示
    _SYSTEM_PROMPT = (
        "あなたはFX市場の専門アナリストです。"
        "分析後、指定されたJSON形式のみで回答してください。"
        "JSONオブジェクト以外は出力しないでください。"
    )

    def _call_ollama(self, prompt: str) -> dict:
        """OllamaでLLM推論を実行しJSONをパース

        Args:
            prompt: プロンプト文字列

        Returns:
            dict: パース済みJSONレスポンス

        Raises:
            LLMResponseError: JSON解析失敗時
            ConfigurationError: Ollama未インストール時
        """
        if _ollama_module is None:
            raise ConfigurationError(
                "ollama パッケージが必要です: pip install ollama"
            )
        timeout = self._settings.timeout_seconds
        client = _ollama_module.Client(
            host=self._settings.host,
            timeout=timeout,
        )
        # R1モデルはformat="json"と<think>が競合するため使わない
        response = client.chat(
            model=self._settings.model,
            messages=[
                {
                    "role": "system",
                    "content": self._SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            options={
                "temperature": self._settings.temperature,
                "num_ctx": self._settings.num_ctx,
                "num_gpu": self._settings.num_gpu,
                "num_thread": self._settings.num_thread,
            },
        )
        content = response.message.content
        return self._parse_json_response(content)

    def _call_ollama_with_retry(
        self,
        prompt: str,
        default_result: dict,
    ) -> dict:
        """リトライ付きOllama呼び出し

        Args:
            prompt: プロンプト文字列
            default_result: 全リトライ失敗時のデフォルト

        Returns:
            dict: LLMレスポンスまたはデフォルト値
        """
        for attempt in range(self._max_retries):
            try:
                return self._call_ollama(prompt)
            except Exception as e:
                err_msg = str(e)
                # <think>截断エラーは簡潔に表示
                if "<think>" in err_msg:
                    err_short = (
                        "JSON解析失敗（<think>截断: "
                        "コンテキスト超過の可能性）"
                    )
                else:
                    err_short = err_msg[:120]
                if attempt < self._max_retries - 1:
                    logger.warning(
                        f"[LLMBase] リトライ "
                        f"{attempt + 1}"
                        f"/{self._max_retries}: "
                        f"{err_short}"
                    )
                    time.sleep(self._retry_delay)
                else:
                    logger.warning(
                        f"[LLMBase] LLM呼び出し"
                        f"{self._max_retries}回失敗"
                        f"→デフォルト値使用: "
                        f"{err_short}"
                    )
                    return default_result
        return default_result

    # R1モデルの<think>ブロック除去パターン（閉じタグあり）
    _THINK_RE = re.compile(
        r"<think>.*?</think>", re.DOTALL
    )
    # 未閉じ<think>ブロック（コンテキスト超過時）
    _THINK_OPEN_RE = re.compile(
        r"<think>.*", re.DOTALL
    )

    def _parse_json_response(self, content: str) -> dict:
        """LLMレスポンスからJSONを抽出

        <think>ブロック除去 → 直接パース →
        コードブロック → ブレース抽出 の4段階フォールバック。

        Args:
            content: LLMのレスポンス文字列

        Returns:
            dict: パース済みdict

        Raises:
            LLMResponseError: パース失敗時
        """
        # R1モデル: 閉じた<think>ブロックを除去
        cleaned = self._THINK_RE.sub("", content).strip()
        # 未閉じ<think>ブロックも除去（コンテキスト超過時）
        if "<think>" in cleaned:
            cleaned = self._THINK_OPEN_RE.sub(
                "", cleaned
            ).strip()
        if not cleaned:
            cleaned = content

        # 直接パース
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # ```json ... ``` コードブロック抽出
        json_match = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            cleaned,
            re.DOTALL,
        )
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # { ... } の最初の出現を抽出（1段ネスト対応）
        brace_match = re.search(
            r"\{(?:[^{}]|\{[^{}]*\})*\}",
            cleaned,
            re.DOTALL,
        )
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        raise LLMResponseError(
            f"JSON解析失敗\nコンテンツ: {content[:200]}"
        )

    @staticmethod
    def _clip(
        val: float | int | None,
        lo: float,
        hi: float,
        default: float = 0.0,
    ) -> float:
        """値を指定範囲にクリップ

        Args:
            val: クリップ対象値
            lo: 下限
            hi: 上限
            default: None/非数値時のデフォルト

        Returns:
            float: クリップ済み値
        """
        if val is None or not isinstance(val, (int, float)):
            return default
        return max(lo, min(hi, float(val)))

    @staticmethod
    def _clip_score(
        val: float | int | None,
        default: float = 0.0,
    ) -> float:
        """スコアを[-1.0, 1.0]にクリップ

        Args:
            val: クリップ対象値
            default: None/非数値時のデフォルト

        Returns:
            float: クリップ済み値
        """
        return LLMGeneratorBase._clip(val, -1.0, 1.0, default)

    @staticmethod
    def _write_csv(
        rows: list[dict],
        columns: list[str],
        output_path: Path,
    ) -> None:
        """行リストをCSVに書き込み

        Args:
            rows: 辞書リスト
            columns: カラム名リスト
            output_path: 出力パス
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(
            output_path, "w", encoding="utf-8", newline=""
        ) as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_existing_csv(
        output_path: Path,
        columns: list[str],
    ) -> list[dict]:
        """既存CSVから処理済み行を読み込み（resume用）

        Args:
            output_path: CSVパス
            columns: 期待するカラムリスト

        Returns:
            list[dict]: 読み込んだ行。ファイル不在や
            ヘッダー不一致時は空リスト
        """
        if not output_path.exists():
            return []
        try:
            with open(
                output_path, "r", encoding="utf-8"
            ) as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != columns:
                    return []
                return list(reader)
        except Exception:
            return []

    @staticmethod
    def _generate_date_range(year: int) -> list[date]:
        """指定年の全日リストを生成

        Args:
            year: 年

        Returns:
            list[date]: 1/1 ~ 12/31 の日付リスト
        """
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        days: list[date] = []
        current = start
        while current <= end:
            days.append(current)
            current += timedelta(days=1)
        return days

    @staticmethod
    def get_symbol_currencies(
        symbol: str,
    ) -> tuple[str, str]:
        """シンボルから基軸/決済通貨を取得

        Args:
            symbol: 対象シンボル

        Returns:
            tuple[str, str]: (base, quote)

        Raises:
            ValidationError: 未対応シンボル
        """
        currencies = SYMBOL_CURRENCIES.get(symbol)
        if not currencies:
            raise ValidationError(
                f"未対応シンボル: {symbol}. "
                f"対応: {list(SYMBOL_CURRENCIES.keys())}"
            )
        return currencies
