"""ファイル出力イベントリスナー"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from autotrader.backtest.events import (
    BacktestEvent,
    EventListener,
    EventType,
    SignalEvent,
    TradeEvent,
)
from autotrader.decision.unified.mode_selector import UNIVERSAL_MODE

# CSV出力カラム定義
CSV_COLUMNS = [
    "trade_id",
    "symbol",
    "direction",
    "entry_time",
    "exit_time",
    "holding_minutes",
    "entry_price",
    "exit_price",
    "trigger_price",
    "fill_price",
    "pips",
    "profit_loss",
    "exit_reason",
    "regime",
    "mode",
    "strategy_id",
    "confidence",
    "consensus_score",
    "sl_pips",
    "tp_pips",
    # スコア内訳
    "score_trend",
    "score_adx",
    "score_rsi",
    "score_macd_slope",
    "score_divergence",
    "score_ema_cross",
    "score_stochastic",
    "score_htf",
    # MFE/MAE
    "mfe_pips",
    "mae_pips",
    # A) 執行・コスト系
    "entry_spread_pips",
    "exit_spread_pips",
    "slippage_pips",
    "commission",
    # エントリー時市場メトリクス
    "entry_atr",
    "entry_adx",
    "entry_bb_width",
    # B) リスク管理・状態系
    "equity_before",
    "equity_after",
    "dd_pct_at_entry",
    "consecutive_losses",
    "risk_per_trade_pct",
    "lot",
    # C) ログ品質強化
    "parent_trade_id",
    "position_id",
    "entry_threshold",
    "htf_alignment",
    "penalty_total",
    "penalty_breakdown",
    "trend_strength",
    "mfe_r",
    "mae_r",
    "time_to_mfe_minutes",
    "session",
    # E) Exit詳細診断
    "exit_reason_detail",
    "stag_minutes_used",
    "stag_mfe_r_used",
    # 理由
    "rationale",
]

# STAGNATION reason文字列からminutes/MFE_Rを抽出する正規表現
_RE_STAG_MINUTES = re.compile(
    r"(\d+(?:\.\d+)?)分(?:経過)?",
)
_RE_STAG_MFE_R = re.compile(
    r"MFE=(-?\d+(?:\.\d+)?)R",
)


class FileEventListener(EventListener):
    """ファイル出力リスナー

    バックテストイベントをサマリーログ + 詳細トレードCSVに出力。

    Attributes:
        log_dir: ログディレクトリパス
        summary_file: サマリーログファイルパス
        trades_file: 詳細トレードCSVファイルパス
        verbose: 詳細出力フラグ
    """

    def __init__(
        self,
        log_dir: str | Path | None = None,
        verbose: bool = True,
    ) -> None:
        """初期化

        Args:
            log_dir: ログ出力先ディレクトリ
            verbose: 詳細出力（月別統計等）
        """
        if log_dir is None:
            from autotrader.config.paths import get_log_dir

            log_dir = get_log_dir()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.summary_file = self.log_dir / f"summary_{timestamp}.log"
        self.trades_file = self.log_dir / f"trades_{timestamp}.csv"

        # 後方互換性のためlog_fileも設定
        self.log_file = self.summary_file

        # サマリーファイル初期化
        self._write_summary(f"# バックテストログ - {timestamp}")
        self._write_summary("")

        # シグナル→トレード紐付け用の内部状態
        self._pending_signal: dict | None = None
        self._trade_rows: list[dict] = []

        # 統計カウンター
        self._exit_stats: dict[str, dict[str, float]] = {}
        self._mode_stats: dict[str, dict[str, float]] = {}
        self._regime_stats: dict[str, dict[str, float]] = {}
        # Exit×Regime×Modeクロス集計
        self._cross_stats: dict[
            str, dict[str, float]
        ] = {}

    def _write_summary(self, line: str) -> None:
        """サマリーファイルに1行書き込む

        Args:
            line: 出力行
        """
        with open(self.summary_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _format_timestamp(self, dt: datetime) -> str:
        """タイムスタンプをフォーマット

        Args:
            dt: datetime

        Returns:
            フォーマット済み文字列
        """
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _update_category_stats(
        self,
        stats: dict[str, dict[str, float]],
        key: str,
        pnl: float,
    ) -> None:
        """カテゴリ別統計を更新

        Args:
            stats: 統計辞書
            key: カテゴリキー
            pnl: 損益
        """
        if not key:
            key = "UNKNOWN"
        if key not in stats:
            stats[key] = {
                "trades": 0, "wins": 0,
                "total_pnl": 0.0,
            }
        stats[key]["trades"] += 1
        if pnl > 0:
            stats[key]["wins"] += 1
        stats[key]["total_pnl"] += pnl

    def on_event(self, event: BacktestEvent) -> None:
        """イベント処理

        Args:
            event: バックテストイベント
        """
        if event.event_type == EventType.BACKTEST_START:
            self._handle_backtest_start(event)
        elif event.event_type == EventType.BACKTEST_END:
            self._handle_backtest_end(event)
        elif event.event_type == EventType.YEAR_START:
            self._handle_year_start(event)
        elif event.event_type == EventType.YEAR_END:
            self._handle_year_end(event)
        elif event.event_type == EventType.MONTH_END:
            self._handle_month_end(event)
        elif event.event_type == EventType.PROGRESS:
            self._handle_progress(event)
        elif event.event_type == EventType.SIGNAL_GENERATED:
            self._handle_signal(event)
        elif event.event_type == EventType.POSITION_OPENED:
            self._handle_position_opened(event)
        elif event.event_type == EventType.POSITION_CLOSED:
            self._handle_position_closed(event)
        elif event.event_type == EventType.METRICS_UPDATE:
            self._handle_metrics(event)

    def _handle_backtest_start(self, event: BacktestEvent) -> None:
        """バックテスト開始イベント"""
        start_year = event.data.get("start_year", "?")
        end_year = event.data.get("end_year", "?")
        self._write_summary("=" * 60)
        self._write_summary(
            f"バックテスト開始: {start_year}-{end_year}"
        )
        self._write_summary(
            f"開始時刻: {self._format_timestamp(event.timestamp)}"
        )
        self._write_summary("=" * 60)
        self._write_summary("")

    def _handle_backtest_end(self, event: BacktestEvent) -> None:
        """バックテスト終了イベント"""
        # CSVファイル書き出し
        self._write_trades_csv()

        # カテゴリ別統計出力
        self._write_summary("")
        self._write_summary("=" * 60)
        self._write_summary("カテゴリ別統計")
        self._write_summary("=" * 60)
        self._write_summary("")

        self._write_category_stats(
            "Exit理由別統計", self._exit_stats
        )
        self._write_category_stats(
            "戦略モード別統計", self._mode_stats
        )
        self._write_category_stats(
            "レジーム別統計", self._regime_stats
        )

        # Exit×Regime×Modeクロス集計
        self._write_cross_stats()

        # UNIVERSAL×RANGEサマリー
        self._write_range_day_summary()

        self._write_summary("")
        self._write_summary("=" * 60)
        cancelled = event.data.get("cancelled", False)
        if cancelled:
            self._write_summary("バックテスト中断")
        else:
            self._write_summary("バックテスト完了")
        self._write_summary(
            f"終了時刻: {self._format_timestamp(event.timestamp)}"
        )
        self._write_summary(
            f"詳細トレードCSV: {self.trades_file.name}"
        )
        self._write_summary("=" * 60)

    def _write_category_stats(
        self,
        title: str,
        stats: dict[str, dict[str, float]],
    ) -> None:
        """カテゴリ別統計をサマリーに出力

        Args:
            title: セクションタイトル
            stats: 統計辞書
        """
        if not stats:
            return

        self._write_summary(f"--- {title} ---")
        for key, s in sorted(stats.items()):
            trades = int(s["trades"])
            wins = int(s["wins"])
            wr = (wins / trades * 100) if trades > 0 else 0
            pnl = s["total_pnl"]
            self._write_summary(
                f"  {key}: "
                f"取引{trades}件, 勝率{wr:.1f}%, "
                f"損益{pnl:+,.0f}"
            )
        self._write_summary("")

    def _write_cross_stats(self) -> None:
        """Exit理由×Regime×Modeクロス集計を出力"""
        if not self._cross_stats:
            return

        self._write_summary(
            "--- Exit×Regime×Mode クロス集計 ---"
        )
        self._write_summary(
            f"{'Exit理由':<20} {'Regime':<12} "
            f"{'Mode':<12} {'取引':>5} "
            f"{'勝率':>6} {'損益':>12}"
        )
        self._write_summary("-" * 70)

        for key in sorted(self._cross_stats.keys()):
            s = self._cross_stats[key]
            parts = key.split("|")
            exit_r = parts[0] if len(parts) > 0 else ""
            regime = parts[1] if len(parts) > 1 else ""
            mode = parts[2] if len(parts) > 2 else ""
            trades = int(s["trades"])
            wins = int(s["wins"])
            wr = (wins / trades * 100) if trades > 0 else 0
            pnl = s["total_pnl"]
            self._write_summary(
                f"{exit_r:<20} {regime:<12} "
                f"{mode:<12} {trades:>5} "
                f"{wr:>5.1f}% {pnl:>+12,.0f}"
            )
        self._write_summary("")

    def _write_range_day_summary(self) -> None:
        """UNIVERSAL×RANGEサマリー"""
        if not self._cross_stats:
            return

        # RANGE×UNIVERSALのキーを抽出
        range_day: dict[str, dict[str, float]] = {}
        for key, s in self._cross_stats.items():
            parts = key.split("|")
            if len(parts) < 3:
                continue
            regime = parts[1].strip()
            mode = parts[2].strip()
            if regime == "RANGE" and mode == UNIVERSAL_MODE:
                exit_r = parts[0].strip()
                range_day[exit_r] = s

        if not range_day:
            return

        self._write_summary(
            "--- UNIVERSAL×RANGE サマリー ---"
        )
        self._write_summary(
            f"{'Exit理由':<20} {'取引':>5} "
            f"{'勝率':>6} {'損益':>12}"
        )
        self._write_summary("-" * 50)

        total_trades = 0
        total_pnl = 0.0
        for exit_r in sorted(range_day.keys()):
            s = range_day[exit_r]
            trades = int(s["trades"])
            wins = int(s["wins"])
            wr = (wins / trades * 100) if trades > 0 else 0
            pnl = s["total_pnl"]
            total_trades += trades
            total_pnl += pnl
            self._write_summary(
                f"{exit_r:<20} {trades:>5} "
                f"{wr:>5.1f}% {pnl:>+12,.0f}"
            )

        self._write_summary("-" * 50)
        self._write_summary(
            f"{'合計':<20} {total_trades:>5} "
            f"{'':>6} {total_pnl:>+12,.0f}"
        )
        self._write_summary("")

    def _write_trades_csv(self) -> None:
        """蓄積したトレードデータをCSVに書き出し"""
        if not self._trade_rows:
            return

        with open(
            self.trades_file, "w",
            newline="", encoding="utf-8",
        ) as f:
            writer = csv.DictWriter(
                f, fieldnames=CSV_COLUMNS,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(self._trade_rows)

    def _handle_year_start(self, event: BacktestEvent) -> None:
        """年開始イベント"""
        year = event.data.get("year", "?")
        self._write_summary("")
        self._write_summary(f"--- {year}年 開始 ---")

    def _handle_year_end(self, event: BacktestEvent) -> None:
        """年終了イベント"""
        year_data = event.data
        year = year_data.get("year", "?")
        trades = year_data.get("trades", 0)
        win_rate = year_data.get("win_rate", 0)
        net_profit = year_data.get("net_profit", 0)
        max_dd = year_data.get("max_drawdown", 0)
        sharpe = year_data.get("sharpe", 0)

        self._write_summary(f"--- {year}年 完了 ---")
        self._write_summary(f"  取引数: {trades}")
        self._write_summary(f"  勝率: {win_rate:.1f}%")
        self._write_summary(f"  純利益: {net_profit:,.0f}")
        self._write_summary(f"  最大DD: {max_dd:.2f}%")
        self._write_summary(f"  シャープ: {sharpe:.3f}")
        self._write_summary("")

    def _handle_month_end(self, event: BacktestEvent) -> None:
        """月終了イベント"""
        if not self.verbose:
            return

        month_data = event.data
        year = month_data.get("year", 0)
        month = month_data.get("month", 0)
        if not isinstance(year, int):
            year = 0
        if not isinstance(month, int):
            month = 0
        trades = month_data.get("trades", 0)
        pnl = month_data.get("pnl", 0)
        return_pct = month_data.get("return_pct", 0)

        self._write_summary(
            f"  {year}/{month:02d}: "
            f"取引{trades}件 "
            f"損益{pnl:+,.0f} "
            f"({return_pct:+.2f}%)"
        )

    def _handle_progress(self, event: BacktestEvent) -> None:
        """進捗イベント（ファイルには出力しない）"""
        pass

    def _handle_signal(self, event: BacktestEvent) -> None:
        """シグナル生成イベント"""
        if not isinstance(event, SignalEvent):
            return

        if event.signal_type == "HOLD":
            return

        # 最新のシグナルデータを一時保存
        self._pending_signal = {
            "confidence": event.confidence,
            "sl_pips": event.sl_pips,
            "tp_pips": event.tp_pips,
            "rationale": event.rationale,
            "score_breakdowns": event.score_breakdowns,
            "consensus_score": 0.0,
        }

    def _handle_position_opened(self, event: BacktestEvent) -> None:
        """ポジション開設イベント"""
        pass

    def _handle_position_closed(self, event: BacktestEvent) -> None:
        """ポジション決済イベント"""
        if not isinstance(event, TradeEvent):
            return

        pnl = event.profit_loss if event.profit_loss else 0
        exit_reason = event.exit_reason or "UNKNOWN"
        mode = event.trading_mode or ""
        regime = event.market_regime or ""

        # 統計カウンター更新
        self._update_category_stats(
            self._exit_stats, exit_reason, pnl
        )
        self._update_category_stats(
            self._mode_stats, mode, pnl
        )
        self._update_category_stats(
            self._regime_stats, regime, pnl
        )
        # Exit×Regime×Modeクロス集計
        cross_key = f"{exit_reason}|{regime}|{mode}"
        self._update_category_stats(
            self._cross_stats, cross_key, pnl
        )

        # CSV行構築
        row: dict[str, object] = {
            "trade_id": event.trade_id,
            "symbol": event.symbol or "",
            "direction": event.direction,
            "entry_time": (
                self._format_timestamp(event.opened_at)
                if event.opened_at else ""
            ),
            "exit_time": self._format_timestamp(
                event.timestamp
            ),
            "holding_minutes": f"{event.holding_minutes:.0f}",
            "entry_price": f"{event.entry_price:.5f}",
            "exit_price": (
                f"{event.exit_price:.5f}"
                if event.exit_price else ""
            ),
            "trigger_price": (
                f"{event.trigger_price:.5f}"
                if event.trigger_price else ""
            ),
            "fill_price": (
                f"{event.fill_price:.5f}"
                if event.fill_price else ""
            ),
            "pips": f"{event.pips:.1f}",
            "profit_loss": f"{pnl:.0f}",
            "exit_reason": exit_reason,
            "regime": regime,
            "mode": mode,
            "strategy_id": event.strategy_id or "",
        }

        # signal_dataからスコア内訳を取得
        sig_data = event.data.get("signal_data", {})
        breakdowns = sig_data.get("score_breakdowns", {})
        # sig_data空時はTradeEventの直接フィールドをフォールバック
        confidence = sig_data.get(
            "confidence", event.data.get(
                "confidence", 0.0,
            ),
        )
        consensus_score = sig_data.get(
            "consensus_score", 0.0,
        )
        sl_pips = sig_data.get("sl_pips", 0.0)
        tp_pips = sig_data.get("tp_pips", 0.0)
        rationale = sig_data.get("rationale", "")

        row["confidence"] = f"{confidence:.3f}"
        row["consensus_score"] = (
            f"{consensus_score:.2f}"
            if consensus_score is not None else ""
        )
        row["sl_pips"] = f"{sl_pips:.1f}"
        row["tp_pips"] = f"{tp_pips:.1f}"
        row["rationale"] = rationale

        # primary_tfのスコア内訳を優先使用
        primary_tf = sig_data.get("primary_tf", "")
        bd = breakdowns.get(primary_tf, {})
        if not bd and breakdowns:
            bd = next(iter(breakdowns.values()), {})

        row["score_trend"] = f"{bd.get('trend', 0):.1f}"
        row["score_adx"] = f"{bd.get('adx', 0):.1f}"
        row["score_rsi"] = f"{bd.get('rsi', 0):.1f}"
        row["score_macd_slope"] = (
            f"{bd.get('macd_slope', 0):.1f}"
        )
        row["score_divergence"] = (
            f"{bd.get('divergence', 0):.1f}"
        )
        row["score_ema_cross"] = (
            f"{bd.get('ema_cross', 0):.1f}"
        )
        row["score_stochastic"] = (
            f"{bd.get('stochastic', 0):.1f}"
        )
        row["score_htf"] = f"{bd.get('htf', 0):.1f}"

        # MFE/MAE
        row["mfe_pips"] = f"{event.mfe_pips:.1f}"
        row["mae_pips"] = f"{event.mae_pips:.1f}"

        # A) 執行・コスト系
        row["entry_spread_pips"] = (
            f"{event.entry_spread_pips:.2f}"
        )
        row["exit_spread_pips"] = (
            f"{event.exit_spread_pips:.2f}"
        )
        row["slippage_pips"] = (
            f"{event.slippage_pips:.2f}"
        )
        row["commission"] = f"{event.commission:.0f}"

        # エントリー時市場メトリクス
        row["entry_atr"] = f"{event.entry_atr:.5f}"
        row["entry_adx"] = f"{event.entry_adx:.1f}"
        row["entry_bb_width"] = (
            f"{event.entry_bb_width:.5f}"
        )

        # B) リスク管理・状態系
        row["equity_before"] = (
            f"{event.equity_before:.0f}"
        )
        row["equity_after"] = (
            f"{event.equity_after:.0f}"
        )
        row["dd_pct_at_entry"] = (
            f"{event.dd_pct_at_entry:.2f}"
        )
        row["consecutive_losses"] = (
            str(event.consecutive_losses)
        )
        row["risk_per_trade_pct"] = (
            f"{event.risk_per_trade_pct:.3f}"
        )
        row["lot"] = f"{event.lot:.2f}"

        # C) ログ品質強化
        row["parent_trade_id"] = (
            event.parent_trade_id or ""
        )
        row["position_id"] = (
            event.position_id or ""
        )
        row["entry_threshold"] = (
            f"{event.entry_threshold:.2f}"
        )
        row["htf_alignment"] = (
            f"{event.htf_alignment:.3f}"
        )
        row["penalty_total"] = (
            f"{event.penalty_total:.3f}"
        )
        # penalty_breakdownをJSON文字列化
        row["penalty_breakdown"] = (
            json.dumps(event.penalty_breakdown)
            if event.penalty_breakdown
            else "{}"
        )
        row["trend_strength"] = (
            f"{event.trend_strength:.3f}"
        )
        row["mfe_r"] = f"{event.mfe_r:.2f}"
        row["mae_r"] = f"{event.mae_r:.2f}"
        row["time_to_mfe_minutes"] = (
            f"{event.time_to_mfe_minutes:.0f}"
        )
        row["session"] = event.session

        # E) Exit詳細診断
        _detail = event.exit_reason_detail or ""
        row["exit_reason_detail"] = _detail
        # STAGNATION系のreason文字列からパラメータ抽出
        _m_min = _RE_STAG_MINUTES.search(_detail)
        _m_mfe = _RE_STAG_MFE_R.search(_detail)
        row["stag_minutes_used"] = (
            _m_min.group(1) if _m_min else ""
        )
        row["stag_mfe_r_used"] = (
            _m_mfe.group(1) if _m_mfe else ""
        )

        self._trade_rows.append(row)

    def _handle_metrics(self, event: BacktestEvent) -> None:
        """メトリクス更新イベント（ファイルには詳細出力しない）"""
        pass

    def get_log_path(self) -> Path:
        """ログファイルパスを取得

        Returns:
            ログファイルの絶対パス
        """
        return self.summary_file.absolute()

    def get_trades_path(self) -> Path:
        """トレードCSVファイルパスを取得

        Returns:
            CSVファイルの絶対パス
        """
        return self.trades_file.absolute()

    def merge_worker_data(
        self,
        trade_rows: list[dict],
        stats: dict[str, dict[str, dict[str, float]]],
    ) -> None:
        """ワーカープロセスのトレードデータをマージ

        ProcessPoolExecutor ワーカーが収集したトレード行と
        統計データをメインプロセスのリスナーにマージする。

        Args:
            trade_rows: ワーカー収集トレード行リスト
            stats: ワーカー収集統計辞書
                   (exit/mode/regime/cross の各キーを持つ)
        """
        self._trade_rows.extend(trade_rows)
        stat_map = {
            "exit": self._exit_stats,
            "mode": self._mode_stats,
            "regime": self._regime_stats,
            "cross": self._cross_stats,
        }
        for stat_key, stat_dict in stats.items():
            target = stat_map.get(stat_key)
            if target is None:
                continue
            for key, entry in stat_dict.items():
                self._merge_stat_entry(
                    target, key, entry
                )

    def _merge_stat_entry(
        self,
        stats: dict[str, dict[str, float]],
        key: str,
        entry: dict[str, float],
    ) -> None:
        """統計エントリーをマージ

        Args:
            stats: 対象統計辞書
            key: カテゴリキー
            entry: マージするエントリー
        """
        if key not in stats:
            stats[key] = {
                "trades": 0, "wins": 0,
                "total_pnl": 0.0,
            }
        stats[key]["trades"] += entry.get("trades", 0)
        stats[key]["wins"] += entry.get("wins", 0)
        stats[key]["total_pnl"] += entry.get(
            "total_pnl", 0.0
        )

    def sort_trade_rows(self) -> None:
        """トレード行を時系列順にソート"""
        self._trade_rows.sort(
            key=lambda r: r.get("entry_time", "")
        )


class TradeRowCollector(EventListener):
    """ワーカープロセス用トレードデータ収集リスナー

    ProcessPoolExecutor のサブプロセスで動作し、
    POSITION_CLOSED イベントからトレード行と統計を収集する。
    メインプロセスへの返却のため pickle 可能な設計。

    Attributes:
        _trade_rows: 収集したトレード行データ
        _exit_stats: Exit理由別統計
        _mode_stats: 戦略モード別統計
        _regime_stats: レジーム別統計
        _cross_stats: Exit×Regime×Modeクロス集計
    """

    def __init__(self) -> None:
        """初期化"""
        self._trade_rows: list[dict] = []
        self._exit_stats: dict[str, dict[str, float]] = {}
        self._mode_stats: dict[str, dict[str, float]] = {}
        self._regime_stats: dict[str, dict[str, float]] = {}
        self._cross_stats: dict[str, dict[str, float]] = {}

    def _update_stats(
        self,
        stats: dict[str, dict[str, float]],
        key: str,
        pnl: float,
    ) -> None:
        """カテゴリ統計を更新

        Args:
            stats: 統計辞書
            key: カテゴリキー
            pnl: 損益
        """
        if not key:
            key = "UNKNOWN"
        if key not in stats:
            stats[key] = {
                "trades": 0, "wins": 0,
                "total_pnl": 0.0,
            }
        stats[key]["trades"] += 1
        if pnl > 0:
            stats[key]["wins"] += 1
        stats[key]["total_pnl"] += pnl

    def on_event(self, event: BacktestEvent) -> None:
        """イベント処理（POSITION_CLOSEDのみ収集）

        Args:
            event: バックテストイベント
        """
        if event.event_type != EventType.POSITION_CLOSED:
            return
        if not isinstance(event, TradeEvent):
            return
        self._collect(event)

    def _collect(self, event: TradeEvent) -> None:
        """トレードデータを収集

        Args:
            event: トレードイベント
        """
        pnl = event.profit_loss if event.profit_loss else 0
        exit_reason = event.exit_reason or "UNKNOWN"
        mode = event.trading_mode or ""
        regime = event.market_regime or ""

        self._update_stats(
            self._exit_stats, exit_reason, pnl
        )
        self._update_stats(self._mode_stats, mode, pnl)
        self._update_stats(
            self._regime_stats, regime, pnl
        )
        cross_key = f"{exit_reason}|{regime}|{mode}"
        self._update_stats(
            self._cross_stats, cross_key, pnl
        )

        # CSV行構築
        def _fmt(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        row: dict[str, object] = {
            "trade_id": event.trade_id,
            "symbol": event.symbol or "",
            "direction": event.direction,
            "entry_time": (
                _fmt(event.opened_at)
                if event.opened_at else ""
            ),
            "exit_time": _fmt(event.timestamp),
            "holding_minutes": (
                f"{event.holding_minutes:.0f}"
            ),
            "entry_price": f"{event.entry_price:.5f}",
            "exit_price": (
                f"{event.exit_price:.5f}"
                if event.exit_price else ""
            ),
            "trigger_price": (
                f"{event.trigger_price:.5f}"
                if event.trigger_price else ""
            ),
            "fill_price": (
                f"{event.fill_price:.5f}"
                if event.fill_price else ""
            ),
            "pips": f"{event.pips:.1f}",
            "profit_loss": f"{pnl:.0f}",
            "exit_reason": exit_reason,
            "regime": regime,
            "mode": mode,
            "strategy_id": event.strategy_id or "",
        }

        sig_data = event.data.get("signal_data", {})
        breakdowns = sig_data.get("score_breakdowns", {})
        confidence = sig_data.get(
            "confidence",
            event.data.get("confidence", 0.0),
        )
        consensus_score = sig_data.get(
            "consensus_score", 0.0
        )
        sl_pips = sig_data.get("sl_pips", 0.0)
        tp_pips = sig_data.get("tp_pips", 0.0)
        rationale = sig_data.get("rationale", "")

        row["confidence"] = f"{confidence:.3f}"
        row["consensus_score"] = (
            f"{consensus_score:.2f}"
            if consensus_score is not None else ""
        )
        row["sl_pips"] = f"{sl_pips:.1f}"
        row["tp_pips"] = f"{tp_pips:.1f}"
        row["rationale"] = rationale

        primary_tf = sig_data.get("primary_tf", "")
        bd = breakdowns.get(primary_tf, {})
        if not bd and breakdowns:
            bd = next(iter(breakdowns.values()), {})

        row["score_trend"] = (
            f"{bd.get('trend', 0):.1f}"
        )
        row["score_adx"] = f"{bd.get('adx', 0):.1f}"
        row["score_rsi"] = f"{bd.get('rsi', 0):.1f}"
        row["score_macd_slope"] = (
            f"{bd.get('macd_slope', 0):.1f}"
        )
        row["score_divergence"] = (
            f"{bd.get('divergence', 0):.1f}"
        )
        row["score_ema_cross"] = (
            f"{bd.get('ema_cross', 0):.1f}"
        )
        row["score_stochastic"] = (
            f"{bd.get('stochastic', 0):.1f}"
        )
        row["score_htf"] = f"{bd.get('htf', 0):.1f}"

        row["mfe_pips"] = f"{event.mfe_pips:.1f}"
        row["mae_pips"] = f"{event.mae_pips:.1f}"

        row["entry_spread_pips"] = (
            f"{event.entry_spread_pips:.2f}"
        )
        row["exit_spread_pips"] = (
            f"{event.exit_spread_pips:.2f}"
        )
        row["slippage_pips"] = (
            f"{event.slippage_pips:.2f}"
        )
        row["commission"] = f"{event.commission:.0f}"

        row["entry_atr"] = f"{event.entry_atr:.5f}"
        row["entry_adx"] = f"{event.entry_adx:.1f}"
        row["entry_bb_width"] = (
            f"{event.entry_bb_width:.5f}"
        )

        row["equity_before"] = (
            f"{event.equity_before:.0f}"
        )
        row["equity_after"] = (
            f"{event.equity_after:.0f}"
        )
        row["dd_pct_at_entry"] = (
            f"{event.dd_pct_at_entry:.2f}"
        )
        row["consecutive_losses"] = (
            str(event.consecutive_losses)
        )
        row["risk_per_trade_pct"] = (
            f"{event.risk_per_trade_pct:.3f}"
        )
        row["lot"] = f"{event.lot:.2f}"

        row["parent_trade_id"] = (
            event.parent_trade_id or ""
        )
        row["position_id"] = (
            event.position_id or ""
        )
        row["entry_threshold"] = (
            f"{event.entry_threshold:.2f}"
        )
        row["htf_alignment"] = (
            f"{event.htf_alignment:.3f}"
        )
        row["penalty_total"] = (
            f"{event.penalty_total:.3f}"
        )
        row["penalty_breakdown"] = (
            json.dumps(event.penalty_breakdown)
            if event.penalty_breakdown else "{}"
        )
        row["trend_strength"] = (
            f"{event.trend_strength:.3f}"
        )
        row["mfe_r"] = f"{event.mfe_r:.2f}"
        row["mae_r"] = f"{event.mae_r:.2f}"
        row["time_to_mfe_minutes"] = (
            f"{event.time_to_mfe_minutes:.0f}"
        )
        row["session"] = event.session

        # E) Exit詳細診断
        _detail = event.exit_reason_detail or ""
        row["exit_reason_detail"] = _detail
        _m_min = _RE_STAG_MINUTES.search(_detail)
        _m_mfe = _RE_STAG_MFE_R.search(_detail)
        row["stag_minutes_used"] = (
            _m_min.group(1) if _m_min else ""
        )
        row["stag_mfe_r_used"] = (
            _m_mfe.group(1) if _m_mfe else ""
        )

        self._trade_rows.append(row)

    def get_stats(
        self,
    ) -> dict[str, dict[str, dict[str, float]]]:
        """収集した統計データを返す

        Returns:
            統計データの辞書（exit/mode/regime/cross）
        """
        return {
            "exit": self._exit_stats,
            "mode": self._mode_stats,
            "regime": self._regime_stats,
            "cross": self._cross_stats,
        }
