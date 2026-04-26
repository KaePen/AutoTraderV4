"""月単位バックテスト実行エンジン

月別Parquetキャッシュを読み込み、月単位でバックテストを実行する。
チェックポイント機構により途中停止→再開が可能。

Usage:
    from autotrader.backtest.day_runner import MonthRunner, MonthRunnerConfig
    runner = MonthRunner(config)
    result = runner.run()
"""

from __future__ import annotations

import json
import logging
import pickle
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from autotrader.backtest.candle_arrays import CandleArrays
from autotrader.backtest.data_pipeline import (
    list_cached_months,
    load_monthly_cache,
)
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.position_event_logger import (
    PositionEventLogger,
)
from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
from autotrader.backtest.tick_simulator import check_tick_exit
from autotrader.core.entities import Signal
from autotrader.core.enums import ExitReason, SignalType, Timeframe

logger = logging.getLogger(__name__)


@dataclass
class MonthRunnerConfig:
    """月単位バックテスト設定

    Attributes:
        symbol: 通貨ペア
        start_year: 開始年
        start_month: 開始月（1-12）
        end_year: 終了年
        end_month: 終了月（1-12、この月を含む）
        base_timeframe: シグナル生成の基準時間足
        data_dir: データディレクトリ
        checkpoint_dir: チェックポイント保存先
        job_id: ジョブ識別子
        use_tick_exit: ティックベースSL/TP判定を使用
    """

    symbol: str = "USDJPY"
    start_year: int = 2024
    start_month: int = 1
    end_year: int = 2024
    end_month: int = 12
    base_timeframe: str = "M15"
    data_dir: Path | None = None
    checkpoint_dir: Path | None = None
    job_id: str = "default"
    use_tick_exit: bool = False


@dataclass
class MonthRunnerResult:
    """月単位バックテスト結果"""

    symbol: str
    start_month: str  # "YYYY-MM"
    end_month: str
    total_months: int
    processed_months: int
    trades: int
    win_rate: float
    profit_factor: float
    net_profit: float
    max_drawdown: float
    sharpe: float
    events_skipped: int
    resumed_from: str | None = None  # "YYYY-MM"


class MonthRunner:
    """月単位でバックテストを実行し、チェックポイントを管理

    月別Parquetキャッシュから1ヶ月ずつデータを読み込み処理する。
    チェックポイントにより途中停止→再開が可能。
    """

    def __init__(
        self,
        config: MonthRunnerConfig,
        bot_config: Any = None,
        sim_config: SimulatorConfig | None = None,
    ) -> None:
        self._config = config
        self._bot_config = bot_config
        self._sim_config = sim_config or SimulatorConfig.from_preset(
            config.symbol,
        )

        if config.data_dir is None:
            from autotrader.config.paths import get_data_dir
            self._data_dir = Path(get_data_dir())
        else:
            self._data_dir = config.data_dir

        if config.checkpoint_dir is None:
            from autotrader.config.paths import get_backtest_dir
            self._checkpoint_dir = (
                Path(get_backtest_dir())
                / "checkpoints"
                / config.job_id
            )
        else:
            self._checkpoint_dir = config.checkpoint_dir / config.job_id

        self._events_skipped = 0

    def run(
        self,
        resume: bool = True,
        fundamental_provider: Any = None,
    ) -> MonthRunnerResult:
        """月単位バックテストを実行"""
        from autotrader.decision.unified import (
            UnifiedBotConfig,
            UnifiedTradeBot,
        )

        cfg = self._config
        bot_config = self._bot_config or UnifiedBotConfig()

        # キャッシュ済み月一覧を取得
        cached_months = list_cached_months(
            cfg.symbol, cfg.base_timeframe, self._data_dir,
        )
        if not cached_months:
            raise ValueError(
                f"{cfg.symbol} {cfg.base_timeframe}: "
                f"月別キャッシュなし。先に data_pipeline prepare-ohlcv を実行してください"
            )

        # 対象月をフィルタ
        start_ym = (cfg.start_year, cfg.start_month)
        end_ym = (cfg.end_year, cfg.end_month)
        target_months = [
            ym for ym in cached_months
            if start_ym <= ym <= end_ym
        ]
        if not target_months:
            raise ValueError(
                f"{cfg.symbol}: 対象期間 {cfg.start_year}-{cfg.start_month:02d}"
                f"～{cfg.end_year}-{cfg.end_month:02d} にキャッシュなし"
            )

        # チェックポイント復元
        resumed_from: tuple[int, int] | None = None
        simulator = TradeSimulator(config=self._sim_config)
        bot = UnifiedTradeBot(bot_config)
        bot.state = bot.state.with_initial_equity(
            self._sim_config.initial_balance,
        )

        if resume:
            checkpoint = self._load_checkpoint()
            if checkpoint is not None:
                last_ym, sim_state, bot_state = checkpoint
                simulator.state = sim_state
                if bot_state is not None:
                    bot = bot_state
                resumed_from = last_ym
                target_months = [
                    ym for ym in target_months if ym > last_ym
                ]
                logger.info(
                    "チェックポイントから再開: %d-%02d (%dヶ月残り)",
                    last_ym[0], last_ym[1], len(target_months),
                )

        if not target_months:
            logger.info("処理対象月なし（全月完了済み）")
            return self._build_result(
                simulator, cfg, 0, len(cached_months), resumed_from,
            )

        # ポジションイベントロガー
        pos_logger = PositionEventLogger()
        simulator.set_position_event_logger(pos_logger)

        # ティックキャッシュの有無を確認
        tick_months: set[tuple[int, int]] = set()
        if cfg.use_tick_exit:
            tick_months = set(
                list_cached_months(cfg.symbol, "ticks", self._data_dir)
            )
            if tick_months:
                logger.info("ティックデータ: %dヶ月分利用可能", len(tick_months))

        # メインループ: 月単位処理
        total_start = time.time()
        processed = 0
        total_target = len(target_months)

        for month_idx, (year, month) in enumerate(target_months):
            month_start = time.time()

            # 月別キャッシュ読み込み
            month_df = load_monthly_cache(
                cfg.symbol, cfg.base_timeframe,
                year, month, self._data_dir,
            )
            if month_df is None or month_df.empty:
                continue

            # ティックデータ読み込み
            tick_df: pd.DataFrame | None = None
            if cfg.use_tick_exit and (year, month) in tick_months:
                tick_df = load_monthly_cache(
                    cfg.symbol, "ticks",
                    year, month, self._data_dir,
                )

            # 1ヶ月分の処理
            self._process_month(
                month_df=month_df,
                tick_df=tick_df,
                simulator=simulator,
                bot=bot,
                symbol=cfg.symbol,
                tf=Timeframe(cfg.base_timeframe),
                fundamental_provider=fundamental_provider,
            )

            processed += 1
            elapsed = time.time() - month_start

            # チェックポイント保存（毎月）
            self._save_checkpoint((year, month), simulator, bot)

            logger.info(
                "[%d/%d] %d-%02d 完了: %d足処理 (%.1f秒)",
                processed, total_target, year, month,
                len(month_df), elapsed,
            )

        total_elapsed = time.time() - total_start
        logger.info(
            "=== MonthRunner完了: %s %dヶ月処理 (%.1f秒) ===",
            cfg.symbol, processed, total_elapsed,
        )

        return self._build_result(
            simulator, cfg, processed, total_target, resumed_from,
        )

    def _process_month(
        self,
        month_df: pd.DataFrame,
        tick_df: pd.DataFrame | None,
        simulator: TradeSimulator,
        bot: Any,
        symbol: str,
        tf: Timeframe,
        fundamental_provider: Any | None,
    ) -> None:
        """1ヶ月分のバックテスト処理"""
        import datetime as _dt

        arrays = CandleArrays.from_dataframe(month_df.reset_index())

        for idx in range(arrays.n_rows):
            candle = arrays.get_candle(idx, symbol, tf)
            candle_time = arrays.get_time(idx)

            # ファンダメンタルチェック
            if fundamental_provider is not None:
                _now_utc = _dt.datetime(
                    candle_time.year, candle_time.month,
                    candle_time.day, candle_time.hour,
                    candle_time.minute,
                    tzinfo=_dt.timezone.utc,
                )
                _fctx = fundamental_provider.get_context(_now_utc, symbol)
                if _fctx.has_high_impact_within_30min:
                    self._events_skipped += 1
                    continue

            # シグナル生成
            current_time = pd.Timestamp(candle_time)
            row_data = (
                month_df.iloc[idx].to_dict()
                if idx < len(month_df)
                else None
            )

            consolidated = bot.generate_signal(
                current_time, candle,
                fundamental_ctx=None,
                fundamental_memory=None,
            )

            # シグナル → Signal エンティティ変換
            signal = None
            consensus_scores = None
            if consolidated.direction.value != "HOLD":
                signal = Signal(
                    signal_type=(
                        SignalType.BUY
                        if consolidated.direction.value == "BUY"
                        else SignalType.SELL
                    ),
                    confidence=consolidated.confidence,
                    stop_loss=consolidated.sl_pips,
                    take_profit=consolidated.tp_pips,
                    lot=consolidated.lot,
                    consensus_score=consolidated.consensus_score,
                    rationale=consolidated.rationale,
                    regime=getattr(consolidated, "regime", None),
                    mode=getattr(consolidated, "mode", None),
                    indicators_snapshot=getattr(
                        consolidated, "indicators_snapshot", None,
                    ),
                )
                consensus_scores = (
                    consolidated.buy_score,
                    consolidated.sell_score,
                )

            # ティックベースSL/TP判定
            if tick_df is not None and not tick_df.empty:
                self._check_tick_exits(
                    simulator, tick_df, candle, candle_time,
                )

            # キャンドル処理
            simulator.process_candle(
                candle=candle,
                signal=signal,
                consensus_scores=consensus_scores,
                row_data=row_data,
            )

    def _check_tick_exits(
        self,
        simulator: TradeSimulator,
        tick_df: pd.DataFrame,
        candle: Any,
        candle_time: Any,
    ) -> None:
        """オープンポジションのティックベースSL/TP判定"""
        tf_minutes = 15  # M15想定
        candle_start = pd.Timestamp(candle_time)
        candle_end = candle_start + pd.Timedelta(minutes=tf_minutes)

        positions_to_close: list[tuple] = []
        for pos in simulator.state.open_positions:
            result = check_tick_exit(
                position_signal_type=pos.signal_type,
                sl_price=pos.stop_loss,
                tp_price=pos.take_profit,
                tick_df=tick_df,
                candle_start=candle_start,
                candle_end=candle_end,
                slippage_price=simulator._slippage_price,
                pip_unit=simulator._pip_unit,
            )
            if result is not None:
                positions_to_close.append((pos, result))

        for pos, tick_result in positions_to_close:
            trade = simulator._close_position(
                pos,
                tick_result.exit_price,
                tick_result.exit_time,
                tick_result.reason,
                tick_result.trigger_price,
            )
            if trade:
                simulator.state.open_positions.remove(pos)

    # ============================================================
    # チェックポイント
    # ============================================================

    def _save_checkpoint(
        self,
        completed_ym: tuple[int, int],
        simulator: TradeSimulator,
        bot: Any,
    ) -> None:
        """チェックポイント保存（月完了時）"""
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "completed_year": completed_ym[0],
            "completed_month": completed_ym[1],
            "symbol": self._config.symbol,
            "job_id": self._config.job_id,
            "balance": simulator.state.balance,
            "open_positions": len(simulator.state.open_positions),
            "closed_trades": len(simulator.state.closed_trades),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        meta_path = self._checkpoint_dir / "checkpoint.json"
        meta_path.write_text(
            json.dumps(meta, indent=2, default=str),
            encoding="utf-8",
        )

        state_path = self._checkpoint_dir / "state.pkl"
        with open(state_path, "wb") as f:
            pickle.dump(simulator.state, f)

        try:
            bot_path = self._checkpoint_dir / "bot_state.pkl"
            with open(bot_path, "wb") as f:
                pickle.dump(bot, f)
        except (pickle.PicklingError, TypeError):
            logger.debug("Bot状態のpickle保存失敗（無視）")

    def _load_checkpoint(
        self,
    ) -> tuple[tuple[int, int], Any, Any] | None:
        """チェックポイント復元

        Returns:
            ((year, month), SimulatorState, bot_or_None) or None
        """
        meta_path = self._checkpoint_dir / "checkpoint.json"
        if not meta_path.exists():
            return None

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        completed_ym = (meta["completed_year"], meta["completed_month"])

        state_path = self._checkpoint_dir / "state.pkl"
        if not state_path.exists():
            return None
        with open(state_path, "rb") as f:
            sim_state = pickle.load(f)  # noqa: S301

        bot_state = None
        bot_path = self._checkpoint_dir / "bot_state.pkl"
        if bot_path.exists():
            try:
                with open(bot_path, "rb") as f:
                    bot_state = pickle.load(f)  # noqa: S301
            except Exception:
                logger.debug("Bot状態の復元失敗（新規botで継続）")

        logger.info(
            "チェックポイント読み込み: %d-%02d (残高: %.0f, "
            "ポジション: %d, トレード: %d)",
            completed_ym[0], completed_ym[1],
            meta.get("balance", 0),
            meta.get("open_positions", 0),
            meta.get("closed_trades", 0),
        )
        return completed_ym, sim_state, bot_state

    def clear_checkpoint(self) -> None:
        """チェックポイントを削除"""
        import shutil

        if self._checkpoint_dir.exists():
            shutil.rmtree(self._checkpoint_dir)
            logger.info("チェックポイント削除: %s", self._checkpoint_dir)

    # ============================================================
    # 結果集計
    # ============================================================

    def _build_result(
        self,
        simulator: TradeSimulator,
        cfg: MonthRunnerConfig,
        processed_months: int,
        total_months: int,
        resumed_from: tuple[int, int] | None,
    ) -> MonthRunnerResult:
        """結果を集計"""
        trades = simulator.get_closed_trades()
        calculator = MetricsCalculator(
            initial_balance=self._sim_config.initial_balance,
        )
        metrics = calculator.calculate(
            trades, simulator.state.daily_pnl,
        )

        return MonthRunnerResult(
            symbol=cfg.symbol,
            start_month=f"{cfg.start_year}-{cfg.start_month:02d}",
            end_month=f"{cfg.end_year}-{cfg.end_month:02d}",
            total_months=total_months,
            processed_months=processed_months,
            trades=len(trades),
            win_rate=metrics.win_rate * 100,
            profit_factor=metrics.profit_factor,
            net_profit=(
                simulator.state.balance
                - self._sim_config.initial_balance
            ),
            max_drawdown=simulator.state.max_drawdown * 100,
            sharpe=metrics.sharpe_ratio or 0,
            events_skipped=self._events_skipped,
            resumed_from=(
                f"{resumed_from[0]}-{resumed_from[1]:02d}"
                if resumed_from else None
            ),
        )


# 後方互換エイリアス
DayRunnerConfig = MonthRunnerConfig
DayRunnerResult = MonthRunnerResult
DayRunner = MonthRunner
