"""Ollamaクライアント

構造化出力対応のOllama LLMクライアント。
Veto判定・信頼度調整機能を提供。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

from autotrader.adapters.ollama.prompts import (
    SYSTEM_PROMPT_TRADING,
    VETO_WITH_FUNDAMENTAL_SECTION,
    build_confidence_adjustment_prompt,
    build_market_outlook_prompt,
    build_post_event_analysis_prompt,
    build_veto_check_prompt,
    format_mtf_summary,
)
from autotrader.adapters.ollama.schemas import (
    ConfidenceAdjustmentOutput,
    MarketOutlookOutput,
    PostEventAnalysisOutput,
    VetoCheckOutput,
)
from autotrader.core.exceptions import (
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
)

T = TypeVar("T", bound=BaseModel)


class OllamaClient:
    """Ollamaクライアント

    構造化出力（format parameter）を使用して
    Pydanticスキーマに従ったJSON出力を生成。

    Args:
        host: Ollamaホスト
        model: モデル名
        temperature: 温度パラメータ
        timeout: タイムアウト秒
        sample_rate: LLMレスポンスサンプリング率
        num_ctx: コンテキストウィンドウサイズ
        keep_alive: モデル保持時間
    """

    # LLMサンプル保存設定
    _sample_dir: Path = Path("logs/llm_samples")

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "erwan2/DeepSeek-R1-Distill-Qwen-14B",
        temperature: float = 0.1,
        timeout: float = 30.0,
        sample_rate: float = 0.1,
        num_ctx: int = 4096,
        keep_alive: str = "5m",
        num_gpu: int = -1,
        use_mmap: bool = True,
        num_thread: int | None = None,
    ) -> None:
        """初期化

        Args:
            host: Ollamaホスト
            model: モデル名
            temperature: 温度パラメータ
            timeout: タイムアウト
            sample_rate: サンプリング率（0.0-1.0）
            num_ctx: コンテキストウィンドウサイズ
            keep_alive: モデル保持時間
            num_gpu: GPUに割り当てるレイヤー数（-1=全GPU使用、0=CPU only）
            use_mmap: メモリマッピング使用
            num_thread: CPUスレッド数（None=自動）
        """
        self._host = host
        self._model = model
        self._temperature = temperature
        self._timeout = timeout
        self._sample_rate = sample_rate
        self._num_ctx = num_ctx
        self._keep_alive = keep_alive
        self._num_gpu = num_gpu
        self._use_mmap = use_mmap
        self._num_thread = num_thread

        self._client = None
        self._async_client = None

    def _get_client(self):
        """同期クライアントを取得"""
        if self._client is None:
            try:
                from ollama import Client
                self._client = Client(
                    host=self._host, timeout=self._timeout
                )
            except ImportError as e:
                raise LLMConnectionError(
                    "ollama パッケージがインストールされていません"
                ) from e
        return self._client

    def _get_async_client(self):
        """非同期クライアントを取得"""
        if self._async_client is None:
            try:
                from ollama import AsyncClient
                self._async_client = AsyncClient(
                    host=self._host, timeout=self._timeout
                )
            except ImportError as e:
                raise LLMConnectionError(
                    "ollama パッケージがインストールされていません"
                ) from e
        return self._async_client

    def _get_options(self) -> dict:
        """Ollama APIオプションを取得

        GPU使用設定を含むオプション辞書を返す。

        Returns:
            dict: Ollamaオプション
        """
        options = {
            "temperature": self._temperature,
            "num_ctx": self._num_ctx,
            "num_gpu": self._num_gpu,
            "use_mmap": self._use_mmap,
        }
        if self._num_thread is not None:
            options["num_thread"] = self._num_thread
        return options

    def _save_llm_sample(
        self,
        content: str,
        schema_name: str,
        force: bool = False,
    ) -> None:
        """LLMレスポンスをサンプリング保存

        Args:
            content: レスポンス内容
            schema_name: スキーマ名
            force: 強制保存フラグ
        """
        if not force and random.random() > self._sample_rate:
            return

        try:
            self._sample_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{schema_name}.json"
            filepath = self._sample_dir / filename

            sample_data = {
                "timestamp": timestamp,
                "schema": schema_name,
                "model": self._model,
                "raw_content": content,
            }

            try:
                sample_data["parsed"] = json.loads(content)
            except json.JSONDecodeError:
                sample_data["parsed"] = None

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(sample_data, f, ensure_ascii=False, indent=2)

        except Exception:
            pass

    def _clean_json_response(self, content: str) -> str:
        """JSON応答をクリーニング

        Args:
            content: LLM応答

        Returns:
            str: クリーニング済みJSON
        """
        content = content.strip()

        # Markdownコードブロック除去
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        # BOM除去
        if content.startswith("\ufeff"):
            content = content[1:]

        # JSONオブジェクトの開始位置を検出
        json_start = content.find("{")
        if json_start > 0:
            content = content[json_start:]

        # JSONオブジェクトの終了位置を検出
        if not content.startswith("{"):
            start = content.find("{")
            end = content.rfind("}") + 1
            if start != -1 and end > start:
                content = content[start:end]

        return content

    def _parse_response(
        self,
        response,
        schema: type[T],
    ) -> T:
        """レスポンスをパースしてスキーマに変換

        Args:
            response: Ollamaレスポンス
            schema: 出力スキーマ

        Returns:
            T: パース済みオブジェクト

        Raises:
            LLMResponseError: パースエラー
        """
        content = ""
        data = None
        try:
            content = response.message.content

            # サンプリング保存
            self._save_llm_sample(content, schema.__name__)

            # JSON応答のクリーニング
            content = self._clean_json_response(content)

            # JSONをパース
            data = json.loads(content)
            return schema.model_validate(data)

        except json.JSONDecodeError as e:
            self._save_llm_sample(
                content, f"{schema.__name__}_JSON_ERROR", force=True
            )
            raise LLMResponseError(f"JSONパースエラー: {e}") from e
        except ValidationError as e:
            self._save_llm_sample(
                content, f"{schema.__name__}_VALIDATION_ERROR", force=True
            )
            raise LLMResponseError(f"スキーマ検証エラー: {e}") from e

    def check_veto(
        self,
        symbol: str,
        timestamp: str,
        current_price: float,
        direction: str,
        confidence: float,
        rsi: float,
        macd: float,
        adx: float,
        trend: str,
        mtf_data: dict | None,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        fundamental_context=None,
    ) -> VetoCheckOutput:
        """Veto判定を実行（同期）

        Args:
            symbol: 通貨ペア
            timestamp: タイムスタンプ
            current_price: 現在価格
            direction: シグナル方向
            confidence: シグナル信頼度
            rsi: RSI値
            macd: MACD値
            adx: ADX値
            trend: トレンド方向
            mtf_data: MTF分析データ
            entry_price: エントリー価格
            stop_loss: ストップロス
            take_profit: テイクプロフィット
            fundamental_context: ファンダメンタルコンテキスト（None=使用しない）

        Returns:
            VetoCheckOutput: Veto判定結果
        """
        prompt = build_veto_check_prompt(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            direction=direction,
            confidence=confidence,
            rsi=rsi,
            macd=macd,
            adx=adx,
            trend=trend,
            mtf_summary=format_mtf_summary(mtf_data or {}),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        # ファンダメンタルコンテキストをプロンプトに追加
        if fundamental_context is not None:
            prompt += VETO_WITH_FUNDAMENTAL_SECTION.format(
                fundamental_section=(
                    fundamental_context.to_prompt_section()
                )
            )

        logger.info(
            f"[LLM] Veto判定開始: {symbol} {direction} "
            f"conf={confidence:.1%} @ {current_price:.3f}"
        )
        start_time = time.perf_counter()

        try:
            client = self._get_client()
            response = client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_TRADING},
                    {"role": "user", "content": prompt},
                ],
                format=VetoCheckOutput.model_json_schema(),
                options=self._get_options(),
                keep_alive=self._keep_alive,
            )
            result = self._parse_response(response, VetoCheckOutput)

            elapsed = time.perf_counter() - start_time
            veto_str = "VETO" if result.veto else "OK"
            logger.info(
                f"[LLM] Veto判定完了: {veto_str} "
                f"(conf={result.confidence:.1%}) [{elapsed:.2f}s]"
            )
            return result

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"[LLM] Veto判定失敗: {e} [{elapsed:.2f}s]")
            if "connection" in str(e).lower():
                raise LLMConnectionError(f"Ollama接続エラー: {e}") from e
            raise LLMResponseError(f"LLMエラー: {e}") from e

    async def check_veto_async(
        self,
        symbol: str,
        timestamp: str,
        current_price: float,
        direction: str,
        confidence: float,
        rsi: float,
        macd: float,
        adx: float,
        trend: str,
        mtf_data: dict | None,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        fundamental_context=None,
    ) -> VetoCheckOutput:
        """Veto判定を実行（非同期）

        Args:
            symbol: 通貨ペア
            timestamp: タイムスタンプ
            current_price: 現在価格
            direction: シグナル方向
            confidence: シグナル信頼度
            rsi: RSI値
            macd: MACD値
            adx: ADX値
            trend: トレンド方向
            mtf_data: MTF分析データ
            entry_price: エントリー価格
            stop_loss: ストップロス
            take_profit: テイクプロフィット
            fundamental_context: ファンダメンタルコンテキスト

        Returns:
            VetoCheckOutput: Veto判定結果
        """
        prompt = build_veto_check_prompt(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            direction=direction,
            confidence=confidence,
            rsi=rsi,
            macd=macd,
            adx=adx,
            trend=trend,
            mtf_summary=format_mtf_summary(mtf_data or {}),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if fundamental_context is not None:
            prompt += VETO_WITH_FUNDAMENTAL_SECTION.format(
                fundamental_section=(
                    fundamental_context.to_prompt_section()
                )
            )

        try:
            client = self._get_async_client()
            response = await asyncio.wait_for(
                client.chat(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT_TRADING},
                        {"role": "user", "content": prompt},
                    ],
                    format=VetoCheckOutput.model_json_schema(),
                    options={
                        "temperature": self._temperature,
                        "num_ctx": self._num_ctx,
                    },
                    keep_alive=self._keep_alive,
                ),
                timeout=self._timeout,
            )
            return self._parse_response(response, VetoCheckOutput)

        except asyncio.TimeoutError as e:
            raise LLMTimeoutError(f"Veto判定タイムアウト: {e}") from e
        except (ConnectionError, OSError) as e:
            raise LLMConnectionError(f"Veto判定接続エラー: {e}") from e
        except Exception as e:
            raise LLMResponseError(f"Veto判定エラー: {e}") from e

    def adjust_confidence(
        self,
        symbol: str,
        timestamp: str,
        current_price: float,
        direction: str,
        confidence: float,
        rsi: float,
        macd: float,
        adx: float,
        mtf_data: dict | None,
        atr: float,
        atr_ratio: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> ConfidenceAdjustmentOutput:
        """信頼度調整を実行（同期）

        Args:
            symbol: 通貨ペア
            timestamp: タイムスタンプ
            current_price: 現在価格
            direction: シグナル方向
            confidence: 現在の信頼度
            rsi: RSI値
            macd: MACD値
            adx: ADX値
            mtf_data: MTF分析データ
            atr: ATR値
            atr_ratio: ATR比率
            entry_price: エントリー価格
            stop_loss: ストップロス
            take_profit: テイクプロフィット

        Returns:
            ConfidenceAdjustmentOutput: 信頼度調整結果
        """
        prompt = build_confidence_adjustment_prompt(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            direction=direction,
            confidence=confidence,
            rsi=rsi,
            macd=macd,
            adx=adx,
            mtf_summary=format_mtf_summary(mtf_data or {}),
            atr=atr,
            atr_ratio=atr_ratio,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        logger.info(
            f"[LLM] 信頼度調整開始: {symbol} {direction} "
            f"conf={confidence:.1%} @ {current_price:.3f}"
        )
        start_time = time.perf_counter()

        try:
            client = self._get_client()
            response = client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_TRADING},
                    {"role": "user", "content": prompt},
                ],
                format=ConfidenceAdjustmentOutput.model_json_schema(),
                options=self._get_options(),
                keep_alive=self._keep_alive,
            )
            result = self._parse_response(
                response, ConfidenceAdjustmentOutput
            )

            elapsed = time.perf_counter() - start_time
            delta = result.adjusted_confidence - confidence
            logger.info(
                f"[LLM] 信頼度調整完了: {confidence:.1%} → "
                f"{result.adjusted_confidence:.1%} ({delta:+.1%}) [{elapsed:.2f}s]"
            )
            return result

        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"[LLM] 信頼度調整失敗: {e} [{elapsed:.2f}s]")
            if "connection" in str(e).lower():
                raise LLMConnectionError(f"Ollama接続エラー: {e}") from e
            raise LLMResponseError(f"LLMエラー: {e}") from e

    async def adjust_confidence_async(
        self,
        symbol: str,
        timestamp: str,
        current_price: float,
        direction: str,
        confidence: float,
        rsi: float,
        macd: float,
        adx: float,
        mtf_data: dict | None,
        atr: float,
        atr_ratio: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
    ) -> ConfidenceAdjustmentOutput:
        """信頼度調整を実行（非同期）

        Args:
            symbol: 通貨ペア
            timestamp: タイムスタンプ
            current_price: 現在価格
            direction: シグナル方向
            confidence: 現在の信頼度
            rsi: RSI値
            macd: MACD値
            adx: ADX値
            mtf_data: MTF分析データ
            atr: ATR値
            atr_ratio: ATR比率
            entry_price: エントリー価格
            stop_loss: ストップロス
            take_profit: テイクプロフィット

        Returns:
            ConfidenceAdjustmentOutput: 信頼度調整結果
        """
        prompt = build_confidence_adjustment_prompt(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            direction=direction,
            confidence=confidence,
            rsi=rsi,
            macd=macd,
            adx=adx,
            mtf_summary=format_mtf_summary(mtf_data or {}),
            atr=atr,
            atr_ratio=atr_ratio,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

        try:
            client = self._get_async_client()
            response = await asyncio.wait_for(
                client.chat(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT_TRADING},
                        {"role": "user", "content": prompt},
                    ],
                    format=ConfidenceAdjustmentOutput.model_json_schema(),
                    options={
                        "temperature": self._temperature,
                        "num_ctx": self._num_ctx,
                    },
                    keep_alive=self._keep_alive,
                ),
                timeout=self._timeout,
            )
            return self._parse_response(
                response, ConfidenceAdjustmentOutput
            )

        except asyncio.TimeoutError as e:
            raise LLMTimeoutError(f"信頼度調整タイムアウト: {e}") from e
        except (ConnectionError, OSError) as e:
            raise LLMConnectionError(f"信頼度調整接続エラー: {e}") from e
        except Exception as e:
            raise LLMResponseError(f"信頼度調整エラー: {e}") from e

    async def analyze_market_outlook_async(
        self,
        symbol: str,
        timestamp: str,
        current_price: float,
        upcoming_events: list[dict],
        technical_summary: str = "",
        valid_days: int = 7,
    ) -> MarketOutlookOutput:
        """市場観を非同期分析（毎朝の更新用）

        Args:
            symbol: 通貨ペア
            timestamp: タイムスタンプ
            current_price: 現在価格
            upcoming_events: 直近イベントリスト
            technical_summary: テクニカルサマリー
            valid_days: 有効日数

        Returns:
            MarketOutlookOutput: 市場観分析結果
        """
        prompt = build_market_outlook_prompt(
            symbol=symbol,
            timestamp=timestamp,
            current_price=current_price,
            upcoming_events=upcoming_events,
            technical_summary=technical_summary,
            valid_days=valid_days,
        )

        try:
            client = self._get_async_client()
            response = await asyncio.wait_for(
                client.chat(
                    model=self._model,
                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT_TRADING,
                        },
                        {"role": "user", "content": prompt},
                    ],
                    format=MarketOutlookOutput.model_json_schema(),
                    options=self._get_options(),
                    keep_alive=self._keep_alive,
                ),
                timeout=self._timeout,
            )
            result = self._parse_response(
                response, MarketOutlookOutput
            )
            logger.info(
                f"[LLM] 市場観分析完了: {symbol} "
                f"score={result.direction_score:+.2f} "
                f"conf={result.confidence:.1%}"
            )
            return result

        except asyncio.TimeoutError as e:
            raise LLMTimeoutError(f"市場観分析タイムアウト: {e}") from e
        except (ConnectionError, OSError) as e:
            raise LLMConnectionError(
                f"市場観分析接続エラー: {e}"
            ) from e
        except Exception as e:
            raise LLMResponseError(f"市場観分析エラー: {e}") from e

    async def analyze_post_event_async(
        self,
        symbol: str,
        timestamp: str,
        event_name: str,
        currency: str,
        actual: float | None,
        forecast: float | None,
        previous: float | None,
        current_price: float,
        price_change: float = 0.0,
    ) -> PostEventAnalysisOutput:
        """指標後バイアスを非同期分析

        Args:
            symbol: 通貨ペア
            timestamp: タイムスタンプ
            event_name: イベント名
            currency: 通貨コード
            actual: 実績値
            forecast: 予測値
            previous: 前回値
            current_price: 現在価格
            price_change: 指標発表後の価格変化率

        Returns:
            PostEventAnalysisOutput: 指標後バイアス分析結果
        """
        prompt = build_post_event_analysis_prompt(
            symbol=symbol,
            timestamp=timestamp,
            event_name=event_name,
            currency=currency,
            actual=actual,
            forecast=forecast,
            previous=previous,
            current_price=current_price,
            price_change=price_change,
        )

        try:
            client = self._get_async_client()
            response = await asyncio.wait_for(
                client.chat(
                    model=self._model,
                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT_TRADING,
                        },
                        {"role": "user", "content": prompt},
                    ],
                    format=PostEventAnalysisOutput.model_json_schema(),
                    options=self._get_options(),
                    keep_alive=self._keep_alive,
                ),
                timeout=self._timeout,
            )
            result = self._parse_response(
                response, PostEventAnalysisOutput
            )
            logger.info(
                f"[LLM] 指標後分析完了: {event_name} "
                f"dir={result.surprise_direction} "
                f"score={result.bias_score:+.2f}"
            )
            return result

        except asyncio.TimeoutError as e:
            raise LLMTimeoutError(
                f"指標後分析タイムアウト: {e}"
            ) from e
        except (ConnectionError, OSError) as e:
            raise LLMConnectionError(
                f"指標後分析接続エラー: {e}"
            ) from e
        except Exception as e:
            raise LLMResponseError(f"指標後分析エラー: {e}") from e

    async def health_check(self) -> bool:
        """ヘルスチェック

        Returns:
            bool: 接続可能ならTrue
        """
        try:
            client = self._get_async_client()
            await asyncio.wait_for(
                client.chat(
                    model=self._model,
                    messages=[{"role": "user", "content": "ping"}],
                    options={"num_predict": 1},
                    keep_alive=self._keep_alive,
                ),
                timeout=10.0,
            )
            return True
        except Exception:
            return False
