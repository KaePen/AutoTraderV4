"""日単位バックテスト実行エンジン

日別Parquetキャッシュを読み込み、日単位でバックテストを実行する。
チェックポイント機構により途中停止→再開が可能。

Usage:
    from autotrader.backtest.day_runner import DayRunner
    runner = DayRunner(config)
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
    list_cached_dates,
    load_daily_cache,
)
from autotrader.backtest.metrics import MetricsCalculator
from autotrader.backtest.position_event_logger import (
    PositionEventLogger,
)
from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
from autotrader.backtest.tick_simulator import check_tick_exit
from autotrader.constraint.entry_gate import EntryGateChecker
from autotrader.core.entities import Signal
from autotrader.core.enums import ExitReason, SignalType, Timeframe

logger = logging.getLogger(__name__)


@dataclass
class DayRunnerConfig:
    """日単位バックテスト設定

    Attributes:
        symbol: 通貨ペア
        start_date: 開始日
        end_date: 終��日（この日を含む）
        base_timeframe: シグナル生成の基準時間足
        data_dir: データディレクトリ
        checkpoint_dir: チェックポイント保存先
        job_id: ジョブ識別子（チェックポイント管理��）
        use_tick_exit: ティックベースSL/TP判定を使用
        save_checkpoint_interval: チェックポイント保存間隔（日数）
    """

    symbol: str = "USDJPY"
    start_date: date = field(default_factory=lambda: date(2024, 1, 1))
    end_date: date = field(default_factory=lambda: date(2024, 12, 31))
    base_timeframe: str = "M15"
    data_dir: Path | None = None
    checkpoint_dir: Path | None = None
    job_id: str = "default"
    use_tick_exit: bool = False
    save_checkpoint_interval: int = 1  # 毎日保存


@dataclass
class DayRunnerResult:
    """日単位��ックテスト結果"""

    symbol: str
    start_date: date
    end_date: date
    total_days: int
    processed_days: int
    trades: int
    win_rate: float
    profit_factor: float
    net_profit: float
    max_drawdown: float
    sharpe: float
    events_skipped: int
    resumed_from: date | None = None


class DayRunner:
    """日単位でバックテストを実行し、チェックポイントを管理

    日��Parquetキャッシュから1日ずつデータを読み込み処理する。
    チェックポイントにより途中停止→再開が可能。
    """

    def __init__(
        self,
        config: DayRunnerConfig,
        bot_config: Any = None,
        sim_config: SimulatorConfig | None = None,
    ) -> None:
        self._config = config
        self._bot_config = bot_config
        self._sim_config = sim_config or SimulatorConfig.from_preset(
            config.symbol,
        )

        # データディレクトリ
        if config.data_dir is None:
            from autotrader.config.paths import get_data_dir
            self._data_dir = Path(get_data_dir())
        else:
            self._data_dir = config.data_dir

        # チェックポイントディレクトリ
        if config.checkpoint_dir is None:
            from autotrader.config.paths import get_backtest_dir
            self._checkpoint_dir = (
                Path(get_backtest_dir())
                / "checkpoints"
                / config.job_id
            )
        else:
            self._checkpoint_dir = (
                config.checkpoint_dir / config.job_id
            )

        self._events_skipped = 0

    def run(
        self,
        resume: bool = True,
        fundamental_provider: Any = None,
    ) -> DayRunnerResult:
        """日単位バックテス��を実行

        Args:
            resume: チェックポイントから再開するか
            fundamental_provider: ファンダメンタルプロバイダー

        Returns:
            DayRunnerResult
        """
        from autotrader.decision.unified import (
            UnifiedBotConfig,
            UnifiedTradeBot,
        )

        cfg = self._config
        bot_config = self._bot_config or UnifiedBotConfig()

        # キャッシュ済み日付一覧を取得
        cached_dates = list_cached_dates(
            cfg.symbol, cfg.base_timeframe, self._data_dir,
        )
        if not cached_dates:
            raise ValueError(
                f"{cfg.symbol} {cfg.base_timeframe}: "
                f"日別キャッシュなし。先に data_pipeline prepare を実行してください"
            )

        # 対象日付をフィルタ
        target_dates = [
            d for d in cached_dates
            if cfg.start_date <= d <= cfg.end_date
        ]
        if not target_dates:
            raise ValueError(
                f"{cfg.symbol}: 対象期間 {cfg.start_date}～{cfg.end_date} "
                f"にキャッシュなし"
            )

        # チェックポイント復元
        resumed_from: date | None = None
        simulator = TradeSimulator(config=self._sim_config)
        bot = UnifiedTradeBot(bot_config)
        bot.state = bot.state.with_initial_equity(
            self._sim_config.initial_balance
        )

        if resume:
            checkpoint = self._load_checkpoint()
            if checkpoint is not None:
                last_date, sim_state, bot_state = checkpoint
                simulator.state = sim_state
                if bot_state is not None:
                    bot = bot_state
                resumed_from = last_date
                # 最終完了日の翌日から開始
                target_dates = [
                    d for d in target_dates if d > last_date
                ]
                logger.info(
                    "チェックポイントから再開: %s (%d日残り)",
                    last_date, len(target_dates),
                )

        if not target_dates:
            logger.info("処理対象日なし（全日完了済み）")
            return self._build_result(
                simulator, cfg, 0, len(cached_dates), resumed_from,
            )

        # ポジションイベントロガー
        pos_logger = PositionEventLogger()
        simulator.set_position_event_logger(pos_logger)

        # ティックキャッシュの有無を確認
        tick_dates = set()
        if cfg.use_tick_exit:
            tick_dates = set(
                list_cached_dates(
                    cfg.symbol, "ticks", self._data_dir,
                )
            )
            if tick_dates:
                logger.info(
                    "ティッ���データ: %d日分利用可能", len(tick_dates),
                )

        # メインループ: 日単位処理
        total_start = time.time()
        processed = 0
        total_target = len(target_dates)

        for day_idx, target_day in enumerate(target_dates):
            day_start = time.time()

            # 日別キャッシュ読み込み
            day_df = load_daily_cache(
                cfg.symbol, cfg.base_timeframe,
                target_day, self._data_dir,
            )
            if day_df is None or day_df.empty:
                continue

            # ティックデータ読み込み（利用可能なら）
            tick_df: pd.DataFrame | None = None
            if cfg.use_tick_exit and target_day in tick_dates:
                tick_df = load_daily_cache(
                    cfg.symbol, "ticks",
                    target_day, self._data_dir,
                )

            # ファンダメンタルチェック + シグナル生成 + シミュレーション
            self._process_day(
                day_df=day_df,
                tick_df=tick_df,
                simulator=simulator,
                bot=bot,
                symbol=cfg.symbol,
                tf=Timeframe(cfg.base_timeframe),
                fundamental_provider=fundamental_provider,
            )

            processed += 1

            # チェックポイント保存
            if processed % cfg.save_checkpoint_interval == 0:
                self._save_checkpoint(target_day, simulator, bot)

            # 進捗ログ（10日ごと）
            if processed % 10 == 0:
                elapsed = time.time() - total_start
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total_target - processed) / rate if rate > 0 else 0
                logger.info(
                    "[%d/%d] %s完了 (%.1f日/秒, ETA %.0f秒)",
                    processed, total_target, target_day,
                    rate, eta,
                )

        # 最終チェックポイント保存
        if target_dates:
            self._save_checkpoint(target_dates[-1], simulator, bot)

        total_elapsed = time.time() - total_start
        logger.info(
            "=== DayRunner完了: %s %d日処理 (%.1f秒) ===",
            cfg.symbol, processed, total_elapsed,
        )

        return self._build_result(
            simulator, cfg, processed, total_target, resumed_from,
        )

    def _process_day(
        self,
        day_df: pd.DataFrame,
        tick_df: pd.DataFrame | None,
        simulator: TradeSimulator,
        bot: Any,
        symbol: str,
        tf: Timeframe,
        fundamental_provider: Any | None,
    ) -> None:
        """1日分のバックテスト処理"""
        import datetime as _dt

        from autotrader.core.entities import Candle

        arrays = CandleArrays.from_dataframe(day_df.reset_index())

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
                _fctx = fundamental_provider.get_context(
                    _now_utc, symbol,
                )
                if _fctx.has_high_impact_within_30min:
                    self._events_skipped += 1
                    continue

            # シグ��ル生成
            current_time = pd.Timestamp(candle_time)
            row_data = (
                day_df.iloc[idx].to_dict()
                if idx < len(day_df)
                else None
            )

            consolidated = bot.generate_signal(
                current_time, candle,
                fundamental_ctx=None,
                fundamental_memory=None,
            )

            # シグナル → シミュレーターに渡す
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

            # キャンドル処理（エントリー/エグジット/PnL更新）
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

        # ティックで決済確定したポジションをクローズ
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
        completed_date: date,
        simulator: TradeSimulator,
        bot: Any,
    ) -> None:
        """チ���ックポイント保存"""
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # メタデータ
        meta = {
            "completed_date": completed_date.isoformat(),
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

        # SimulatorState
        state_path = self._checkpoint_dir / "state.pkl"
        with open(state_path, "wb") as f:
            pickle.dump(simulator.state, f)

        # Bot状態
        try:
            bot_path = self._checkpoint_dir / "bot_state.pkl"
            with open(bot_path, "wb") as f:
                pickle.dump(bot, f)
        except (pickle.PicklingError, TypeError):
            logger.debug("Bot状態のpickle保存失敗（無視）")

    def _load_checkpoint(
        self,
    ) -> tuple[date, Any, Any] | None:
        """チ��ックポイント復元

        Returns:
            (completed_date, SimulatorState, bot_or_None) or None
        """
        meta_path = self._checkpoint_dir / "checkpoint.json"
        if not meta_path.exists():
            return None

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        completed_date = date.fromisoformat(meta["completed_date"])

        # SimulatorState
        state_path = self._checkpoint_dir / "state.pkl"
        if not state_path.exists():
            return None
        with open(state_path, "rb") as f:
            sim_state = pickle.load(f)  # noqa: S301

        # Bot状態（オプショ��）
        bot_state = None
        bot_path = self._checkpoint_dir / "bot_state.pkl"
        if bot_path.exists():
            try:
                with open(bot_path, "rb") as f:
                    bot_state = pickle.load(f)  # noqa: S301
            except Exception:
                logger.debug("Bot状態の復元失敗（新規botで継続）")

        logger.info(
            "���ェックポイント読み込み: %s (残高: %.0f, "
            "ポジション: %d, トレード: %d)",
            completed_date,
            meta.get("balance", 0),
            meta.get("open_positions", 0),
            meta.get("closed_trades", 0),
        )
        return completed_date, sim_state, bot_state

    def clear_checkpoint(self) -> None:
        """チェックポイントを削除"""
        import shutil

        if self._checkpoint_dir.exists():
            shutil.rmtree(self._checkpoint_dir)
            logger.info(
                "チェックポイント削除: %s", self._checkpoint_dir,
            )

    # ============================================================
    # 結果集計
    # ============================================================

    def _build_result(
        self,
        simulator: TradeSimulator,
        cfg: DayRunnerConfig,
        processed_days: int,
        total_days: int,
        resumed_from: date | None,
    ) -> DayRunnerResult:
        """結果を集計"""
        trades = simulator.get_closed_trades()
        calculator = MetricsCalculator(
            initial_balance=self._sim_config.initial_balance,
        )
        metrics = calculator.calculate(
            trades, simulator.state.daily_pnl,
        )

        return DayRunnerResult(
            symbol=cfg.symbol,
            start_date=cfg.start_date,
            end_date=cfg.end_date,
            total_days=total_days,
            processed_days=processed_days,
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
            resumed_from=resumed_from,
        )
