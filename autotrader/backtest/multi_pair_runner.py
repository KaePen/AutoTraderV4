"""マルチペア統合バックテスト（時系列インターリーブ + 共有ポートフォリオ）

旧 scripts/run_multi_pair_backtest.py のコア機能を autotrader/backtest/ 配下に
再実装したもの。8通貨ペアを M1 ベースで時系列インターリーブし、共有資金プール
とグローバル制限（global_max_positions, max_same_direction_jpy 等）を強制
することで、ライブ環境でのポートフォリオ運用を再現する。

Usage:
    from autotrader.backtest.multi_pair_runner import (
        MultiPairConfig, run_multi_pair_period
    )

    cfg = MultiPairConfig(
        symbols=["USDJPY", "EURJPY", "GBPJPY"],
        start_year=2025, end_year=2026,
        global_max_positions=4,
        max_same_direction_jpy=3,
    )
    result = run_multi_pair_period(cfg)
    print(result.summary())
"""
from __future__ import annotations

import csv
import dataclasses
import gc
import heapq
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from autotrader.backtest.candle_arrays import CandleArrays
from autotrader.backtest.data_pipeline import (
    list_cached_months,
    load_monthly_cache,
)
from autotrader.backtest.runner import BacktestConfig, BacktestRunner
from autotrader.backtest.simulator import SimulatorConfig, TradeSimulator
from autotrader.backtest.tick_simulator import check_tick_exit
from autotrader.config.config_loader import ConfigLoader
from autotrader.config.trading_params import (
    get_pip_unit,
    get_preset,
    get_quote_ccy_rate,
)
from autotrader.core.entities import Signal
from autotrader.core.enums import ExitReason, SignalType, Timeframe
from autotrader.decision.unified import UnifiedBotConfig, UnifiedTradeBot
from autotrader.decision.unified.adaptive import TradeRecord
from autotrader.decision.unified.risk.position_manager import (
    PositionManagerConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_EQUITY = 1_000_000.0


# ============================================================
# 設定
# ============================================================

@dataclass
class MultiPairConfig:
    """マルチペアBT設定"""

    symbols: list[str]
    start_year: int = 2025
    end_year: int = 2026
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    global_max_positions: int = 4
    per_pair_max_positions: int = 1
    global_max_exposure_lot: float = 10.0
    max_same_direction_jpy: int = 3
    sequential_years: bool = True
    data_load_workers: int = 6
    # 年内期間フィルタ (start_year==end_year のときのみ有効)
    period_start: datetime | None = None
    period_end: datetime | None = None
    # ティックレベル exit (monthly_cache/ticks 利用)
    use_tick_exit: bool = False
    # 1ペアあたりのティック判定で使う基準TFのバー長(分)。
    # base_tf=M1なら1分、M5なら5分。
    tick_check_tf_minutes: int = 1
    # トレード CSV 出力パス（None=出力しない）
    output_trades_csv: str | None = None


# ============================================================
# 状態
# ============================================================

@dataclass
class PortfolioState:
    """共有ポートフォリオ状態（資金・ポジションカウント・DD追跡）"""

    equity: float = DEFAULT_INITIAL_EQUITY
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    peak_equity: float = DEFAULT_INITIAL_EQUITY
    max_dd_pct: float = 0.0
    global_open_positions: int = 0
    global_exposure_lot: float = 0.0
    per_pair_positions: dict[str, int] = field(default_factory=dict)
    per_pair_exposure: dict[str, float] = field(default_factory=dict)
    jpy_buy_count: int = 0
    jpy_sell_count: int = 0
    blocked_global: int = 0
    blocked_per_pair: int = 0
    blocked_exposure: int = 0
    blocked_direction: int = 0
    monthly_pnl: dict[tuple[int, int], float] = field(default_factory=dict)

    def can_open_position(
        self,
        symbol: str,
        config: MultiPairConfig,
        signal_direction: str,
    ) -> bool:
        if self.global_open_positions >= config.global_max_positions:
            self.blocked_global += 1
            return False
        if self.per_pair_positions.get(symbol, 0) >= config.per_pair_max_positions:
            self.blocked_per_pair += 1
            return False
        if self.global_exposure_lot >= config.global_max_exposure_lot:
            self.blocked_exposure += 1
            return False
        if (
            config.max_same_direction_jpy > 0
            and symbol.endswith("JPY")
        ):
            limit = config.max_same_direction_jpy
            if signal_direction == "BUY" and self.jpy_buy_count >= limit:
                self.blocked_direction += 1
                return False
            if signal_direction == "SELL" and self.jpy_sell_count >= limit:
                self.blocked_direction += 1
                return False
        return True

    def update_peak(self) -> None:
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        if self.peak_equity > 0:
            dd = (self.peak_equity - self.equity) / self.peak_equity * 100
            if dd > self.max_dd_pct:
                self.max_dd_pct = dd

    def update_dd_unrealized(
        self, equity_with_unrealized: float,
    ) -> None:
        """含み損込みのequityで peak/DD を mark-to-market 更新

        portfolio.equity は close 時のみ更新されるため close-base の
        DDは楽観バイアスを持つ。本メソッドは毎足呼び、含み損を反映した
        実効的な最大DDを追跡する。

        Args:
            equity_with_unrealized: portfolio.equity + 全ペア含み損
        """
        if equity_with_unrealized > self.peak_equity:
            self.peak_equity = equity_with_unrealized
        if self.peak_equity > 0:
            dd = (
                (self.peak_equity - equity_with_unrealized)
                / self.peak_equity * 100
            )
            if dd > self.max_dd_pct:
                self.max_dd_pct = dd


@dataclass
class PairContext:
    """ペアごとの実行コンテキスト"""

    symbol: str
    bot: UnifiedTradeBot
    simulator: TradeSimulator
    arrays: CandleArrays
    period_df: pd.DataFrame
    base_tf: Timeframe
    runner: BacktestRunner


# ============================================================
# データロード
# ============================================================

def _create_runner(symbol: str) -> BacktestRunner:
    preset = get_preset(symbol)
    config = BacktestConfig(
        symbol=symbol,
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        pip_value=preset.pip_value,
        max_positions=preset.max_positions,
        bonus_max_positions=preset.bonus_max_positions,
        bonus_score_threshold=preset.bonus_score_threshold,
    )
    return BacktestRunner(config=config, verbose=False, log_to_file=False)


def _load_pair_data(
    symbol: str,
    needed_years: list[int],
) -> tuple[BacktestRunner, dict[str, pd.DataFrame]]:
    runner = _create_runner(symbol)
    market_data = runner._load_all_timeframes(
        include_m1=True, needed_years=needed_years,
    )
    if "D1" not in market_data:
        runner.load_data()
        if runner._d1_df is not None:
            market_data["D1"] = runner._d1_df
    if hasattr(runner, "_tf_data"):
        runner._tf_data.clear()
    return runner, market_data


def _load_all_pair_data(
    symbols: list[str],
    needed_years: list[int],
    max_workers: int = 6,
) -> dict[str, tuple[BacktestRunner, dict[str, pd.DataFrame]]]:
    runners: dict[str, tuple[BacktestRunner, dict[str, pd.DataFrame]]] = {}
    workers = min(max_workers, len(symbols))
    t0 = time.time()
    logger.info(
        "[MultiPair] データロード開始: %dペア, workers=%d, years=%s",
        len(symbols), workers, needed_years,
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_load_pair_data, sym, needed_years): sym
            for sym in symbols
        }
        for done, future in enumerate(as_completed(futures), 1):
            sym = futures[future]
            runners[sym] = future.result()
            elapsed = time.time() - t0
            logger.info(
                "[MultiPair]   [%d/%d] %s ロード完了 (%.1fs)",
                done, len(symbols), sym, elapsed,
            )
    gc.collect()
    logger.info("[MultiPair] 全ペアロード完了: %.1fs", time.time() - t0)
    return runners


# ============================================================
# コンテキスト構築
# ============================================================

def _setup_pair_context(
    symbol: str,
    runner: BacktestRunner,
    year: int,
    bot_config: UnifiedBotConfig,
    pm_config: PositionManagerConfig,
    initial_balance: float,
    full_market_data: dict[str, pd.DataFrame],
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> PairContext | None:
    base_tf_name = None
    for tf_name in ["M1", "M5", "M15", "H1"]:
        if tf_name in full_market_data:
            base_tf_name = tf_name
            break
    if base_tf_name is None:
        return None

    tf = Timeframe(base_tf_name)
    df = full_market_data[base_tf_name]

    start = period_start or datetime(year, 1, 1)
    end = period_end or datetime(year + 1, 1, 1)
    period_df = df[(df["time"] >= start) & (df["time"] < end)].reset_index(
        drop=True,
    )
    if period_df.empty:
        return None

    market_data: dict[str, pd.DataFrame] = {}
    for tf_key, tf_df in full_market_data.items():
        year_df = tf_df[
            (tf_df["time"] >= start) & (tf_df["time"] < end)
        ].reset_index(drop=True)
        if not year_df.empty:
            market_data[tf_key] = year_df

    bot = UnifiedTradeBot(bot_config)
    bot.state = bot.state.with_initial_equity(initial_balance)
    bot.set_market_data(market_data)

    preset = get_preset(symbol)
    pip_unit = get_pip_unit(symbol)
    quote_rate = get_quote_ccy_rate(symbol)

    pm_cfg: PositionManagerConfig | None = None
    if bot_config.use_position_manager:
        pm_dict = asdict(pm_config)
        pm_dict["spread_pips"] = preset.spread_pips
        pm_dict["slippage_pips"] = preset.slippage_pips
        pm_dict["pip_unit"] = pip_unit
        pm_cfg = PositionManagerConfig(**pm_dict)

    sim_config = SimulatorConfig(
        initial_balance=initial_balance,
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        pip_value=preset.pip_value,
        max_positions=preset.max_positions,
        bonus_max_positions=preset.bonus_max_positions,
        bonus_score_threshold=preset.bonus_score_threshold,
        default_volume=1.0,
        use_position_manager=bot_config.use_position_manager,
        use_dynamic_lot=bot_config.use_dynamic_lot,
        pip_unit=pip_unit,
        quote_ccy_rate=quote_rate,
        commission_per_lot=preset.commission_per_lot,
        bot_config=bot_config,
        sl_tp_in_pips=True,
        pm_config=pm_cfg,
    )
    simulator = TradeSimulator(config=sim_config)
    arrays = CandleArrays.from_dataframe(period_df)

    return PairContext(
        symbol=symbol, bot=bot, simulator=simulator,
        arrays=arrays, period_df=period_df, base_tf=tf, runner=runner,
    )


# ============================================================
# 1年実行（時系列インターリーブ）
# ============================================================

def _run_year(
    year: int,
    contexts: dict[str, PairContext],
    config: MultiPairConfig,
    portfolio: PortfolioState,
) -> dict[str, list[Any]]:
    """1年分を時系列インターリーブで実行"""

    def _pair_time_gen(sym: str, arrays: CandleArrays):
        for idx in range(arrays.n_rows):
            yield (arrays.get_time(idx), sym, idx)

    total_bars = sum(ctx.arrays.n_rows for ctx in contexts.values())
    merged = heapq.merge(
        *(_pair_time_gen(sym, ctx.arrays) for sym, ctx in contexts.items()),
        key=lambda x: (x[0], x[1]),
    )

    current_month: tuple[int, int] | None = None
    month_start_equity = portfolio.equity

    cached_exp: dict[str, tuple[float, float, float, int, int]] = {
        sym: (0.0, 0.0, 0.0, 0, 0) for sym in contexts
    }
    pair_last_dir: dict[str, str] = {}

    t0 = time.time()
    log_interval = max(total_bars // 20, 10000)

    # ティック exit 用キャッシュ
    # tick_months[sym] = set of (year, month) で月別ティック存在を判定
    # tick_df_cache[(sym, year, month)] = pd.DataFrame
    tick_months: dict[str, set[tuple[int, int]]] = {}
    tick_df_cache: dict[tuple[str, int, int], pd.DataFrame] = {}
    if config.use_tick_exit:
        from autotrader.config.paths import get_data_dir
        _data_dir = Path(get_data_dir())
        for sym in contexts:
            tick_months[sym] = set(
                list_cached_months(sym, "ticks", _data_dir)
            )
        n_avail = sum(len(v) for v in tick_months.values())
        logger.info(
            "[MultiPair] ティック exit 有効: %dペア, 月別キャッシュ計%d件",
            len(tick_months), n_avail,
        )
    tf_minutes_for_tick = max(1, config.tick_check_tf_minutes)

    for bar_num, (bar_time, sym, idx) in enumerate(merged):
        ctx = contexts[sym]

        # 月変わり検出
        cmonth = (bar_time.year, bar_time.month)
        if current_month is None:
            current_month = cmonth
            month_start_equity = portfolio.equity
        elif cmonth != current_month:
            month_pnl = portfolio.equity - month_start_equity
            portfolio.monthly_pnl[current_month] = (
                portfolio.monthly_pnl.get(current_month, 0.0) + month_pnl
            )
            current_month = cmonth
            month_start_equity = portfolio.equity

        candle = ctx.arrays.get_candle(idx, sym, ctx.base_tf)

        # equity 同期
        ctx.simulator.state.balance = portfolio.equity
        ctx.simulator.state.equity = portfolio.equity
        ctx.bot.state = ctx.bot.state.with_initial_equity(
            portfolio.initial_equity,
        )
        ctx.bot.state = dataclasses.replace(
            ctx.bot.state, equity=portfolio.equity,
        )

        # ポジション情報を bot へ反映（キャッシュ）
        exp, b_lot, s_lot, b_cnt, s_cnt = cached_exp[sym]
        ctx.bot.state = ctx.bot.state.with_exposure(
            open_exposure_lot=exp,
            open_same_direction_lot=max(b_lot, s_lot),
            open_buy_count=b_cnt,
            open_sell_count=s_cnt,
        )

        # クロスペア方向
        other_dirs = {s: d for s, d in pair_last_dir.items() if s != sym}
        ctx.bot.set_cross_pair_directions(other_dirs)

        # シグナル生成
        current_time = pd.Timestamp(bar_time)
        consolidated = ctx.bot.generate_signal(current_time, candle)

        if consolidated.direction != SignalType.HOLD:
            pair_last_dir[sym] = consolidated.direction.value

        signal: Signal | None = None
        if (
            consolidated.direction != SignalType.HOLD
            and consolidated.confidence >= 0.5
        ):
            sl_pips = consolidated.sl_pips if consolidated.sl_pips > 0 else None
            tp_pips = consolidated.tp_pips if consolidated.tp_pips > 0 else None
            row = ctx.period_df.iloc[idx]
            atr_val = float(row.get("atr_14", 0) or 0)
            indicators: dict[str, Any] = {}
            if atr_val > 0:
                indicators["atr_14"] = atr_val
            signal = Signal(
                symbol=sym,
                timeframe=ctx.base_tf,
                signal_type=consolidated.direction,
                confidence=min(consolidated.confidence, 1.0),
                stop_loss=sl_pips,
                take_profit=tp_pips,
                reasoning=consolidated.rationale,
                regime=consolidated.regime,
                mode=consolidated.mode,
                consensus_score=consolidated.consensus_score,
                lot=consolidated.lot,
                indicators_snapshot=indicators,
            )

        # ポートフォリオ制約
        if signal is not None and not portfolio.can_open_position(
            sym, config, signal.signal_type.value,
        ):
            signal = None

        balance_before = ctx.simulator.state.balance
        prev_n = len(ctx.simulator.state.open_positions)
        prev_trades = len(ctx.simulator.state.closed_trades)

        # ティック exit 判定（オープン中ポジションのみ）
        if (
            config.use_tick_exit
            and ctx.simulator.state.open_positions
            and tick_months.get(sym)
        ):
            ymkey = (bar_time.year, bar_time.month)
            if ymkey in tick_months[sym]:
                cache_key = (sym, ymkey[0], ymkey[1])
                tick_df = tick_df_cache.get(cache_key)
                if tick_df is None:
                    from autotrader.config.paths import get_data_dir
                    tick_df = load_monthly_cache(
                        sym, "ticks", ymkey[0], ymkey[1],
                        Path(get_data_dir()),
                    )
                    tick_df_cache[cache_key] = tick_df
                    # メモリ節約: 古い月のキャッシュ破棄
                    for k in list(tick_df_cache.keys()):
                        if k[0] == sym and k[1:] != ymkey:
                            tick_df_cache.pop(k, None)
                if tick_df is not None and not tick_df.empty:
                    candle_start = pd.Timestamp(bar_time)
                    # tick_df のインデックスTZと揃える
                    _tz = getattr(tick_df.index, "tz", None)
                    if _tz is not None and candle_start.tzinfo is None:
                        candle_start = candle_start.tz_localize(_tz)
                    elif _tz is None and candle_start.tzinfo is not None:
                        candle_start = candle_start.tz_localize(None)
                    candle_end = candle_start + pd.Timedelta(
                        minutes=tf_minutes_for_tick
                    )
                    positions_to_close: list[tuple] = []
                    for pos in list(ctx.simulator.state.open_positions):
                        result = check_tick_exit(
                            position_signal_type=pos.signal_type,
                            sl_price=pos.stop_loss,
                            tp_price=pos.take_profit,
                            tick_df=tick_df,
                            candle_start=candle_start,
                            candle_end=candle_end,
                            slippage_price=ctx.simulator._slippage_price,
                            pip_unit=ctx.simulator._pip_unit,
                        )
                        if result is not None:
                            positions_to_close.append((pos, result))
                    for pos, tres in positions_to_close:
                        # exit_time の TZ を opened_at と揃える
                        et = tres.exit_time
                        if (
                            getattr(pos, "opened_at", None) is not None
                            and pos.opened_at.tzinfo is None
                            and et.tzinfo is not None
                        ):
                            et = et.tz_localize(None)
                        ctx.simulator._close_position(
                            pos,
                            tres.exit_price,
                            et,
                            tres.reason,
                        )

        consensus_scores = (consolidated.buy_score, consolidated.sell_score)
        ctx.simulator.process_candle(
            candle, signal, consensus_scores=consensus_scores,
        )

        pnl_delta = ctx.simulator.state.balance - balance_before
        portfolio.equity += pnl_delta
        portfolio.update_peak()

        # 含み損込みの mark-to-market DD更新
        # 各 simulator.state.equity = simulator.state.balance + 自身の含み損
        # multi_pair では simulator.state.balance は portfolio.equity に同期(L390-391)
        # → 各 (equity - balance) の合計が全ペア含み損
        unrealized_total = sum(
            (c.simulator.state.equity - c.simulator.state.balance)
            for c in contexts.values()
        )
        portfolio.update_dd_unrealized(
            portfolio.equity + unrealized_total,
        )

        curr_n = len(ctx.simulator.state.open_positions)
        balance_changed = ctx.simulator.state.balance != balance_before
        if curr_n != prev_n or balance_changed:
            positions = ctx.simulator.state.open_positions
            new_exp = sum(p.volume for p in positions)
            new_b_lot = sum(
                p.volume for p in positions
                if p.signal_type == SignalType.BUY
            )
            new_s_lot = sum(
                p.volume for p in positions
                if p.signal_type == SignalType.SELL
            )
            new_b_cnt = sum(
                1 for p in positions if p.signal_type == SignalType.BUY
            )
            new_s_cnt = sum(
                1 for p in positions if p.signal_type == SignalType.SELL
            )
            cached_exp[sym] = (
                new_exp, new_b_lot, new_s_lot, new_b_cnt, new_s_cnt,
            )
            portfolio.per_pair_positions[sym] = curr_n
            portfolio.per_pair_exposure[sym] = new_exp
            portfolio.global_open_positions = sum(
                portfolio.per_pair_positions.values(),
            )
            portfolio.global_exposure_lot = sum(
                portfolio.per_pair_exposure.values(),
            )
            jpy_b = jpy_s = 0
            for s, c in contexts.items():
                if s.endswith("JPY"):
                    for p in c.simulator.state.open_positions:
                        if p.signal_type == SignalType.BUY:
                            jpy_b += 1
                        else:
                            jpy_s += 1
            portfolio.jpy_buy_count = jpy_b
            portfolio.jpy_sell_count = jpy_s

        # 決済時 bot.on_trade_executed
        closed = ctx.simulator.state.closed_trades
        if len(closed) > prev_trades:
            new_trade = closed[-1]
            pnl = new_trade.profit_loss or 0
            ctx.bot.on_trade_executed(
                bar_time, pnl=pnl, trade_record=TradeRecord.from_trade(new_trade),
            )

        if bar_num > 0 and bar_num % log_interval == 0:
            elapsed = time.time() - t0
            pct = bar_num / total_bars * 100
            logger.info(
                "[MultiPair] %d年: %.0f%% (%d/%d, equity=%.0f, dd=%.2f%%, %.0fs)",
                year, pct, bar_num, total_bars,
                portfolio.equity, portfolio.max_dd_pct, elapsed,
            )

    # 年末強制決済
    for sym, ctx in contexts.items():
        last_idx = ctx.arrays.n_rows - 1
        if last_idx >= 0:
            last_candle = ctx.arrays.get_candle(last_idx, sym, ctx.base_tf)
            balance_before = ctx.simulator.state.balance
            ctx.simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)
            pnl_delta = ctx.simulator.state.balance - balance_before
            portfolio.equity += pnl_delta
            portfolio.update_peak()

    # 最終月 PnL 記録
    if current_month is not None:
        month_pnl = portfolio.equity - month_start_equity
        portfolio.monthly_pnl[current_month] = (
            portfolio.monthly_pnl.get(current_month, 0.0) + month_pnl
        )

    elapsed = time.time() - t0
    logger.info(
        "[MultiPair] %d年完了: %d bars (%.0fs) equity=%.0f dd=%.2f%%",
        year, total_bars, elapsed, portfolio.equity, portfolio.max_dd_pct,
    )

    return {sym: list(ctx.simulator.state.closed_trades) for sym, ctx in contexts.items()}


# ============================================================
# 結果集計
# ============================================================

@dataclass
class MultiPairResult:
    config: MultiPairConfig
    pair_trades: dict[str, list[Any]]
    portfolio: PortfolioState

    @property
    def total_trades(self) -> int:
        return sum(len(t) for t in self.pair_trades.values())

    @property
    def total_pnl(self) -> float:
        return sum(
            (t.profit_loss or 0)
            for trades in self.pair_trades.values()
            for t in trades
        )

    @property
    def winning_trades(self) -> int:
        return sum(
            1 for trades in self.pair_trades.values()
            for t in trades if (t.profit_loss or 0) > 0
        )

    @property
    def win_rate(self) -> float:
        total = self.total_trades
        return (self.winning_trades / total) if total else 0.0

    @property
    def profit_factor(self) -> float:
        gross_win = sum(
            (t.profit_loss or 0)
            for trades in self.pair_trades.values()
            for t in trades if (t.profit_loss or 0) > 0
        )
        gross_loss = -sum(
            (t.profit_loss or 0)
            for trades in self.pair_trades.values()
            for t in trades if (t.profit_loss or 0) < 0
        )
        if gross_loss <= 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    def export_trades_csv(self, path: str | Path) -> int:
        """全ペアのトレードを CSV に出力。trade-by-trade 比較用。"""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = [
            "symbol", "trade_id", "ticket", "signal_type", "volume",
            "entry_price", "exit_price", "stop_loss", "take_profit",
            "profit_loss", "profit_loss_pips", "exit_reason",
            "consensus_score", "regime", "mode",
            "opened_at", "closed_at",
            "mfe_pips", "mae_pips",
        ]
        n_written = 0
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for sym, trades in self.pair_trades.items():
                for t in trades:
                    sig = (
                        t.signal_type.value
                        if hasattr(t.signal_type, "value")
                        else str(t.signal_type)
                    )
                    er = (
                        t.exit_reason.value
                        if t.exit_reason and hasattr(t.exit_reason, "value")
                        else (str(t.exit_reason) if t.exit_reason else "")
                    )
                    w.writerow([
                        sym,
                        getattr(t, "trade_id", ""),
                        getattr(t, "ticket", 0),
                        sig,
                        t.volume,
                        t.entry_price,
                        t.exit_price,
                        t.stop_loss,
                        t.take_profit,
                        t.profit_loss,
                        t.profit_loss_pips,
                        er,
                        getattr(t, "consensus_score", None),
                        getattr(t, "regime", None),
                        getattr(t, "mode", None),
                        t.opened_at.isoformat() if t.opened_at else "",
                        t.closed_at.isoformat() if t.closed_at else "",
                        getattr(t, "mfe_pips", None),
                        getattr(t, "mae_pips", None),
                    ])
                    n_written += 1
        return n_written

    def summary(self) -> dict[str, Any]:
        per_pair: dict[str, Any] = {}
        for sym, trades in self.pair_trades.items():
            wins = sum(1 for t in trades if (t.profit_loss or 0) > 0)
            pnl = sum((t.profit_loss or 0) for t in trades)
            per_pair[sym] = {
                "trades": len(trades),
                "wins": wins,
                "win_rate": (wins / len(trades)) if trades else 0.0,
                "pnl": pnl,
            }
        return {
            "symbols": self.config.symbols,
            "period": f"{self.config.start_year}-{self.config.end_year}",
            "initial_equity": self.portfolio.initial_equity,
            "final_equity": self.portfolio.equity,
            "net_pnl": self.total_pnl,
            "max_drawdown_pct": self.portfolio.max_dd_pct,
            "trades": self.total_trades,
            "wins": self.winning_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "blocked_global": self.portfolio.blocked_global,
            "blocked_per_pair": self.portfolio.blocked_per_pair,
            "blocked_exposure": self.portfolio.blocked_exposure,
            "blocked_direction": self.portfolio.blocked_direction,
            "per_pair": per_pair,
            "monthly_pnl": {
                f"{y}-{m:02d}": v
                for (y, m), v in sorted(self.portfolio.monthly_pnl.items())
            },
        }


# ============================================================
# 期間実行
# ============================================================

def run_multi_pair_period(
    config: MultiPairConfig,
    bot_config: UnifiedBotConfig | None = None,
    pm_config: PositionManagerConfig | None = None,
    bot_overrides: dict | None = None,
) -> MultiPairResult:
    """指定期間で全ペア時系列インターリーブBT

    bot/pm 設定は ConfigLoader.load_preset_config(symbol) で各ペア用に
    解決される（symbol_overrides.yaml 反映）。bot_config/pm_config が
    渡された場合はそちらを優先（全ペア共通設定）。
    """
    years = list(range(config.start_year, config.end_year + 1))

    runners = _load_all_pair_data(
        symbols=config.symbols,
        needed_years=years,
        max_workers=config.data_load_workers,
    )

    portfolio = PortfolioState(
        equity=config.initial_equity,
        initial_equity=config.initial_equity,
        peak_equity=config.initial_equity,
    )

    accumulated_trades: dict[str, list[Any]] = {sym: [] for sym in config.symbols}

    # 年ごとの bot は通常作り直すが、sequential_years=True で
    # bot 状態（edge validator 等）を年跨ぎ保持
    persistent_contexts: dict[str, PairContext] = {}
    loader = ConfigLoader()

    for year in years:
        logger.info("[MultiPair] === 年=%d 実行開始 ===", year)
        contexts: dict[str, PairContext] = {}

        for sym in config.symbols:
            runner, market_data = runners[sym]
            sym_bot, sym_pm = (
                (bot_config, pm_config)
                if bot_config is not None and pm_config is not None
                else loader.load_preset_config(sym)
            )
            # bot_overrides をフィールド単位で適用
            if bot_overrides:
                sym_bot = dataclasses.replace(sym_bot, **bot_overrides)

            ctx = _setup_pair_context(
                symbol=sym,
                runner=runner,
                year=year,
                bot_config=sym_bot,
                pm_config=sym_pm,
                initial_balance=config.initial_equity,
                full_market_data=market_data,
                period_start=config.period_start,
                period_end=config.period_end,
            )
            if ctx is None:
                logger.warning("[MultiPair] %s %d年 データなし", sym, year)
                continue

            # 前年の bot 状態を引き継ぎ（sequential）
            if config.sequential_years and sym in persistent_contexts:
                prev_bot = persistent_contexts[sym].bot
                ctx.bot.state = prev_bot.state
                # adaptive 系の継承（属性が存在する場合）
                for attr in ("edge_validator", "adaptive_overrides", "tuner"):
                    if hasattr(prev_bot, attr):
                        try:
                            setattr(ctx.bot, attr, getattr(prev_bot, attr))
                        except Exception:
                            pass

            contexts[sym] = ctx

        if not contexts:
            logger.warning("[MultiPair] %d年 全ペアデータなし、スキップ", year)
            continue

        _run_year(year, contexts, config, portfolio)

        # トレード累積
        for sym, ctx in contexts.items():
            accumulated_trades[sym].extend(list(ctx.simulator.state.closed_trades))

        persistent_contexts = contexts

    result = MultiPairResult(
        config=config,
        pair_trades=accumulated_trades,
        portfolio=portfolio,
    )

    if config.output_trades_csv:
        n = result.export_trades_csv(config.output_trades_csv)
        logger.info(
            "[MultiPair] trades.csv 出力: %s (%d rows)",
            config.output_trades_csv, n,
        )

    return result
