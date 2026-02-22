"""バックテスト用ファンダメンタルプロバイダー

CSVファイルから過去の経済イベントを読み込み、
バックテスト時刻に合わせてFundamentalContextを提供する。

事前生成済みLLMコンテキストCSVがある場合はそちらを優先し、
マクロバイアス・センチメントスコアを正確に再現する。
"""

from __future__ import annotations

import bisect
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from autotrader.adapters.fundamental.news_csv_writer import (
    read_news_csv,
)
from autotrader.adapters.fundamental.news_schemas import NewsItem
from autotrader.adapters.fundamental.normalizer import (
    EconomicEventNormalizer,
)
from autotrader.adapters.fundamental.schemas import (
    EconomicEvent,
    EventSource,
    FundamentalContext,
    ImpactLevel,
)

# 経済イベントCSVカラム定義
_CSV_COLUMNS = [
    "event_id", "event_time", "currency", "event_name",
    "impact", "actual", "forecast", "previous",
]

# シンボル→通貨ペア（先行・後続）のマッピング
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

# 高インパクト指標のバイアス乗数
_HIGH_IMPACT_MULTIPLIER = 3.0

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

        # ニュースアイテム（published_at 昇順ソート）
        self._news_items: list[NewsItem] = []

    def load_news_csv(self, csv_path: str | Path) -> int:
        """ニュースCSVを読み込み

        `collect_gdelt_news.py` で生成した
        `news_YYYY.csv` を読み込み、内部リストに追記する。

        Args:
            csv_path: ニュースCSVファイルパス

        Returns:
            int: 読み込んだニュース件数
        """
        path = Path(csv_path)
        items = read_news_csv(path)
        if not items:
            return 0

        # 追記してソート
        self._news_items.extend(items)
        self._news_items.sort(key=lambda n: n.published_at)
        logger.info(
            f"[BacktestFundamental] ニュース"
            f"{len(items)}件読込: {path.name}"
        )
        return len(items)

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

            # 重複排除してマージし、時刻順ソート（bisect検索用）
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
        ない場合はイベントベースのバイアス計算にフォールバック。

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

        # 直前24時間の発表済みイベントからマクロバイアスを計算
        released_24h = self._get_released_events(
            symbol_events, current_time, hours=24
        )
        macro_bias, macro_summary = self._estimate_bias_from_events(
            released_24h, symbol
        )

        # 直前4時間の発表済みイベントから指標後バイアスを計算
        released_4h = self._get_released_events(
            symbol_events, current_time, hours=4
        )
        post_bias, post_summary = self._estimate_bias_from_events(
            released_4h, symbol
        )

        # LLMコンテキストがあればそちらを優先、
        # なければイベントベースの計算結果を使用
        has_llm = (
            symbol in self._llm_ts
            and len(self._llm_ts[symbol]) > 0
        )
        if has_llm:
            m_score = llm_ctx["macro_bias_score"]
            m_summary = llm_ctx["macro_bias_summary"]
            p_score = llm_ctx["post_event_bias_score"]
            p_summary = llm_ctx["post_event_summary"]
            s_score = llm_ctx["sentiment_score"]
        else:
            m_score = macro_bias
            m_summary = macro_summary
            p_score = post_bias
            p_summary = post_summary
            s_score = 0.0

        return FundamentalContext(
            macro_bias_score=m_score,
            macro_bias_summary=m_summary,
            post_event_bias_score=p_score,
            post_event_summary=p_summary,
            sentiment_score=s_score,
            upcoming_events=upcoming_dicts,
            has_high_impact_within_30min=high_impact_soon,
        )

    def _get_released_events(
        self,
        events: list[EconomicEvent],
        current_time: datetime,
        hours: int,
    ) -> list[EconomicEvent]:
        """指定時間内の発表済みイベントを取得

        イベントリストは event_time 昇順ソート済みを前提として
        bisect による O(log n) 検索を使用する。

        Args:
            events: 時刻昇順ソート済みイベントリスト
            current_time: 現在時刻（UTC）
            hours: 過去何時間を対象とするか

        Returns:
            list[EconomicEvent]: 発表済みイベントリスト
        """
        if not events:
            return []
        cutoff = current_time - timedelta(hours=hours)
        # bisect で検索範囲を絞る
        times = [ev.event_time for ev in events]
        lo = bisect.bisect_left(times, cutoff)
        hi = bisect.bisect_left(times, current_time)
        return [
            ev for ev in events[lo:hi]
            if ev.actual is not None
        ]

    def _estimate_bias_from_events(
        self,
        released_events: list[EconomicEvent],
        symbol: str,
    ) -> tuple[float, str]:
        """発表済みイベントからバイアスを計算

        surprise_magnitude（実績/予測乖離）から
        symbol の通貨方向バイアスを計算する。

        実績 > 予測 → 発表通貨にポジティブバイアス（先行通貨なら+、後続通貨なら-）
        実績 < 予測 → 発表通貨にネガティブバイアス
        高インパクト指標は乗数3倍で適用。
        バイアスは -1.0〜+1.0 にクリップ。

        Args:
            released_events: 発表済みイベントリスト
            symbol: 対象シンボル

        Returns:
            tuple[float, str]: (bias_score, summary_text)
        """
        if not released_events:
            return 0.0, "発表済み指標なし"

        # シンボル→通貨ペア取得
        sym_upper = symbol.upper()
        base_cur, quote_cur = _SYMBOL_CURRENCIES.get(
            sym_upper, (sym_upper[:3], sym_upper[3:])
        )

        total_bias = 0.0
        event_summaries: list[str] = []

        for ev in released_events:
            if ev.actual is None:
                continue
            if ev.forecast is None:
                # 予測なしの場合は前回値を参照
                if ev.previous is None:
                    continue
                reference = ev.previous
            else:
                reference = ev.forecast

            if reference == 0.0:
                continue

            # サプライズ幅（実績 - 予測）の正規化
            surprise = (ev.actual - reference) / abs(reference)

            # インパクト乗数
            multiplier = (
                _HIGH_IMPACT_MULTIPLIER
                if ev.impact == ImpactLevel.HIGH
                else 1.0
            )

            # 通貨方向でバイアス符号を決定
            if ev.currency == base_cur:
                # 先行通貨の強さ → ペアにとってポジティブ
                bias = surprise * multiplier
            elif ev.currency == quote_cur:
                # 後続通貨の強さ → ペアにとってネガティブ
                bias = -surprise * multiplier
            else:
                continue

            total_bias += bias
            direction = "↑" if bias > 0 else "↓"
            event_summaries.append(
                f"{ev.currency}/{ev.event_name}{direction}"
            )

        # -1.0〜+1.0 にクリップ
        clipped = max(-1.0, min(1.0, total_bias))

        if event_summaries:
            summary = f"バイアス{clipped:+.2f}: " + ", ".join(
                event_summaries[:3]
            )
        else:
            summary = "バイアス計算対象指標なし"

        return clipped, summary

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
