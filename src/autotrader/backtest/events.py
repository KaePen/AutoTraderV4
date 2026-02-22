"""バックテストイベントシステム"""

from __future__ import annotations

import heapq
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Callable

import pandas as pd

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class EventType(Enum):
    """イベントタイプ"""
    
    # 進捗イベント
    BACKTEST_START = "backtest_start"
    BACKTEST_END = "backtest_end"
    YEAR_START = "year_start"
    YEAR_END = "year_end"
    MONTH_START = "month_start"
    MONTH_END = "month_end"
    PROGRESS = "progress"
    
    # トレードイベント
    SIGNAL_GENERATED = "signal_generated"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    
    # 分析イベント
    METRICS_UPDATE = "metrics_update"
    INDICATOR_UPDATE = "indicator_update"

    # 初期化進捗イベント（TFロード・年並列実行の無音フェーズを可視化）
    INIT_PROGRESS = "init_progress"


@dataclass
class BacktestEvent:
    """バックテストイベント基底クラス"""
    
    event_type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    data: dict = field(default_factory=dict)


@dataclass
class ProgressEvent(BacktestEvent):
    """進捗イベント"""
    
    event_type: EventType = field(default=EventType.PROGRESS)
    current: int = 0
    total: int = 0
    percentage: float = 0.0
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None
    message: str = ""
    
    def __post_init__(self):
        if self.total > 0:
            self.percentage = (self.current / self.total) * 100


@dataclass
class InitProgressEvent(BacktestEvent):
    """初期化進捗イベント

    TFインジケータ計算・年並列実行など、無音のフェーズをUIに通知する。

    Attributes:
        phase: フェーズ識別子
            "tf_loading": タイムフレームごとのインジケータ計算
            "year_parallel": 年並列バックテストの完了通知
        label: 現在処理中の項目ラベル（例: "M1", "2023年"）
        current: 完了済み件数
        total: 全件数
    """

    event_type: EventType = field(default=EventType.INIT_PROGRESS)
    phase: str = ""
    label: str = ""
    current: int = 0
    total: int = 0


@dataclass
class SignalEvent(BacktestEvent):
    """シグナル生成イベント"""

    event_type: EventType = field(default=EventType.SIGNAL_GENERATED)
    signal_type: str = ""
    symbol: str = ""
    timeframe: str = ""
    confidence: float = 0.0
    sl_pips: float = 0.0
    tp_pips: float = 0.0
    rationale: str = ""
    aligned_timeframes: list[str] = field(default_factory=list)
    # ログ強化フィールド
    tf_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    filters_applied: list[str] = field(default_factory=list)
    # スコア内訳（TF別の各指標貢献値）
    score_breakdowns: dict[str, dict[str, float]] = field(
        default_factory=dict
    )


@dataclass
class TradeEvent(BacktestEvent):
    """トレードイベント"""

    event_type: EventType = field(default=EventType.POSITION_OPENED)
    trade_id: str = ""
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    exit_price: float | None = None
    volume: float = 0.0
    profit_loss: float | None = None
    exit_reason: str | None = None
    opened_at: datetime | None = None  # エントリー時間
    # ログ強化フィールド
    close_reason_detail: str = ""
    holding_minutes: float = 0.0
    pips: float = 0.0
    # モード/レジーム追跡
    trading_mode: str = ""
    market_regime: str = ""
    strategy_id: str = ""
    # MFE/MAE（pips単位）
    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    # エントリー時メトリクス（pips単位）
    entry_spread_pips: float = 0.0
    entry_atr: float = 0.0
    entry_adx: float = 0.0
    entry_bb_width: float = 0.0
    # A) 執行・コスト系
    exit_spread_pips: float = 0.0
    slippage_pips: float = 0.0
    commission: float = 0.0
    # B) リスク管理・状態系
    equity_before: float = 0.0
    equity_after: float = 0.0
    dd_pct_at_entry: float = 0.0
    consecutive_losses: int = 0
    risk_per_trade_pct: float = 0.0
    lot: float = 0.0
    # C) ログ品質強化フィールド
    parent_trade_id: str = ""
    position_id: str = ""
    entry_threshold: float = 0.0
    htf_alignment: float = 0.0
    penalty_total: float = 0.0
    penalty_breakdown: dict[str, float] = field(
        default_factory=dict
    )
    trend_strength: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    time_to_mfe_minutes: float = 0.0
    session: str = ""
    # D) trigger/fill価格分離
    trigger_price: float = 0.0
    fill_price: float = 0.0

    def __post_init__(self):
        if self.exit_price is not None:
            self.event_type = EventType.POSITION_CLOSED


@dataclass
class MetricsEvent(BacktestEvent):
    """メトリクス更新イベント"""
    
    event_type: EventType = field(default=EventType.METRICS_UPDATE)
    balance: float = 0.0
    equity: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0


@dataclass(frozen=True)
class CandleEvent:
    """キャンドルイベント

    バックテストでの時系列イベントを表現。
    全タイムフレームをマージして時系列順に処理するために使用。

    Attributes:
        timestamp: イベント発生時刻
        timeframe: タイムフレーム文字列 (M1, M5, M15, H1, H4, D1)
        candle_data: キャンドルデータ (OHLCV)
        row_data: 指標値を含むデータ行
        timeframe_minutes: ソート用の時間足分数
    """

    timestamp: datetime
    timeframe: str
    candle_data: dict[str, float]
    row_data: dict[str, float]
    timeframe_minutes: int = 0

    def __post_init__(self) -> None:
        """タイムフレーム分数を設定"""
        tf_minutes_map = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440, "W1": 10080,
        }
        # frozen=True なので object.__setattr__ を使用
        object.__setattr__(
            self, "timeframe_minutes",
            tf_minutes_map.get(self.timeframe, 0)
        )

    def __lt__(self, other: "CandleEvent") -> bool:
        """比較演算子（heapq用）

        同時刻の場合は長期足を優先（分数が大きいほど後ろ→反転）。
        """
        if self.timestamp == other.timestamp:
            # 長期足を先に処理（分数が大きいほど優先）
            return self.timeframe_minutes > other.timeframe_minutes
        return self.timestamp < other.timestamp


class TimelineEventQueue:
    """タイムラインイベントキュー

    複数タイムフレームのキャンドルデータを時系列順にマージ。
    同時刻に複数TFが確定した場合はバッチで返す。
    """

    def __init__(
        self,
        market_data: dict[str, pd.DataFrame],
        symbol: str = "USDJPY",
    ):
        """初期化

        Args:
            market_data: タイムフレーム別のDataFrame
            symbol: シンボル
        """
        self._symbol = symbol
        self._events: list[CandleEvent] = []
        self._build_queue(market_data)

    def _build_queue(
        self,
        market_data: dict[str, pd.DataFrame],
    ) -> None:
        """全TFデータからイベントキューを構築"""

        import numpy as np

        for tf, df in market_data.items():
            if df is None or df.empty:
                continue

            # numpy配列ベースの高速構築
            times = df["time"].values
            opens = df["open"].values.astype(np.float64)
            highs = df["high"].values.astype(np.float64)
            lows = df["low"].values.astype(np.float64)
            closes = df["close"].values.astype(np.float64)
            volumes = df["volume"].values.astype(np.float64)

            # 指標列を事前取得
            extra_cols = [
                c for c in df.columns if c != "time"
            ]
            extra_arrays = {}
            for col in extra_cols:
                try:
                    extra_arrays[col] = (
                        df[col].values.astype(np.float64)
                    )
                except (ValueError, TypeError):
                    pass

            n_rows = len(df)
            for i in range(n_rows):
                ts_raw = times[i]
                if ts_raw is None or pd.isna(ts_raw):
                    continue
                timestamp = pd.Timestamp(ts_raw).to_pydatetime()

                # キャンドルデータ
                candle_data = {
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                    "volume": float(volumes[i]),
                }

                # 指標データをdict化
                row_data = {}
                for col, arr in extra_arrays.items():
                    val = arr[i]
                    if not np.isnan(val):
                        row_data[col] = float(val)

                event = CandleEvent(
                    timestamp=timestamp,
                    timeframe=tf,
                    candle_data=candle_data,
                    row_data=row_data,
                )
                heapq.heappush(self._events, event)

    def __iter__(self) -> "TimelineEventQueue":
        """イテレーター"""
        return self

    def __next__(self) -> list[CandleEvent]:
        """次のイベントバッチを返す

        同時刻のイベントをまとめて返す。

        Returns:
            list[CandleEvent]: 同時刻イベントのリスト

        Raises:
            StopIteration: キューが空の場合
        """

        if not self._events:
            raise StopIteration

        # 最初のイベントを取得
        first_event = heapq.heappop(self._events)
        batch = [first_event]

        # 同時刻のイベントを全て取得
        while self._events and self._events[0].timestamp == first_event.timestamp:
            batch.append(heapq.heappop(self._events))

        return batch

    def __len__(self) -> int:
        """残りイベント数"""
        return len(self._events)

    @property
    def total_events(self) -> int:
        """初期イベント総数（参考用）"""
        return len(self._events)


class EventListener(ABC):
    """イベントリスナー基底クラス"""
    
    @abstractmethod
    def on_event(self, event: BacktestEvent) -> None:
        """イベント処理"""
        pass


class RichEventListener(EventListener):
    """Rich進捗バー付きリスナー"""

    def __init__(self, verbose: bool = False):
        """初期化

        Args:
            verbose: 詳細出力モード
        """
        self.verbose = verbose
        self._progress = None
        self._task_id = None
        self._console = None
        self._year_task_id = None
        self._prep_task_id = None
        self._current_year = None
        self._last_progress_pct = -10
        self._is_tty = True
        # 年別進捗バー（並列実行中の年ごとのタスクID）
        self._year_row_tasks: dict[int, any] = {}
        # 非TTY用：年別の最終進捗（25%刻み重複防止）
        self._year_last_pct: dict[int, int] = {}

        try:
            from rich.console import Console
            from rich.progress import (
                Progress,
                SpinnerColumn,
                TextColumn,
                BarColumn,
                TaskProgressColumn,
                TimeRemainingColumn,
            )
            self._console = Console()
            self._is_tty = self._console.is_terminal

            if self._is_tty:
                self._progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(bar_width=40),
                    TaskProgressColumn(),
                    TimeRemainingColumn(),
                    console=self._console,
                )
        except ImportError:
            logger.warning("richライブラリが見つかりません")
            self._is_tty = False

    def on_event(self, event: BacktestEvent) -> None:
        """イベント処理"""
        if self._console is None:
            # richがない場合は標準出力にフォールバック
            self._handle_event_fallback(event)
            return

        if event.event_type == EventType.BACKTEST_START:
            start_year = event.data.get("start_year")
            end_year = event.data.get("end_year")
            if self._is_tty:
                self._console.print()
                self._console.rule(
                    f"[bold green]バックテスト開始: {start_year}-{end_year}",
                    style="green"
                )
                if self._progress:
                    self._progress.start()
                    # 準備フェーズを即座に可視化（TFロード前の空白期間対策）
                    self._prep_task_id = self._progress.add_task(
                        "[yellow]準備中...[/yellow]",
                        total=None,
                    )
            else:
                print(f"\n=== バックテスト開始: {start_year}-{end_year} ===", flush=True)

        elif event.event_type == EventType.INIT_PROGRESS:
            if isinstance(event, InitProgressEvent):
                self._handle_init_progress(event)

        elif event.event_type == EventType.BACKTEST_END:
            if self._is_tty and self._progress:
                # 残存している年別バーをすべて削除
                for task_id in list(self._year_row_tasks.values()):
                    self._progress.remove_task(task_id)
                self._year_row_tasks.clear()
                # year_parallelタスクが残っていれば削除してからstop
                if self._prep_task_id is not None:
                    self._progress.remove_task(self._prep_task_id)
                    self._prep_task_id = None
                self._progress.stop()
            cancelled = event.data.get("results", {}).get("cancelled", False)
            if cancelled:
                if self._is_tty:
                    self._console.print(
                        "[yellow]バックテストがキャンセルされました[/yellow]"
                    )
                else:
                    print("バックテストがキャンセルされました", flush=True)
            else:
                if self._is_tty:
                    self._console.rule(
                        "[bold green]バックテスト完了", style="green"
                    )
                else:
                    print("\n=== バックテスト完了 ===", flush=True)
            if self._is_tty:
                self._console.print()

        elif event.event_type == EventType.YEAR_START:
            year = event.data.get("year")
            self._current_year = year
            self._last_progress_pct = -10
            if self._is_tty and self._progress:
                self._year_task_id = self._progress.add_task(
                    f"[cyan]{year}年[/cyan]",
                    total=100
                )
            else:
                print(f"\n--- {year}年 処理開始 ---", flush=True)

        elif event.event_type == EventType.YEAR_END:
            year_data = event.data
            year = year_data.get("year")
            trades = year_data.get("trades", 0)
            win_rate = year_data.get("win_rate", 0)
            net_profit = year_data.get("net_profit", 0)

            if self._is_tty:
                if self._year_task_id is not None and self._progress:
                    self._progress.update(self._year_task_id, completed=100)
                    self._progress.remove_task(self._year_task_id)
                    self._year_task_id = None

                profit_color = "green" if net_profit > 0 else "red"
                self._console.print(
                    f"  [bold]{year}年[/bold]: "
                    f"取引 {trades}件, 勝率 {win_rate:.1f}%, "
                    f"[{profit_color}]損益 ¥{net_profit:+,.0f}[/{profit_color}]"
                )
            else:
                sign = "+" if net_profit > 0 else ""
                print(
                    f"  {year}年完了: "
                    f"取引 {trades}件, 勝率 {win_rate:.1f}%, "
                    f"損益 ¥{sign}{net_profit:,.0f}",
                    flush=True
                )

        elif event.event_type == EventType.MONTH_END:
            if self.verbose:
                month_data = event.data
                year = month_data.get("year")
                month = month_data.get("month")
                trades = month_data.get("trades", 0)
                pnl = month_data.get("pnl", 0)
                return_pct = month_data.get("return_pct", 0)

                if self._is_tty:
                    pnl_color = "green" if pnl >= 0 else "red"
                    self._console.print(
                        f"    {year}/{month:02d}: "
                        f"取引 {trades}件, "
                        f"[{pnl_color}]¥{pnl:+,.0f} ({return_pct:+.2f}%)"
                        f"[/{pnl_color}]"
                    )
                else:
                    sign = "+" if pnl >= 0 else ""
                    print(
                        f"    {year}/{month:02d}: "
                        f"取引 {trades}件, ¥{sign}{pnl:,.0f} ({return_pct:+.2f}%)",
                        flush=True
                    )

        elif event.event_type == EventType.PROGRESS:
            if isinstance(event, ProgressEvent):
                if self._is_tty:
                    if self._year_task_id is not None and self._progress:
                        self._progress.update(
                            self._year_task_id,
                            completed=event.percentage
                        )
                else:
                    # 10%刻みで進捗を出力
                    pct_10 = int(event.percentage // 10) * 10
                    if pct_10 > self._last_progress_pct:
                        self._last_progress_pct = pct_10
                        print(f"    進捗: {pct_10}%", flush=True)

        elif event.event_type == EventType.SIGNAL_GENERATED:
            if self.verbose and isinstance(event, SignalEvent):
                if event.signal_type != "HOLD":
                    if self._is_tty:
                        color = "green" if event.signal_type == "BUY" else "red"
                        self._console.print(
                            f"      [{color}]{event.signal_type}[/{color}] "
                            f"TF={event.timeframe} "
                            f"conf={event.confidence:.2f}"
                        )
                    else:
                        print(
                            f"      {event.signal_type} "
                            f"TF={event.timeframe} conf={event.confidence:.2f}",
                            flush=True
                        )

        elif event.event_type == EventType.POSITION_CLOSED:
            if isinstance(event, TradeEvent):
                pnl = event.profit_loss or 0
                if self.verbose or abs(pnl) > 10000:
                    if self._is_tty:
                        pnl_color = "green" if pnl >= 0 else "red"
                        self._console.print(
                            f"      決済: [{pnl_color}]¥{pnl:+,.0f}[/{pnl_color}] "
                            f"({event.exit_reason})"
                        )
                    else:
                        sign = "+" if pnl >= 0 else ""
                        print(
                            f"      決済: ¥{sign}{pnl:,.0f} ({event.exit_reason})",
                            flush=True
                        )

    def _handle_init_progress(self, event: InitProgressEvent) -> None:
        """初期化進捗イベントを処理（TFロード・年並列）"""
        if event.phase == "tf_loading":
            desc_tty = (
                f"[yellow]インジケータ計算[/yellow] "
                f"[dim]{event.label}[/dim]"
            )
            desc_plain = (
                f"  インジケータ計算: {event.label}"
                f" ({event.current}/{event.total})"
            )
            if self._is_tty and self._progress:
                if self._prep_task_id is None:
                    self._prep_task_id = self._progress.add_task(
                        desc_tty,
                        total=event.total,
                        completed=event.current,
                    )
                else:
                    self._progress.update(
                        self._prep_task_id,
                        total=event.total,
                        completed=event.current,
                        description=desc_tty,
                    )
                if event.current >= event.total:
                    self._progress.remove_task(self._prep_task_id)
                    self._prep_task_id = None
            else:
                print(desc_plain, flush=True)

        elif event.phase == "year_parallel":
            # 年完了数を表示（開始時は不確定スピナーのまま）
            if event.current == 0:
                desc_tty = "[cyan]年バックテスト並列実行中...[/cyan]"
            else:
                desc_tty = (
                    f"[cyan]年バックテスト並列実行中[/cyan] "
                    f"[dim]{event.label}完了[/dim]"
                )
            desc_plain = f"  並列処理: {event.current}/{event.total}年完了"
            if self._is_tty and self._progress:
                if self._prep_task_id is None:
                    self._prep_task_id = self._progress.add_task(
                        desc_tty,
                        total=None,  # 不確定モード（スピナー）で開始
                        completed=0,
                    )
                elif event.current == 0:
                    # 開始時：不確定モードを維持
                    self._progress.update(
                        self._prep_task_id,
                        total=None,
                        description=desc_tty,
                    )
                else:
                    # 最初の年完了時から確定モードへ切り替え
                    self._progress.update(
                        self._prep_task_id,
                        total=event.total,
                        completed=event.current,
                        description=desc_tty,
                    )
            else:
                print(desc_plain, flush=True)

        elif event.phase == "year_row_update":
            # 年別個別バー（並列実行中の年ごとのローソク足進捗）
            year = int(event.label)
            if self._is_tty and self._progress:
                if year not in self._year_row_tasks:
                    # 年バーを新規作成
                    task_id = self._progress.add_task(
                        f"  [dim]{year}年[/dim]",
                        total=event.total,
                        completed=event.current,
                    )
                    self._year_row_tasks[year] = task_id
                else:
                    self._progress.update(
                        self._year_row_tasks[year],
                        completed=event.current,
                        total=event.total,
                    )
                # 完了したら年バーを削除
                if event.total > 0 and event.current >= event.total:
                    self._progress.remove_task(
                        self._year_row_tasks.pop(year)
                    )
            else:
                # 非TTY：25%刻みで出力
                pct = (
                    event.current / event.total * 100
                    if event.total > 0 else 0
                )
                pct_25 = int(pct // 25) * 25
                last = self._year_last_pct.get(year, -1)
                if pct_25 > last:
                    self._year_last_pct[year] = pct_25
                    print(
                        f"  {year}年: "
                        f"{event.current:,}/{event.total:,}足"
                        f" ({pct:.0f}%)",
                        flush=True,
                    )

    def _handle_event_fallback(self, event: BacktestEvent) -> None:
        """richなしでのイベント処理"""
        if event.event_type == EventType.BACKTEST_START:
            start_year = event.data.get("start_year")
            end_year = event.data.get("end_year")
            print(f"\n=== バックテスト開始: {start_year}-{end_year} ===", flush=True)

        elif event.event_type == EventType.BACKTEST_END:
            print("\n=== バックテスト完了 ===", flush=True)

        elif event.event_type == EventType.YEAR_START:
            year = event.data.get("year")
            self._current_year = year
            self._last_progress_pct = -10
            print(f"\n--- {year}年 処理開始 ---", flush=True)

        elif event.event_type == EventType.YEAR_END:
            year_data = event.data
            year = year_data.get("year")
            trades = year_data.get("trades", 0)
            win_rate = year_data.get("win_rate", 0)
            net_profit = year_data.get("net_profit", 0)
            sign = "+" if net_profit > 0 else ""
            print(
                f"  {year}年完了: "
                f"取引 {trades}件, 勝率 {win_rate:.1f}%, "
                f"損益 ¥{sign}{net_profit:,.0f}",
                flush=True
            )

        elif event.event_type == EventType.PROGRESS:
            if isinstance(event, ProgressEvent):
                pct_10 = int(event.percentage // 10) * 10
                if pct_10 > self._last_progress_pct:
                    self._last_progress_pct = pct_10
                    print(f"    進捗: {pct_10}%", flush=True)

        elif event.event_type == EventType.INIT_PROGRESS:
            if isinstance(event, InitProgressEvent):
                if event.phase == "tf_loading":
                    print(
                        f"  インジケータ計算: {event.label}"
                        f" ({event.current}/{event.total})",
                        flush=True,
                    )
                elif event.phase == "year_parallel":
                    print(
                        f"  並列処理: {event.current}/{event.total}年完了",
                        flush=True,
                    )
                elif event.phase == "year_row_update":
                    year = int(event.label)
                    pct = (
                        event.current / event.total * 100
                        if event.total > 0 else 0
                    )
                    pct_25 = int(pct // 25) * 25
                    last = self._year_last_pct.get(year, -1)
                    if pct_25 > last:
                        self._year_last_pct[year] = pct_25
                        print(
                            f"  {year}年: "
                            f"{event.current:,}/{event.total:,}足"
                            f" ({pct:.0f}%)",
                            flush=True,
                        )


class ConsoleEventListener(EventListener):
    """コンソール出力リスナー"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._last_progress = -1

    def on_event(self, event: BacktestEvent) -> None:
        """イベントをコンソールに出力"""
        if event.event_type == EventType.BACKTEST_START:
            print(f"\n{'='*60}")
            print(f"バックテスト開始: {event.data.get('start_year')}-"
                  f"{event.data.get('end_year')}")
            print(f"{'='*60}")
        
        elif event.event_type == EventType.BACKTEST_END:
            print(f"\n{'='*60}")
            print("バックテスト完了")
            print(f"{'='*60}\n")
        
        elif event.event_type == EventType.YEAR_START:
            print(f"\n--- {event.data.get('year')}年 開始 ---")
        
        elif event.event_type == EventType.YEAR_END:
            year_data = event.data
            print(f"--- {year_data.get('year')}年 完了 ---")
            print(f"  取引数: {year_data.get('trades', 0)}")
            print(f"  勝率: {year_data.get('win_rate', 0):.1f}%")
            print(f"  純利益: {year_data.get('net_profit', 0):,.0f}")
        
        elif event.event_type == EventType.MONTH_END:
            if self.verbose:
                month_data = event.data
                print(f"  {month_data.get('year')}/{month_data.get('month'):02d}: "
                      f"取引{month_data.get('trades', 0)}件 "
                      f"損益{month_data.get('pnl', 0):+,.0f} "
                      f"({month_data.get('return_pct', 0):+.2f}%)")
        
        elif event.event_type == EventType.PROGRESS:
            if isinstance(event, ProgressEvent):
                progress_int = int(event.percentage // 10) * 10
                if progress_int > self._last_progress:
                    self._last_progress = progress_int
                    eta_str = ""
                    if event.eta_seconds is not None:
                        eta_str = f" (残り {event.eta_seconds:.0f}秒)"
                    print(f"  進捗: {event.percentage:.1f}%{eta_str}")
        
        elif event.event_type == EventType.SIGNAL_GENERATED:
            if self.verbose and isinstance(event, SignalEvent):
                if event.signal_type != "HOLD":
                    print(f"    [{event.timestamp}] "
                          f"シグナル: {event.signal_type} "
                          f"信頼度: {event.confidence:.2f} "
                          f"TF: {','.join(event.aligned_timeframes)}")
        
        elif event.event_type == EventType.POSITION_OPENED:
            if self.verbose and isinstance(event, TradeEvent):
                print(f"    [{event.timestamp}] "
                      f"ポジション開設: {event.direction} "
                      f"@ {event.entry_price:.5f}")
        
        elif event.event_type == EventType.POSITION_CLOSED:
            if self.verbose and isinstance(event, TradeEvent):
                pnl_str = f"{event.profit_loss:+,.0f}" if event.profit_loss else "N/A"
                print(f"    [{event.timestamp}] "
                      f"ポジション決済: {event.exit_reason} "
                      f"損益: {pnl_str}")


class BacktestEventEmitter:
    """バックテストイベントエミッター"""
    
    def __init__(self):
        self._listeners: list[EventListener] = []
        self._callbacks: list[Callable[[BacktestEvent], None]] = []
    
    def add_listener(self, listener: EventListener) -> None:
        """リスナー追加"""
        self._listeners.append(listener)
    
    def remove_listener(self, listener: EventListener) -> None:
        """リスナー削除"""
        if listener in self._listeners:
            self._listeners.remove(listener)
    
    def add_callback(
        self, 
        callback: Callable[[BacktestEvent], None]
    ) -> None:
        """コールバック追加"""
        self._callbacks.append(callback)
    
    def emit(self, event: BacktestEvent) -> None:
        """イベント発行"""
        for listener in self._listeners:
            try:
                listener.on_event(event)
            except Exception as e:
                logger.warning(f"リスナーエラー: {e}")
        
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.warning(f"コールバックエラー: {e}")
    
    def emit_init_progress(
        self,
        phase: str,
        label: str,
        current: int,
        total: int,
    ) -> None:
        """初期化進捗イベント（TFロード・年並列）"""
        self.emit(InitProgressEvent(
            phase=phase,
            label=label,
            current=current,
            total=total,
        ))

    def emit_backtest_start(
        self,
        start_year: int,
        end_year: int,
        config: dict | None = None
    ) -> None:
        """バックテスト開始イベント"""
        self.emit(BacktestEvent(
            event_type=EventType.BACKTEST_START,
            data={
                "start_year": start_year,
                "end_year": end_year,
                "config": config or {},
            }
        ))
    
    def emit_backtest_end(self, results: dict | None = None) -> None:
        """バックテスト終了イベント"""
        self.emit(BacktestEvent(
            event_type=EventType.BACKTEST_END,
            data={"results": results or {}}
        ))
    
    def emit_year_start(self, year: int) -> None:
        """年開始イベント"""
        self.emit(BacktestEvent(
            event_type=EventType.YEAR_START,
            data={"year": year}
        ))
    
    def emit_year_end(self, year_result: dict) -> None:
        """年終了イベント"""
        self.emit(BacktestEvent(
            event_type=EventType.YEAR_END,
            data=year_result
        ))
    
    def emit_month_end(self, month_result: dict) -> None:
        """月終了イベント"""
        self.emit(BacktestEvent(
            event_type=EventType.MONTH_END,
            data=month_result
        ))
    
    def emit_progress(
        self,
        current: int,
        total: int,
        elapsed: float,
        message: str = ""
    ) -> None:
        """進捗イベント"""
        eta = None
        if current > 0 and elapsed > 0:
            eta = (elapsed / current) * (total - current)
        
        self.emit(ProgressEvent(
            current=current,
            total=total,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            message=message
        ))
    
    def emit_signal(
        self,
        signal_type: str,
        symbol: str,
        timeframe: str,
        confidence: float,
        sl_pips: float,
        tp_pips: float,
        rationale: str,
        aligned_timeframes: list[str],
        candle_time: datetime,
        tf_scores: dict[str, dict[str, float]] | None = None,
        filters_applied: list[str] | None = None,
        score_breakdowns: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """シグナル生成イベント"""
        self.emit(SignalEvent(
            timestamp=candle_time,
            signal_type=signal_type,
            symbol=symbol,
            timeframe=timeframe,
            confidence=confidence,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            rationale=rationale,
            aligned_timeframes=aligned_timeframes,
            tf_scores=tf_scores or {},
            filters_applied=filters_applied or [],
            score_breakdowns=score_breakdowns or {},
        ))
    
    def emit_trade_opened(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        volume: float,
        candle_time: datetime,
        trading_mode: str = "",
        market_regime: str = "",
    ) -> None:
        """ポジション開設イベント"""
        self.emit(TradeEvent(
            timestamp=candle_time,
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            volume=volume,
            trading_mode=trading_mode,
            market_regime=market_regime,
        ))
    
    def emit_trade_closed(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        volume: float,
        profit_loss: float,
        exit_reason: str,
        candle_time: datetime,
        opened_at: datetime | None = None,
        close_reason_detail: str = "",
        holding_minutes: float = 0.0,
        pips: float = 0.0,
        trading_mode: str = "",
        market_regime: str = "",
        signal_data: dict | None = None,
        mfe_pips: float = 0.0,
        mae_pips: float = 0.0,
        entry_spread_pips: float = 0.0,
        entry_atr: float = 0.0,
        entry_adx: float = 0.0,
        entry_bb_width: float = 0.0,
        exit_spread_pips: float = 0.0,
        slippage_pips: float = 0.0,
        commission: float = 0.0,
        equity_before: float = 0.0,
        equity_after: float = 0.0,
        dd_pct_at_entry: float = 0.0,
        consecutive_losses: int = 0,
        risk_per_trade_pct: float = 0.0,
        lot: float = 0.0,
        parent_trade_id: str = "",
        position_id: str = "",
        entry_threshold: float = 0.0,
        htf_alignment: float = 0.0,
        penalty_total: float = 0.0,
        penalty_breakdown: dict[str, float] | None = None,
        trend_strength: float = 0.0,
        mfe_r: float = 0.0,
        mae_r: float = 0.0,
        time_to_mfe_minutes: float = 0.0,
        session: str = "",
        strategy_id: str = "",
        trigger_price: float = 0.0,
        fill_price: float = 0.0,
    ) -> None:
        """ポジション決済イベント"""
        event = TradeEvent(
            timestamp=candle_time,
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            volume=volume,
            profit_loss=profit_loss,
            exit_reason=exit_reason,
            opened_at=opened_at,
            close_reason_detail=close_reason_detail,
            holding_minutes=holding_minutes,
            pips=pips,
            trading_mode=trading_mode,
            market_regime=market_regime,
            strategy_id=strategy_id,
            mfe_pips=mfe_pips,
            mae_pips=mae_pips,
            entry_spread_pips=entry_spread_pips,
            entry_atr=entry_atr,
            entry_adx=entry_adx,
            entry_bb_width=entry_bb_width,
            exit_spread_pips=exit_spread_pips,
            slippage_pips=slippage_pips,
            commission=commission,
            equity_before=equity_before,
            equity_after=equity_after,
            dd_pct_at_entry=dd_pct_at_entry,
            consecutive_losses=consecutive_losses,
            risk_per_trade_pct=risk_per_trade_pct,
            lot=lot,
            parent_trade_id=parent_trade_id,
            position_id=position_id,
            entry_threshold=entry_threshold,
            htf_alignment=htf_alignment,
            penalty_total=penalty_total,
            penalty_breakdown=penalty_breakdown or {},
            trend_strength=trend_strength,
            mfe_r=mfe_r,
            mae_r=mae_r,
            time_to_mfe_minutes=time_to_mfe_minutes,
            session=session,
            trigger_price=trigger_price,
            fill_price=fill_price,
        )
        # シグナルデータをevent.dataに格納（リスナーで参照）
        if signal_data:
            event.data["signal_data"] = signal_data
        self.emit(event)
    
    def emit_metrics(
        self,
        balance: float,
        equity: float,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        max_drawdown: float
    ) -> None:
        """メトリクス更新イベント"""
        win_rate = 0.0
        if total_trades > 0:
            win_rate = winning_trades / total_trades * 100
        
        profit_factor = 0.0
        if losing_trades > 0 and winning_trades > 0:
            # 簡易計算
            profit_factor = winning_trades / losing_trades
        
        self.emit(MetricsEvent(
            balance=balance,
            equity=equity,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            max_drawdown=max_drawdown
        ))
