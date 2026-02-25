"""イベント分析LLMジェネレーター

経済指標CSVからシンボル関連イベントを日次抽出し、
LLMで短期インパクト分析を行い、
llm_events_SYMBOL_YYYY.csv に出力する。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.llm_generator_base import (
    LLMGeneratorBase,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    ImpactLevel,
)
from autotrader.config.llm_settings import OllamaSettings

# イベントCSVカラム定義
EVENT_CSV_COLUMNS = [
    "date",
    "event_count",
    "high_impact_count",
    "net_surprise_score",
    "dominant_event_name",
    "dominant_surprise_pct",
    "expected_volatility",
    "price_direction_bias",
    "convergence_hours",
    "trade_caution_level",
    "summary",
]

# インパクト表記
_IMPACT_LABELS: dict[ImpactLevel, str] = {
    ImpactLevel.HIGH: "高インパクト",
    ImpactLevel.MEDIUM: "中インパクト",
    ImpactLevel.LOW: "低インパクト",
}


class LLMEventGenerator(LLMGeneratorBase):
    """イベント分析LLMジェネレーター

    events_YYYY.csv からシンボル関連イベントを日次抽出し、
    LLM分析結果を llm_events_SYMBOL_YYYY.csv に出力する。

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
        super().__init__(
            ollama_settings=ollama_settings,
            retry_delay_seconds=retry_delay_seconds,
            max_retries=max_retries,
        )

    def generate_for_symbol_year(
        self,
        symbol: str,
        year: int,
        events: list[EconomicEvent],
        output_dir: str | Path = "data/fundamental",
        overwrite: bool = False,
    ) -> Path:
        """指定シンボル・年のイベントLLM CSVを生成

        Args:
            symbol: 対象シンボル（例: USDJPY）
            year: 対象年
            events: 全経済イベントリスト（全通貨含む）
            output_dir: 出力ディレクトリ
            overwrite: 既存ファイル上書き

        Returns:
            Path: 生成したCSVパス
        """
        output_path = (
            Path(output_dir) / f"llm_events_{symbol}_{year}.csv"
        )
        if output_path.exists() and not overwrite:
            logger.info(
                f"[EventGen] スキップ（既存）: {output_path}"
            )
            return output_path

        base, quote = self.get_symbol_currencies(symbol)

        # 対象通貨・年のイベント抽出
        relevant = self._filter_events(
            events, (base, quote), year
        )
        logger.info(
            f"[EventGen] {symbol}/{year}: "
            f"全{len(events)}件→{len(relevant)}件"
        )

        # 日付ごとにグループ化
        daily_events = self._group_by_date(relevant)

        # 全日に対してLLM分析
        date_range = self._generate_date_range(year)
        total_days = len(date_range)
        rows: list[dict] = []

        for idx, target_date in enumerate(date_range, 1):
            day_events = daily_events.get(target_date, [])
            result = self._analyze_date(
                symbol, base, quote, target_date, day_events
            )
            result["date"] = target_date.isoformat()
            result["event_count"] = len(day_events)
            result["high_impact_count"] = sum(
                1
                for ev in day_events
                if ev.impact == ImpactLevel.HIGH
            )
            rows.append(result)

            if idx % 50 == 0 or idx == total_days:
                logger.info(
                    f"[EventGen] {symbol}/{year}: "
                    f"{idx}/{total_days}日完了"
                )

        # CSV書き込み
        self._write_csv(rows, EVENT_CSV_COLUMNS, output_path)
        logger.info(
            f"[EventGen] 完了: {output_path} ({len(rows)}日)"
        )
        return output_path

    def _filter_events(
        self,
        events: list[EconomicEvent],
        currencies: tuple[str, str],
        year: int,
    ) -> list[EconomicEvent]:
        """対象シンボル・年のイベントのみ抽出

        Args:
            events: 全イベント
            currencies: (base, quote)
            year: 対象年

        Returns:
            list[EconomicEvent]: フィルタ済み
        """
        return [
            ev
            for ev in events
            if ev.currency in currencies
            and ev.event_time.year == year
        ]

    def _group_by_date(
        self,
        events: list[EconomicEvent],
    ) -> dict[date, list[EconomicEvent]]:
        """イベントを日付ごとにグループ化

        Args:
            events: フィルタ済みイベント

        Returns:
            dict[date, list[EconomicEvent]]
        """
        result: dict[date, list[EconomicEvent]] = defaultdict(
            list
        )
        for ev in events:
            result[ev.event_time.date()].append(ev)
        return dict(result)

    def _analyze_date(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        events: list[EconomicEvent],
    ) -> dict:
        """1日分のイベントをLLM分析

        イベントが0件の場合はLLM呼び出しをスキップし
        デフォルト値を返す。

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 分析対象日
            events: 当日イベント

        Returns:
            dict: 分析結果
        """
        # 発表済みイベントのみ
        released = [ev for ev in events if ev.is_released]

        if not released:
            # 未発表の高インパクト指標がある場合は注意度を設定
            high_unreleased = sum(
                1
                for ev in events
                if ev.impact == ImpactLevel.HIGH
                and not ev.is_released
            )
            result = self._default_event_result()
            if high_unreleased >= 2:
                result["trade_caution_level"] = 2
                result["summary"] = (
                    f"未発表高インパクト指標{high_unreleased}件"
                )
            elif high_unreleased == 1:
                result["trade_caution_level"] = 1
                result["summary"] = "未発表高インパクト指標1件"
            return result

        prompt = self._build_event_prompt(
            symbol, base, quote, target_date, released
        )
        raw = self._call_ollama_with_retry(
            prompt, self._default_event_result()
        )
        return self._build_event_result(raw)

    def _build_event_prompt(
        self,
        symbol: str,
        base: str,
        quote: str,
        target_date: date,
        events: list[EconomicEvent],
    ) -> str:
        """イベント分析プロンプトを構築

        Args:
            symbol: シンボル
            base: 基軸通貨
            quote: 決済通貨
            target_date: 対象日
            events: 当日の発表済みイベント

        Returns:
            str: プロンプト文字列
        """
        events_text = self._format_events_for_prompt(events)

        return f"""あなたはFXトレードの経済指標アナリストです。
以下の経済指標発表結果に基づき、{symbol}への短期的インパクトを分析してください。

## 分析対象
- シンボル: {symbol} ({base}/{quote})
- 分析日: {target_date.year}年{target_date.month}月{target_date.day}日

## 当日の発表済み経済指標（{len(events)}件）
{events_text}

## 分析指示
1. 各指標のサプライズ方向と大きさを評価
2. {base}と{quote}への相対的な影響を判断
3. 複数指標の相互関係（矛盾・補強）を考慮
4. インパクトの持続時間を推定（即時収束型 vs 持続型）

## 出力形式（JSONのみで回答）
{{
  "net_surprise_score": <-1.0~+1.0: 加重サプライズ合計。+は{base}高方向>,
  "dominant_event_name": "<最大影響イベント名>",
  "dominant_surprise_pct": <最大影響イベントのサプライズ率>,
  "expected_volatility": <0.0~2.0: 通常比ボラティリティ倍率>,
  "price_direction_bias": <-1.0~+1.0: 短期価格方向。+は{symbol}上昇>,
  "convergence_hours": <0.5~72.0: インパクト収束までの推定時間>,
  "trade_caution_level": <0/1/2: 0=通常, 1=注意, 2=回避推奨>,
  "summary": "<分析要約（日本語、200文字以内）>"
}}"""

    def _build_event_result(self, data: dict) -> dict:
        """LLMレスポンスからイベント結果dictを構築

        Args:
            data: LLMレスポンスdict

        Returns:
            dict: バリデーション済み結果
        """
        caution = data.get("trade_caution_level")
        if caution is None or not isinstance(caution, (int, float)):
            caution_val = 0
        else:
            caution_val = max(0, min(2, int(caution)))

        return {
            "net_surprise_score": self._clip_score(
                data.get("net_surprise_score")
            ),
            "dominant_event_name": str(
                data.get("dominant_event_name", "")
            )[:200],
            "dominant_surprise_pct": float(
                data.get("dominant_surprise_pct", 0.0)
                if isinstance(
                    data.get("dominant_surprise_pct"),
                    (int, float),
                )
                else 0.0
            ),
            "expected_volatility": self._clip(
                data.get("expected_volatility"), 0.0, 2.0, 1.0
            ),
            "price_direction_bias": self._clip_score(
                data.get("price_direction_bias")
            ),
            "convergence_hours": self._clip(
                data.get("convergence_hours"), 0.5, 72.0, 0.0
            ),
            "trade_caution_level": caution_val,
            "summary": str(data.get("summary", ""))[:200],
        }

    @staticmethod
    def _default_event_result() -> dict:
        """イベントなし日のデフォルト結果

        Returns:
            dict: デフォルト値辞書
        """
        return {
            "net_surprise_score": 0.0,
            "dominant_event_name": "",
            "dominant_surprise_pct": 0.0,
            "expected_volatility": 1.0,
            "price_direction_bias": 0.0,
            "convergence_hours": 0.0,
            "trade_caution_level": 0,
            "summary": "関連経済指標の発表なし",
        }

    def _format_events_for_prompt(
        self,
        events: list[EconomicEvent],
    ) -> str:
        """イベントリストをプロンプト用テキストに変換

        Args:
            events: イベントリスト

        Returns:
            str: フォーマット済みテキスト
        """
        if not events:
            return "（なし）"

        lines = []
        sorted_events = sorted(
            events, key=lambda e: e.event_time
        )
        for ev in sorted_events:
            impact_label = _IMPACT_LABELS.get(
                ev.impact, ev.impact.value
            )
            time_str = ev.event_time.strftime("%H:%M")
            actual = (
                f"{ev.actual:.2f}"
                if ev.actual is not None
                else "未発表"
            )
            forecast = (
                f"{ev.forecast:.2f}"
                if ev.forecast is not None
                else "予測なし"
            )
            previous = (
                f"{ev.previous:.2f}"
                if ev.previous is not None
                else "前回なし"
            )
            surprise = ev.surprise_magnitude
            surprise_str = (
                f" サプライズ={surprise:+.1%}"
                if surprise is not None
                else ""
            )
            lines.append(
                f"- {time_str} [{impact_label}] "
                f"{ev.currency} {ev.event_name}: "
                f"実績={actual} 予測={forecast} "
                f"前回={previous}{surprise_str}"
            )
        return "\n".join(lines)
