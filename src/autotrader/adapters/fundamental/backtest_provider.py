"""バックテスト用ファンダメンタルプロバイダー

CSVファイルから過去の経済イベントを読み込み、
バックテスト時刻に合わせてFundamentalContextを提供する。

事前生成済みLLMコンテキストCSVがある場合はそちらを優先し、
マクロバイアス・センチメントスコアを正確に再現する。
"""

from __future__ import annotations

import bisect
import csv
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    FundamentalContext,
    ImpactLevel,
)
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)

# 経済イベントCSVカラム定義
_CSV_COLUMNS = [
    "event_id", "event_time", "currency", "event_name",
    "impact", "actual", "forecast", "previous",
]

# LLMコンテキストCSVカラム定義
_LLM_CSV_COLUMNS = [
    "period_start",
    "macro_bias_score",
    "macro_bias_summary",
    "post_event_bias_score",
    "post_event_summary",
    "sentiment_score",
]

# LLMコンテキストのデフォルト値（データなし時）
_DEFAULT_LLM_CONTEXT = {
    "macro_bias_score": 0.0,
    "macro_bias_summary": "バックテスト（LLMコンテキストなし）",
    "post_event_bias_score": 0.0,
    "post_event_summary": "バックテスト（LLMコンテキストなし）",
    "sentiment_score": 0.0,
}


class BacktestFundamentalProvider:
    """バックテスト用ファンダメンタルプロバイダー

    MT5の過去データCSVを読み込み、バックテスト時刻に
    合わせてFundamentalContextを提供する。

    事前生成済みLLMコンテキストCSV（`llm_context_SYMBOL_YYYY.csv`）が
    あれば `load_llm_context_csv()` で読み込む。
    LLMコンテキストがある場合はそちらのバイアス値を優先する。

    使用するCSVフォーマット（`data/fundamental/events_YYYY.csv`）:
    ```
    event_id,event_time,currency,event_name,impact,actual,forecast,previous
    mt5_12345,2024-01-15T08:30:00+00:00,USD,NFP,high,256000,180000,185000
    ```

    Args:
        event_guard_minutes: 重要指標前の取引停止分数
    """

    def __init__(self, event_guard_minutes: int = 30) -> None:
        """初期化

        Args:
            event_guard_minutes: 重要指標前の取引停止分数
        """
        self._guard_minutes = event_guard_minutes
        self._events: list[EconomicEvent] = []
        self._events_sorted_ts: list[float] = []
        self._normalizer = EconomicEventNormalizer()
        self._loaded_files: list[str] = []

        # LLMコンテキスト: symbol → (period_start_ts一覧, コンテキスト一覧)
        # bisect用にUNIXタイムスタンプのソート済みリストを保持
        self._llm_ts: dict[str, list[float]] = {}
        self._llm_data: dict[str, list[dict]] = {}

    def load_csv(self, csv_path: str | Path) -> int:
        """CSVファイルから経済イベントを読み込み

        Args:
            csv_path: CSVファイルパス

        Returns:
            int: 読み込んだイベント数
        """
        path = Path(csv_path)
        if not path.exists():
            logger.warning(
                f"[BacktestFundamental] CSVが見つかりません: {path}"
            )
            return 0

        loaded: list[EconomicEvent] = []
        fetched_at = datetime.now(timezone.utc)

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        event = self._parse_row(row, fetched_at)
                        if event:
                            loaded.append(event)
                    except Exception as e:
                        logger.debug(
                            f"[BacktestFundamental] 行スキップ: {e}"
                        )
                        continue

            # 重複排除してマージ
            self._events.extend(loaded)
            self._events = self._normalizer.deduplicate(
                self._events
            )
            # イベントを時系列順にソートしてbisect用TSリストを構築
            self._events.sort(key=lambda e: e.event_time)
            self._events_sorted_ts = [
                e.event_time.timestamp() for e in self._events
            ]
            self._loaded_files.append(str(path))

            logger.info(
                f"[BacktestFundamental] {len(loaded)}件読込: "
                f"{path.name}"
            )
            return len(loaded)

        except Exception as e:
            logger.error(
                f"[BacktestFundamental] CSV読込エラー: {e}"
            )
            return 0

    def load_llm_context_csv(
        self,
        csv_path: str | Path,
        symbol: str,
    ) -> int:
        """事前生成済みLLMコンテキストCSVを読み込み

        `generate_fundamental_llm.py` で生成した
        `llm_context_SYMBOL_YYYY.csv` を読み込む。

        Args:
            csv_path: LLMコンテキストCSVファイルパス
            symbol: 対象シンボル（例: USDJPY）

        Returns:
            int: 読み込んだ期間数（月数）
        """
        path = Path(csv_path)
        if not path.exists():
            logger.warning(
                f"[BacktestFundamental] LLMコンテキストCSV"
                f"が見つかりません: {path}"
            )
            return 0

        def _f(val: str, default: float = 0.0) -> float:
            """文字列をfloatに変換"""
            try:
                return float(val) if val else default
            except ValueError:
                return default

        ts_list: list[float] = []
        data_list: list[dict] = []

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    period_str = row.get("period_start", "")
                    if not period_str:
                        continue
                    try:
                        period_dt = datetime.fromisoformat(
                            period_str
                        )
                        if period_dt.tzinfo is None:
                            period_dt = period_dt.replace(
                                tzinfo=timezone.utc
                            )
                    except ValueError:
                        continue

                    ts_list.append(period_dt.timestamp())
                    data_list.append({
                        "macro_bias_score": _f(
                            row.get("macro_bias_score", "")
                        ),
                        "macro_bias_summary": row.get(
                            "macro_bias_summary", ""
                        ),
                        "post_event_bias_score": _f(
                            row.get("post_event_bias_score", "")
                        ),
                        "post_event_summary": row.get(
                            "post_event_summary", ""
                        ),
                        "sentiment_score": _f(
                            row.get("sentiment_score", "")
                        ),
                    })

            # シンボルに追記（既存データとマージ）
            existing_ts = self._llm_ts.get(symbol, [])
            existing_data = self._llm_data.get(symbol, [])

            # マージしてソート
            combined = sorted(
                zip(existing_ts + ts_list,
                    existing_data + data_list),
                key=lambda x: x[0],
            )
            if combined:
                merged_ts, merged_data = zip(*combined)
                self._llm_ts[symbol] = list(merged_ts)
                self._llm_data[symbol] = list(merged_data)
            else:
                self._llm_ts[symbol] = []
                self._llm_data[symbol] = []

            loaded = len(ts_list)
            logger.info(
                f"[BacktestFundamental] LLMコンテキスト"
                f"{loaded}期間読込: {path.name} ({symbol})"
            )
            return loaded

        except Exception as e:
            logger.error(
                f"[BacktestFundamental] LLMコンテキストCSV"
                f"読込エラー: {e}"
            )
            return 0

    def get_context(
        self, current_time: datetime, symbol: str
    ) -> FundamentalContext:
        """指定時刻のファンダメンタルコンテキストを取得

        事前生成済みLLMコンテキストがあればそちらを使用。
        ない場合はニュートラルなコンテキストを返す。

        Args:
            current_time: バックテスト現在時刻（UTC）
            symbol: トレード対象シンボル

        Returns:
            FundamentalContext: ファンダメンタルコンテキスト
        """
        # LLMコンテキストの取得（bisectで O(log n)）
        llm_ctx = self._get_llm_context(current_time, symbol)

        # 経済イベントがない場合はLLMコンテキストだけ返す
        if not self._events:
            return FundamentalContext(
                macro_bias_score=llm_ctx["macro_bias_score"],
                macro_bias_summary=llm_ctx["macro_bias_summary"],
                post_event_bias_score=llm_ctx[
                    "post_event_bias_score"
                ],
                post_event_summary=llm_ctx[
                    "post_event_summary"
                ],
                sentiment_score=llm_ctx["sentiment_score"],
                upcoming_events=[],
                has_high_impact_within_30min=False,
            )

        # シンボル関連イベントにフィルタリング
        symbol_events = self._normalizer.filter_by_symbol(
            self._events, symbol
        )

        # 直近1時間のイベントを取得
        upcoming = self._normalizer.get_upcoming_events(
            symbol_events, current_time, window_minutes=60
        )

        upcoming_dicts = [
            {
                "name": ev.event_name,
                "minutes_until": ev.minutes_until(current_time),
                "impact": ev.impact.value,
            }
            for ev in upcoming
        ]

        # 30分以内の高インパクト指標チェック
        high_impact_soon = any(
            ev.impact == ImpactLevel.HIGH
            and 0 <= ev.minutes_until(current_time)
            <= self._guard_minutes
            for ev in upcoming
        )

        return FundamentalContext(
            macro_bias_score=llm_ctx["macro_bias_score"],
            macro_bias_summary=llm_ctx["macro_bias_summary"],
            post_event_bias_score=llm_ctx[
                "post_event_bias_score"
            ],
            post_event_summary=llm_ctx["post_event_summary"],
            sentiment_score=llm_ctx["sentiment_score"],
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )

    def _get_llm_context(
        self, current_time: datetime, symbol: str
    ) -> dict:
        """指定時刻のLLMコンテキストをbisectで取得

        直前の月次コンテキストを O(log n) で探索。

        Args:
            current_time: バックテスト現在時刻
            symbol: トレード対象シンボル

        Returns:
            dict: LLMコンテキストスコア辞書
        """
        ts_list = self._llm_ts.get(symbol)
        data_list = self._llm_data.get(symbol)
        if not ts_list or not data_list:
            return _DEFAULT_LLM_CONTEXT.copy()

        current_ts = current_time.timestamp()
        # bisect_right で current_ts より大きい最初のインデックス
        idx = bisect.bisect_right(ts_list, current_ts) - 1
        if idx < 0:
            return _DEFAULT_LLM_CONTEXT.copy()

        # .copy() でイミュータブル保証（外部による変更防止）
        return data_list[idx].copy()

    def _parse_row(
        self, row: dict, fetched_at: datetime
    ) -> EconomicEvent | None:
        """CSVの1行をEconomicEventに変換

        Args:
            row: CSV行辞書
            fetched_at: 取得時刻

        Returns:
            EconomicEvent | None: 変換済みイベント
        """
        event_time_str = row.get("event_time", "")
        if not event_time_str:
            return None

        try:
            # ISO8601形式でパース
            event_time = datetime.fromisoformat(event_time_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(
                    tzinfo=timezone.utc
                )
        except ValueError:
            return None

        currency = row.get("currency", "").upper()
        if not currency:
            return None

        event_name = row.get("event_name", "")
        if not event_name:
            return None

        impact_str = row.get("impact", "low").lower()
        impact = {
            "high": ImpactLevel.HIGH,
            "medium": ImpactLevel.MEDIUM,
            "low": ImpactLevel.LOW,
        }.get(impact_str, ImpactLevel.LOW)

        def parse_float(val: str) -> float | None:
            """文字列をfloatに変換"""
            if not val or val.strip() == "":
                return None
            try:
                return float(val)
            except ValueError:
                return None

        return EconomicEvent(
            event_id=row.get(
                "event_id", f"bt_{hash(event_name)}"
            ),
            event_time=event_time,
            currency=currency,
            event_name=event_name,
            impact=impact,
            source=EventSource.MT5,
            fetched_at=fetched_at,
            actual=parse_float(row.get("actual", "")),
            forecast=parse_float(row.get("forecast", "")),
            previous=parse_float(row.get("previous", "")),
        )
