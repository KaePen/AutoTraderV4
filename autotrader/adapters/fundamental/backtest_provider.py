"""バックテスト用ファンダメンタルプロバイダー

CSVファイルから過去の経済イベントを読み込み、
バックテスト時刻に合わせてFundamentalContextを提供する。

フォールバック階層:
1. イベントLLM CSV（llm_events_SYMBOL_YYYY.csv）→ 合成アルゴリズム
2. 月次LLM CSV（llm_context_SYMBOL_YYYY.csv）→ 旧ロジック
3. events CSV のみ → _estimate_bias_from_events
4. 何もなし → FundamentalContext.neutral()
"""

from __future__ import annotations

import bisect
import csv
import math
import re
from dataclasses import dataclass
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
    FundamentalMemory,
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

# 休日イベント判定パターン
_HOLIDAY_RE = re.compile(r"(?i)holiday|bank\s+holiday")

# インパクトレベル別重み（合成時に使用）
_IMPACT_WEIGHT: dict[str, float] = {
    "high": 3.0,
    "medium": 1.0,
    "low": 0.3,
}

# 影響度の最小閾値（これ未満は無視）
_INFLUENCE_THRESHOLD = 0.05

# 過去イベント検索の最大時間（時間）
_MAX_LOOKBACK_HOURS = 72


@dataclass(frozen=True)
class EventLLMRecord:
    """イベントLLM分析結果（CSV1行に対応）

    Attributes:
        event_time: イベント発表時刻（UTC）
        currency: 対象通貨
        event_name: イベント名称
        impact: インパクトレベル
        surprise_score: サプライズスコア
        direction_bias: 方向バイアス
        convergence_hours: 影響収束推定時間
        expected_volatility: ボラティリティ倍率
        trade_caution_level: 取引注意度
        is_holiday: 休日イベントフラグ
    """

    event_time: datetime
    currency: str
    event_name: str
    impact: str
    surprise_score: float
    direction_bias: float
    convergence_hours: float
    expected_volatility: float
    trade_caution_level: int
    is_holiday: bool


def compute_influence(
    elapsed_hours: float,
    convergence_hours: float,
    decay_coefficient: float = 2.0,
) -> float:
    """時間減衰による残存影響度を計算

    指数減衰モデル: exp(-decay_coeff * elapsed / convergence)
    convergence_hours の約35%で影響半減。

    Args:
        elapsed_hours: イベントからの経過時間
        convergence_hours: 影響収束推定時間
        decay_coefficient: 減衰係数（大きいほど急速に減衰）

    Returns:
        float: 残存影響度 (0.0~1.0)
    """
    if convergence_hours <= 0:
        return 0.0
    if elapsed_hours < 0:
        return 0.0
    if elapsed_hours >= convergence_hours:
        return 0.0
    ratio = elapsed_hours / convergence_hours
    return math.exp(-decay_coefficient * ratio)


class BacktestFundamentalProvider:
    """バックテスト用ファンダメンタルプロバイダー

    MT5の過去データCSVを読み込み、バックテスト時刻に
    合わせてFundamentalContextを提供する。

    フォールバック階層:
    1. イベントLLM CSV → 合成アルゴリズム（Phase 2）
    2. 月次LLM CSV → 旧ロジック
    3. events CSV のみ → バイアス計算
    4. 何もなし → FundamentalContext.neutral()

    Args:
        event_guard_minutes: 重要指標前の取引停止分数
        decay_coefficient: 時間減衰係数
    """

    def __init__(
        self,
        event_guard_minutes: int = 30,
        decay_coefficient: float = 2.0,
    ) -> None:
        """初期化

        Args:
            event_guard_minutes: 重要指標前の取引停止分数
            decay_coefficient: 時間減衰係数
        """
        self._guard_minutes = event_guard_minutes
        self._decay_coefficient = decay_coefficient
        self._events: list[EconomicEvent] = []
        self._events_sorted_ts: list[float] = []
        self._normalizer = EconomicEventNormalizer()
        self._loaded_files: list[str] = []

        # 月次LLMコンテキスト: symbol → (ts一覧, コンテキスト一覧)
        self._llm_ts: dict[str, list[float]] = {}
        self._llm_data: dict[str, list[dict]] = {}

        # イベントLLMレコード: symbol → レコード一覧
        self._event_llm_records: dict[
            str, list[EventLLMRecord]
        ] = {}
        # bisect用: symbol → タイムスタンプ一覧
        self._event_llm_ts: dict[str, list[float]] = {}

        # ニュースアイテム（published_at 昇順ソート）
        self._news_items: list[NewsItem] = []

        # Phase 2b: FundamentalMemory（バイアス蓄積）
        self.memory: FundamentalMemory | None = None

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

    def load_event_llm_csv(
        self,
        csv_path: str | Path,
        symbol: str,
    ) -> int:
        """イベントLLM分析結果CSVを読み込み

        Phase 1 で生成した `llm_events_SYMBOL_YYYY.csv` を
        読み込み、内部リストに追記する。

        Args:
            csv_path: イベントLLM CSVファイルパス
            symbol: 対象シンボル

        Returns:
            int: 読み込んだレコード数
        """
        path = Path(csv_path)
        if not path.exists():
            logger.warning(
                f"[BacktestFundamental] イベントLLM CSV"
                f"が見つかりません: {path}"
            )
            return 0

        loaded: list[EventLLMRecord] = []

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rec = self._parse_event_llm_row(row)
                    if rec is not None:
                        loaded.append(rec)

            if not loaded:
                return 0

            # 既存データとマージしてソート
            existing = self._event_llm_records.get(symbol, [])
            merged = existing + loaded
            merged.sort(key=lambda r: r.event_time)

            self._event_llm_records[symbol] = merged
            self._event_llm_ts[symbol] = [
                r.event_time.timestamp() for r in merged
            ]

            logger.info(
                f"[BacktestFundamental] イベントLLM "
                f"{len(loaded)}件読込: {path.name} ({symbol})"
            )
            return len(loaded)

        except Exception as e:
            logger.error(
                f"[BacktestFundamental] イベントLLM CSV"
                f"読込エラー: {e}"
            )
            return 0

    def get_context(
        self, current_time: datetime, symbol: str
    ) -> FundamentalContext:
        """指定時刻のファンダメンタルコンテキストを取得

        フォールバック階層:
        1. イベントLLM CSV → 合成アルゴリズム
        2. 月次LLM CSV → 旧ロジック
        3. events CSV のみ → バイアス計算
        4. 何もなし → FundamentalContext.neutral()

        Args:
            current_time: バックテスト現在時刻（UTC）
            symbol: トレード対象シンボル

        Returns:
            FundamentalContext: ファンダメンタルコンテキスト
        """
        # 共通: upcoming events / high impact check
        upcoming_dicts, high_impact_soon = (
            self._compute_upcoming(current_time, symbol)
        )

        # 優先度1: イベントLLMデータがある場合
        records = self._event_llm_records.get(symbol, [])
        if records:
            ctx = self._synthesize_event_llm_context(
                current_time, symbol,
                upcoming_dicts, high_impact_soon,
            )
            self._update_memory(ctx, current_time)
            return ctx

        # 優先度2-3: 旧ロジック（月次LLM or events計算）
        ctx = self._fallback_context(
            current_time, symbol,
            upcoming_dicts, high_impact_soon,
        )
        self._update_memory(ctx, current_time)
        return ctx

    def enable_memory(self) -> None:
        """FundamentalMemoryを有効化する

        Phase 2b: バイアス蓄積メモリを初期化。
        Runner側で呼び出す。
        """
        self.memory = FundamentalMemory()

    def _update_memory(
        self,
        ctx: FundamentalContext,
        current_time: datetime,
    ) -> None:
        """メモリをContextのイベント情報で更新

        - direction_biasとsurprise_scoreが有意なら更新
        - 日付変更時に日次減衰を適用

        Args:
            ctx: 生成されたコンテキスト
            current_time: 現在時刻
        """
        if self.memory is None:
            return

        current_date = current_time.date()

        # 日次減衰
        if (
            self.memory.last_event_date is not None
            and current_date > self.memory.last_event_date
        ):
            days = (
                current_date - self.memory.last_event_date
            ).days
            self.memory.apply_daily_decay(days)

        # イベントバイアス更新（有意なイベントのみ）
        if (
            abs(ctx.direction_bias) > 0.05
            and abs(ctx.surprise_score) > 0.05
        ):
            self.memory.update_event(
                ctx.direction_bias, ctx.surprise_score,
            )
            self.memory.last_event_date = current_date
        elif self.memory.last_event_date is None:
            self.memory.last_event_date = current_date

    # --------------------------------------------------
    # プライベート: 共通ヘルパー
    # --------------------------------------------------

    def _compute_upcoming(
        self,
        current_time: datetime,
        symbol: str,
    ) -> tuple[list[dict], bool]:
        """直近イベント情報と高インパクトフラグを計算

        Args:
            current_time: 現在時刻
            symbol: 対象シンボル

        Returns:
            tuple[list[dict], bool]: (upcoming_dicts, high_impact)
        """
        if not self._events:
            return [], False

        symbol_events = self._normalizer.filter_by_symbol(
            self._events, symbol
        )
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
        high_impact_soon = any(
            ev.impact == ImpactLevel.HIGH
            and 0 <= ev.minutes_until(current_time)
            <= self._guard_minutes
            for ev in upcoming
        )
        return upcoming_dicts, high_impact_soon

    # --------------------------------------------------
    # プライベート: イベントLLM合成（Phase 2）
    # --------------------------------------------------

    def _synthesize_event_llm_context(
        self,
        current_time: datetime,
        symbol: str,
        upcoming_dicts: list[dict],
        high_impact_soon: bool,
    ) -> FundamentalContext:
        """イベントLLMデータから合成コンテキストを生成

        Args:
            current_time: 現在時刻
            symbol: 対象シンボル
            upcoming_dicts: 直近イベント情報
            high_impact_soon: 高インパクトフラグ

        Returns:
            FundamentalContext: 合成コンテキスト
        """
        records = self._event_llm_records[symbol]
        ts_list = self._event_llm_ts[symbol]
        current_ts = current_time.timestamp()

        # 過去72時間のイベントを候補に（bisect検索）
        cutoff_ts = (
            current_time - timedelta(hours=_MAX_LOOKBACK_HOURS)
        ).timestamp()
        lo = bisect.bisect_left(ts_list, cutoff_ts)
        hi = bisect.bisect_right(ts_list, current_ts)
        candidates = records[lo:hi]

        # 各候補の影響度を計算
        active: list[tuple[EventLLMRecord, float]] = []
        for rec in candidates:
            elapsed_h = (
                current_time - rec.event_time
            ).total_seconds() / 3600
            infl = compute_influence(
                elapsed_h,
                rec.convergence_hours,
                self._decay_coefficient,
            )
            if infl > _INFLUENCE_THRESHOLD:
                active.append((rec, infl))

        # アクティブイベントがなければニュートラル
        if not active:
            return FundamentalContext(
                upcoming_events=upcoming_dicts,
                has_high_impact_within_30min=high_impact_soon,
            )

        # --- 方向性合成（重み付き平均） ---
        total_w = 0.0
        w_bias = 0.0
        w_surprise = 0.0
        for rec, infl in active:
            w = infl * _IMPACT_WEIGHT.get(rec.impact, 0.3)
            w_bias += rec.direction_bias * w
            w_surprise += rec.surprise_score * w
            total_w += w

        direction_bias = 0.0
        surprise_score = 0.0
        if total_w > 0:
            direction_bias = max(
                -1.0, min(1.0, w_bias / total_w)
            )
            surprise_score = max(
                -1.0, min(1.0, w_surprise / total_w)
            )

        # --- ボラティリティ合成（通常イベントのmax） ---
        normal_vols = [
            1.0 + (rec.expected_volatility - 1.0) * infl
            for rec, infl in active
            if not rec.is_holiday
        ]
        volatility_multiplier = (
            max(normal_vols) if normal_vols else 1.0
        )

        # --- 流動性合成（休日イベントのmin） ---
        liquidity_factor = 1.0
        is_holiday = False
        for rec, infl in active:
            if rec.is_holiday:
                is_holiday = True
                # 減衰適用: 流動性は時間とともに正常化
                liq = (
                    rec.expected_volatility * infl
                    + (1.0 - infl)
                )
                liquidity_factor = min(liquidity_factor, liq)

        # --- 注意度合成（全アクティブイベントのmax） ---
        event_caution_level = max(
            rec.trade_caution_level
            for rec, infl in active
        )

        # --- 収束進捗（最も未収束なもの） ---
        convergence_progress = min(
            1.0 - infl for _, infl in active
        )

        # --- アクティブイベント数 ---
        active_event_count = len(active)

        return FundamentalContext(
            has_high_impact_within_30min=high_impact_soon,
            event_caution_level=event_caution_level,
            is_holiday=is_holiday,
            liquidity_factor=liquidity_factor,
            volatility_multiplier=volatility_multiplier,
            active_event_count=active_event_count,
            direction_bias=direction_bias,
            surprise_score=surprise_score,
            convergence_progress=convergence_progress,
            upcoming_events=upcoming_dicts,
        )

    # --------------------------------------------------
    # プライベート: フォールバック（旧ロジック）
    # --------------------------------------------------

    def _fallback_context(
        self,
        current_time: datetime,
        symbol: str,
        upcoming_dicts: list[dict],
        high_impact_soon: bool,
    ) -> FundamentalContext:
        """旧ロジックによるフォールバックコンテキスト

        月次LLMデータまたはイベントベース計算で
        後方互換フィールドを設定する。

        Args:
            current_time: 現在時刻
            symbol: 対象シンボル
            upcoming_dicts: 直近イベント情報
            high_impact_soon: 高インパクトフラグ

        Returns:
            FundamentalContext: フォールバックコンテキスト
        """
        llm_ctx = self._get_llm_context(current_time, symbol)

        if not self._events:
            return FundamentalContext(
                macro_bias_score=llm_ctx["macro_bias_score"],
                macro_bias_summary=llm_ctx[
                    "macro_bias_summary"
                ],
                post_event_bias_score=llm_ctx[
                    "post_event_bias_score"
                ],
                post_event_summary=llm_ctx[
                    "post_event_summary"
                ],
                sentiment_score=llm_ctx["sentiment_score"],
                upcoming_events=upcoming_dicts,
                has_high_impact_within_30min=high_impact_soon,
            )

        # シンボル関連イベントにフィルタリング
        symbol_events = self._normalizer.filter_by_symbol(
            self._events, symbol
        )

        # 24hマクロバイアス計算
        released_24h = self._get_released_events(
            symbol_events, current_time, hours=24
        )
        macro_bias, macro_summary = (
            self._estimate_bias_from_events(
                released_24h, symbol
            )
        )

        # 4h指標後バイアス計算
        released_4h = self._get_released_events(
            symbol_events, current_time, hours=4
        )
        post_bias, post_summary = (
            self._estimate_bias_from_events(
                released_4h, symbol
            )
        )

        # LLMコンテキスト優先
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
            # Phase 2 フィールドは direction_bias に旧バイアスを反映
            direction_bias=p_score,
        )

    # --------------------------------------------------
    # プライベート: ユーティリティ
    # --------------------------------------------------

    def _parse_event_llm_row(
        self, row: dict
    ) -> EventLLMRecord | None:
        """CSV行をEventLLMRecordに変換

        Args:
            row: CSV行辞書

        Returns:
            EventLLMRecord | None: 変換済みレコード
        """
        event_time_str = row.get("event_time", "")
        if not event_time_str:
            return None

        try:
            event_time = datetime.fromisoformat(event_time_str)
            if event_time.tzinfo is None:
                event_time = event_time.replace(
                    tzinfo=timezone.utc
                )
        except ValueError:
            return None

        currency = row.get("currency", "").upper()
        event_name = row.get("event_name", "")
        if not currency or not event_name:
            return None

        def _f(val: str, default: float = 0.0) -> float:
            try:
                return float(val) if val else default
            except ValueError:
                return default

        def _i(val: str, default: int = 0) -> int:
            try:
                return int(float(val)) if val else default
            except ValueError:
                return default

        return EventLLMRecord(
            event_time=event_time,
            currency=currency,
            event_name=event_name,
            impact=row.get("impact", "low").lower(),
            surprise_score=max(
                -1.0, min(1.0, _f(row.get(
                    "surprise_score", ""
                )))
            ),
            direction_bias=max(
                -1.0, min(1.0, _f(row.get(
                    "direction_bias", ""
                )))
            ),
            convergence_hours=max(
                0.0, min(72.0, _f(row.get(
                    "convergence_hours", ""
                )))
            ),
            expected_volatility=max(
                0.0, min(2.0, _f(row.get(
                    "expected_volatility", ""
                )))
            ),
            trade_caution_level=max(
                0, min(2, _i(row.get(
                    "trade_caution_level", ""
                )))
            ),
            is_holiday=self._parse_is_holiday(
                row, event_name,
            ),
        )

    @staticmethod
    def _parse_is_holiday(
        row: dict, event_name: str,
    ) -> bool:
        """CSVの is_holiday カラムを読み取り

        カラムが存在する場合はその値を使用。
        存在しない場合はevent_nameからの正規表現判定にフォールバック。

        Args:
            row: CSV行辞書
            event_name: イベント名称

        Returns:
            bool: 休日イベントかどうか
        """
        raw = row.get("is_holiday")
        if raw is not None and raw != "":
            return str(raw).strip().lower() in (
                "true", "1", "yes",
            )
        # フォールバック: 旧CSVとの後方互換
        return bool(_HOLIDAY_RE.search(event_name))

    def _get_released_events(
        self,
        events: list[EconomicEvent],
        current_time: datetime,
        hours: int,
    ) -> list[EconomicEvent]:
        """指定時間内の発表済みイベントを取得

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

        Args:
            released_events: 発表済みイベントリスト
            symbol: 対象シンボル

        Returns:
            tuple[float, str]: (bias_score, summary_text)
        """
        if not released_events:
            return 0.0, "発表済み指標なし"

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
                if ev.previous is None:
                    continue
                reference = ev.previous
            else:
                reference = ev.forecast

            if reference == 0.0:
                continue

            surprise = (ev.actual - reference) / abs(reference)

            multiplier = (
                _HIGH_IMPACT_MULTIPLIER
                if ev.impact == ImpactLevel.HIGH
                else 1.0
            )

            if ev.currency == base_cur:
                bias = surprise * multiplier
            elif ev.currency == quote_cur:
                bias = -surprise * multiplier
            else:
                continue

            total_bias += bias
            direction = "↑" if bias > 0 else "↓"
            event_summaries.append(
                f"{ev.currency}/{ev.event_name}{direction}"
            )

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
        idx = bisect.bisect_right(ts_list, current_ts) - 1
        if idx < 0:
            return _DEFAULT_LLM_CONTEXT.copy()

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
