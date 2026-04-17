"""Nautilus Trader バックテスト実行スクリプト（月並列対応）

既存BTと同じ.indicator_cacheのデータを使い、
Nautilus Traderでバックテストを月並列で実行する。

Usage:
    uv run python scripts/backtest_nautilus.py run --symbol USDJPY --start 2020-01-01 --end 2022-12-31 --consensus-threshold 18.0
    uv run python scripts/backtest_nautilus.py run --symbol USDJPY --start 2020-01-01 --end 2022-12-31 --cpus 12
    uv run python scripts/backtest_nautilus.py compare --native results/xxx.json --nautilus results/yyy.json
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
import time as _time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

SUPPORTED_SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]

_NAUTILUS_SYMBOL_MAP = {
    "USDJPY": "USD/JPY", "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY", "AUDJPY": "AUD/JPY",
    "CADJPY": "CAD/JPY", "CHFJPY": "CHF/JPY",
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
}

_TIMEFRAMES_ALL = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]


def _get_data_dir() -> Path:
    from autotrader.config.paths import get_data_dir
    return Path(get_data_dir())


# ---------------------------------------------------------------------------
# データ読み込み
# ---------------------------------------------------------------------------


def _load_market_data(
    symbol: str,
    start: datetime,
    end: datetime,
    max_timeframe: str = "D1",
) -> dict[str, pd.DataFrame]:
    """既存BTと同一の.indicator_cacheからデータを読み込み."""
    from autotrader.backtest.data_loader import DataLoader
    from autotrader.calculator.precompute import PrecomputeEngine
    from autotrader.core.enums import Timeframe

    data_dir = _get_data_dir()
    symbol_dir = data_dir / symbol
    chart_dir = symbol_dir / "chart"
    base_dir = chart_dir if chart_dir.exists() else symbol_dir
    ind_cache_dir = symbol_dir / ".indicator_cache"

    precompute = PrecomputeEngine()
    market_data: dict[str, pd.DataFrame] = {}

    start_year = start.year - 1
    end_year = end.year
    needed_years = list(range(start_year, end_year + 1))

    # max_timeframe でTFリストをキャップ
    cap_minutes = Timeframe(max_timeframe).minutes()
    timeframes = [
        tf for tf in _TIMEFRAMES_ALL
        if Timeframe(tf).minutes() <= cap_minutes
    ]

    for tf in timeframes:
        df: pd.DataFrame | None = None

        # 優先1: .indicator_cache/
        if ind_cache_dir.exists():
            tf_dirs = sorted(ind_cache_dir.glob(f"{tf}_*"))
            if tf_dirs:
                cache_path = tf_dirs[0]
                year_dfs = []
                for y in needed_years:
                    yf = cache_path / f"{y}.parquet"
                    if yf.exists():
                        year_dfs.append(pd.read_parquet(yf))
                if year_dfs:
                    df = pd.concat(year_dfs, ignore_index=True)
                    df = df.sort_values("time").reset_index(drop=True)

        # 優先2: chart/cache/
        if df is None:
            _tf_names = [tf, "Daily"] if tf == "D1" else [tf]
            for _tn in _tf_names:
                cache_pq = base_dir / "cache" / f"{symbol}_{_tn}.parquet"
                if cache_pq.exists():
                    df = pd.read_parquet(cache_pq)
                    break

        if df is None:
            continue

        # 期間フィルタ（ウォームアップ500本含む）
        if "time" in df.columns:
            time_col = pd.to_datetime(df["time"], utc=True)
            end_mask = time_col < end
            start_idx = time_col.searchsorted(start)
            warmup_start = max(0, start_idx - 500)
            df = df.iloc[warmup_start:].loc[end_mask.iloc[warmup_start:]].reset_index(drop=True)

        if "sma_20" not in df.columns:
            from autotrader.calculator.precompute import PrecomputeEngine
            df = PrecomputeEngine().compute_technical_indicators(df)

        market_data[tf] = df

    return market_data


def _load_m1_quote_df(
    symbol: str,
    start: datetime,
    end: datetime,
    spread_pips: float,
    pip_unit: float,
) -> pd.DataFrame:
    """M1バーデータをbid/ask DataFrameに変換."""
    data_dir = _get_data_dir()
    symbol_dir = data_dir / symbol
    ind_cache_dir = symbol_dir / ".indicator_cache"
    chart_dir = symbol_dir / "chart"
    base_dir = chart_dir if chart_dir.exists() else symbol_dir

    df: pd.DataFrame | None = None

    if ind_cache_dir.exists():
        tf_dirs = sorted(ind_cache_dir.glob("M1_*"))
        if tf_dirs:
            cache_path = tf_dirs[0]
            year_dfs = []
            for y in range(start.year, end.year + 1):
                yf = cache_path / f"{y}.parquet"
                if yf.exists():
                    year_dfs.append(pd.read_parquet(yf))
            if year_dfs:
                df = pd.concat(year_dfs, ignore_index=True)

    if df is None:
        cache_pq = base_dir / "cache" / f"{symbol}_M1.parquet"
        if cache_pq.exists():
            df = pd.read_parquet(cache_pq)

    if df is None:
        raise FileNotFoundError(f"M1データが見つかりません: {symbol}")

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df[(df["time"] >= start) & (df["time"] < end)]
        df = df.set_index("time")

    spread = spread_pips * pip_unit
    return pd.DataFrame({
        "bid_price": df["close"],
        "ask_price": df["close"] + spread,
    }, index=df.index)


# ---------------------------------------------------------------------------
# run-month: 1ヶ月分バックテスト（サブプロセスとして実行）
# ---------------------------------------------------------------------------


def cmd_run_month(args: argparse.Namespace) -> None:
    """1ヶ月分のバックテストを実行し、結果をJSONファイルに出力."""
    from decimal import Decimal

    from nautilus_trader.backtest.config import BacktestEngineConfig
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import LoggingConfig, StrategyConfig
    from nautilus_trader.model.currencies import JPY, USD
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
    from nautilus_trader.model.identifiers import InstrumentId, TraderId, Venue
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.objects import Money
    from nautilus_trader.model.orders.list import OrderList
    from nautilus_trader.persistence.wranglers import (
        BarDataWrangler,
        QuoteTickDataWrangler,
    )
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy

    from autotrader.config.trading_params import get_preset
    from autotrader.core.entities import Candle
    from autotrader.core.enums import SignalType, Timeframe
    from autotrader.decision.unified.config import UnifiedBotConfig
    from autotrader.decision.unified.risk.position_manager import (
        ManagementActionType,
        PositionManager,
        PositionManagerConfig,
    )
    from autotrader.decision.unified.mode_selector import TradingPlan
    from autotrader.decision.unified.trade_bot import UnifiedTradeBot

    symbol = args.symbol
    year = args.year
    month = args.month
    output_path = Path(args.output)
    nautilus_symbol = _NAUTILUS_SYMBOL_MAP[symbol]
    preset = get_preset(symbol)

    month_start = datetime(year, month, 1, tzinfo=UTC)
    month_end = datetime(year + (1 if month == 12 else 0),
                         1 if month == 12 else month + 1, 1, tzinfo=UTC)

    max_timeframe = args.max_timeframe  # None=プリセット値を使用

    # プリセットからmax_timeframe解決（CLI未指定時）
    if max_timeframe is None:
        from autotrader.config.trading_params import get_symbol_overrides
        _ovr = get_symbol_overrides(symbol)
        max_timeframe = _ovr.get("signal", {}).get("max_timeframe", "D1")

    # データ読み込み
    market_data = _load_market_data(symbol, month_start, month_end,
                                    max_timeframe=max_timeframe)
    if "M1" not in market_data:
        json.dump({"year": year, "month": month, "trades": [], "error": "M1データなし"},
                  open(output_path, "w"))
        return

    # TradeBot — プリセットオーバーライド適用（既存BTと同条件）
    from autotrader.config.trading_params import (
        get_pip_unit,
        get_quote_ccy_rate,
        get_symbol_overrides,
    )

    pip_unit = get_pip_unit(symbol)
    qcr = get_quote_ccy_rate(symbol)
    bot_kwargs: dict[str, Any] = {
        "max_positions": preset.max_positions,
        "bonus_max_positions": preset.bonus_max_positions,
        "bonus_score_threshold": preset.bonus_score_threshold,
        "base_risk_pct": preset.base_risk_pct,
        "max_lot_per_trade": preset.max_lot_per_trade,
        "max_total_exposure_lot": preset.max_total_exposure_lot,
        "equity_floor_pct": preset.equity_floor_pct,
        "pip_unit": pip_unit,
        "quote_ccy_rate": qcr,
        "sg_spread_threshold_pips": preset.sg_spread_threshold_pips,
    }
    sym_ovr = get_symbol_overrides(symbol)
    sym_ovr.pop("multi_consensus_threshold", None)
    bot_kwargs.update(sym_ovr.get("signal", {}))
    bot_kwargs.update(sym_ovr.get("filter", {}))
    bot_kwargs.update(sym_ovr.get("risk_mgmt", {}))
    if args.consensus_threshold is not None:
        bot_kwargs["consensus_threshold"] = args.consensus_threshold
    if max_timeframe is not None:
        bot_kwargs["max_timeframe"] = max_timeframe
    if args.bot_overrides:
        _ovr = json.loads(args.bot_overrides)
        bot_kwargs.update(_ovr)
    bot_config = UnifiedBotConfig(**bot_kwargs)
    bot = UnifiedTradeBot(bot_config)
    bot.set_market_data(market_data)

    # M1 quote data
    quote_df = _load_m1_quote_df(
        symbol, month_start, month_end,
        spread_pips=preset.spread_pips, pip_unit=preset.pip_unit,
    )
    if quote_df.empty:
        json.dump({"year": year, "month": month, "trades": []}, open(output_path, "w"))
        return

    # Nautilusエンジン
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(log_level="ERROR"),
    ))

    sim_venue = Venue("SIM")
    is_jpy = preset.quote_ccy_rate == 1.0
    base_ccy = JPY if is_jpy else USD
    initial_bal = 1_000_000 if is_jpy else 10_000

    engine.add_venue(
        venue=sim_venue, oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN, base_currency=base_ccy,
        starting_balances=[Money(initial_bal, base_ccy)],
        fill_model=FillModel(prob_fill_on_limit=0.2, prob_slippage=0.5, random_seed=42),
    )

    instrument = TestInstrumentProvider.default_fx_ccy(nautilus_symbol, sim_venue)
    engine.add_instrument(instrument)

    # QuoteTick
    ticks = QuoteTickDataWrangler(instrument=instrument).process(quote_df.copy())
    engine.add_data(ticks)

    # M1 EXTERNALバー
    m1_df = market_data["M1"].copy()
    if "time" in m1_df.columns:
        m1_df["time"] = pd.to_datetime(m1_df["time"], utc=True)
        m1_df = m1_df[(m1_df["time"] >= month_start) & (m1_df["time"] < month_end)]
        m1_df = m1_df.set_index("time")
    if "volume" not in m1_df.columns and "tick_volume" in m1_df.columns:
        m1_df["volume"] = m1_df["tick_volume"]
    m1_bt = BarType.from_str(f"{nautilus_symbol}.SIM-1-MINUTE-MID-EXTERNAL")
    m1_bars = BarDataWrangler(bar_type=m1_bt, instrument=instrument).process(
        m1_df[["open", "high", "low", "close", "volume"]])
    engine.add_data(m1_bars)

    # ストラテジー
    pip_unit = preset.pip_unit
    trade_results: list[dict[str, Any]] = []

    class _Cfg(StrategyConfig, frozen=True):
        iid: str = ""
        bt: str = ""
        sym: str = "USDJPY"
        pu: float = 0.01
        dl: float = 0.1

    # PositionManager 初期化
    pm_config = PositionManagerConfig(
        spread_pips=preset.spread_pips,
        slippage_pips=preset.slippage_pips,
        pip_unit=pip_unit,
    )
    pm = PositionManager(pm_config)

    class S(Strategy):
        def __init__(self, config: _Cfg, tb: UnifiedTradeBot,
                     pm: PositionManager, bot_cfg: UnifiedBotConfig) -> None:
            super().__init__(config)
            self._tb = tb
            self._pm = pm
            self._bot_cfg = bot_cfg
            self._inst: Instrument | None = None

        def on_start(self) -> None:
            self._last_lot: float = 0.0
            self._pos_id: str | None = None
            self._pos_dir: SignalType | None = None
            self._pos_entry: float = 0.0
            self._pos_sl: float = 0.0
            self._pos_tp: float = 0.0
            self._pos_atr: float = 0.002  # デフォルト20pips
            self._pos_score: float = 0.0
            self._partial_trades: list[dict] = []
            self._pending_sig: Any = None  # 次足OPEN約定用
            iid = InstrumentId.from_str(self.config.iid)
            self._inst = self.cache.instrument(iid)
            if self._inst is None:
                self.stop()
                return
            self.subscribe_bars(BarType.from_str(self.config.bt))

        def _has_open_position(self) -> bool:
            if self._inst is None:
                return False
            return not self.portfolio.is_flat(self._inst.id)

        def _close_position_market(self, bar: Bar) -> None:
            """成行でポジション全決済"""
            if self._inst is None:
                return
            self.cancel_all_orders(self._inst.id)
            self.close_all_positions(self._inst.id)

        def _reduce_position(self, bar: Bar, close_ratio: float) -> None:
            """部分決済"""
            if self._inst is None:
                return
            positions = self.cache.positions_open(instrument_id=self._inst.id)
            if not positions:
                return
            pos = positions[0]
            qty_total = float(pos.quantity)
            close_qty = int(qty_total * close_ratio)
            if close_qty <= 0:
                return
            # 部分決済トレード記録
            cl = float(bar.close)
            pu = self.config.pu
            is_buy = self._pos_dir == SignalType.BUY
            e = self._pos_entry
            pips = (cl - e) / pu if is_buy else (e - cl) / pu
            close_lot = self._last_lot * close_ratio
            pnl = pips * 1000.0 * close_lot  # pip_value=1000 for JPY pairs
            self._partial_trades.append({
                "symbol": self.config.sym,
                "direction": "BUY" if is_buy else "SELL",
                "entry_price": e, "exit_price": cl,
                "volume": close_lot,
                "pnl": pnl, "pnl_pips": pips,
                "opened_at": str(pos.ts_opened),
                "closed_at": str(bar.ts_event),
                "partial": True,
            })
            # Nautilus reduce注文
            side = OrderSide.SELL if is_buy else OrderSide.BUY
            order = self.order_factory.market(
                instrument_id=self._inst.id,
                order_side=side,
                quantity=self._inst.make_qty(Decimal(str(close_qty))),
                reduce_only=True,
            )
            self.submit_order(order)
            self._last_lot -= close_lot

        def _update_sl(self, new_sl: float) -> None:
            """SL更新（内部状態のみ、on_barでチェック）"""
            self._pos_sl = new_sl

        def on_bar(self, bar: Bar) -> None:
            if self._inst is None:
                return

            bt = pd.Timestamp(bar.ts_event, unit="ns", tz="UTC")
            bar_time = bt.to_pydatetime()
            cl = float(bar.close)

            # --- ポジション管理（PM evaluate + intrabar SL/TP）---
            if self._has_open_position() and self._pos_id is not None:
                hi = float(bar.high)
                lo = float(bar.low)
                pu = self.config.pu
                is_buy = self._pos_dir == SignalType.BUY

                # Intrabar SL/TPチェック（既存BTと同等）
                if is_buy:
                    if lo <= self._pos_sl:
                        self._close_position_market(bar)
                        return
                    if self._pos_tp > 0 and hi >= self._pos_tp:
                        self._close_position_market(bar)
                        return
                else:
                    if hi >= self._pos_sl:
                        self._close_position_market(bar)
                        return
                    if self._pos_tp > 0 and lo <= self._pos_tp:
                        self._close_position_market(bar)
                        return

                # シグナル生成（consensus score用）
                c = Candle(symbol=self.config.sym, timeframe=Timeframe.M1,
                           time=bar_time,
                           open=float(bar.open), high=hi,
                           low=lo, close=cl,
                           volume=float(bar.volume))
                sig = self._tb.generate_signal(bt, c)
                buy_score = sig.buy_score if hasattr(sig, "buy_score") else 0.0
                sell_score = sig.sell_score if hasattr(sig, "sell_score") else 0.0

                action = self._pm.evaluate(
                    position_id=self._pos_id,
                    current_price=cl,
                    current_time=bar_time,
                    atr=self._pos_atr,
                    current_signal=sig.direction,
                    buy_score=buy_score,
                    sell_score=sell_score,
                )

                if action.action_type == ManagementActionType.FULL_CLOSE:
                    self._close_position_market(bar)
                    return
                elif action.action_type == ManagementActionType.PARTIAL_CLOSE:
                    self._reduce_position(bar, action.close_ratio)
                    if action.new_sl is not None:
                        self._pos_sl = action.new_sl
                    return
                elif action.action_type == ManagementActionType.UPDATE_SL:
                    if action.new_sl is not None:
                        self._pos_sl = action.new_sl
                return  # ポジション保有中は新規エントリーしない

            # --- pending signal 約定（次足OPENで実行）---
            if not self._has_open_position() and self._pending_sig is not None:
                sig = self._pending_sig
                self._pending_sig = None
                op = float(bar.open)  # 次足のOPEN価格で約定
                pu = self.config.pu
                if sig.direction == SignalType.BUY:
                    sl, tp = op - sig.sl_pips * pu, op + sig.tp_pips * pu
                else:
                    sl, tp = op + sig.sl_pips * pu, op - sig.tp_pips * pu
                lot = sig.lot if sig.lot else self.config.dl
                qty = int(lot * 100_000)
                side = OrderSide.BUY if sig.direction == SignalType.BUY else OrderSide.SELL
                order = self.order_factory.market(
                    instrument_id=self._inst.id,
                    order_side=side,
                    quantity=self._inst.make_qty(Decimal(str(qty))),
                )
                self._last_lot = lot
                self._pos_dir = sig.direction
                self._pos_entry = op
                self._pos_sl = sl
                self._pos_tp = tp
                self._pos_score = sig.consensus_score or 0.0
                self._partial_trades = []
                self._pos_atr = 0.002
                if hasattr(sig, "indicators_snapshot") and sig.indicators_snapshot:
                    self._pos_atr = sig.indicators_snapshot.get("atr_14", 0.002)
                self.submit_order(order)
                return  # 約定処理完了、シグナル生成はスキップ

            # --- シグナル生成 → pending保存（次足OPENで約定）---
            if self._has_open_position():
                return

            c = Candle(symbol=self.config.sym, timeframe=Timeframe.M1,
                       time=bar_time,
                       open=float(bar.open), high=float(bar.high),
                       low=float(bar.low), close=cl,
                       volume=float(bar.volume))
            sig = self._tb.generate_signal(bt, c)
            if sig.direction == SignalType.HOLD or sig.confidence < 0.5:
                return
            if sig.sl_pips <= 0 or sig.tp_pips <= 0:
                return
            # 次足のOPENで約定するためpendingに保存
            self._pending_sig = sig

        def on_position_opened(self, event: Any) -> None:
            pos = self.cache.position(event.position_id)
            if pos is None:
                return
            self._pos_id = str(event.position_id)
            plan = TradingPlan.create_universal(self._bot_cfg)
            from dataclasses import replace as _dc_replace
            plan = _dc_replace(plan, regime=getattr(
                self._tb, "_last_regime", "TREND"))
            self._pm.register_position(
                position_id=self._pos_id,
                direction=self._pos_dir,
                entry_price=self._pos_entry,
                entry_time=pd.Timestamp(pos.ts_opened, unit="ns", tz="UTC").to_pydatetime(),
                sl=self._pos_sl,
                tp=self._pos_tp,
                volume=self._last_lot,
                plan=plan,
                entry_own_score=self._pos_score,
            )

        def on_position_closed(self, event: Any) -> None:
            pos = self.cache.position(event.position_id)
            if pos is None:
                return
            e, x = float(pos.avg_px_open), float(pos.avg_px_close)
            pnl = float(pos.realized_pnl)
            pu = self.config.pu
            is_buy = pos.entry == OrderSide.BUY
            pips = (x - e) / pu if is_buy else (e - x) / pu
            # 部分決済トレードを記録
            for pt in self._partial_trades:
                trade_results.append(pt)
            # 残りポジションの最終決済
            remaining_lot = self._last_lot
            if remaining_lot > 0.001:
                trade_results.append({
                    "symbol": self.config.sym,
                    "direction": "BUY" if is_buy else "SELL",
                    "entry_price": e, "exit_price": x,
                    "volume": remaining_lot,
                    "pnl": pnl, "pnl_pips": pips,
                    "opened_at": str(pos.ts_opened),
                    "closed_at": str(pos.ts_closed),
                })
            elif not self._partial_trades:
                # 部分決済もなく残りもない場合（フォールバック）
                trade_results.append({
                    "symbol": self.config.sym,
                    "direction": "BUY" if is_buy else "SELL",
                    "entry_price": e, "exit_price": x,
                    "volume": 0.0,
                    "pnl": pnl, "pnl_pips": pips,
                    "opened_at": str(pos.ts_opened),
                    "closed_at": str(pos.ts_closed),
                })
            # PM登録解除
            if self._pos_id:
                self._pm.unregister_position(self._pos_id)
            self._pos_id = None
            self._pos_dir = None

        def on_stop(self) -> None:
            if self._inst:
                self.cancel_all_orders(self._inst.id)
                self.close_all_positions(self._inst.id)

    strategy = S(
        config=_Cfg(
            iid=f"{nautilus_symbol}.SIM",
            bt=f"{nautilus_symbol}.SIM-1-MINUTE-MID-EXTERNAL",
            sym=symbol, pu=pip_unit, dl=preset.min_lot,
        ), tb=bot, pm=pm, bot_cfg=bot_config)
    engine.add_strategy(strategy)
    engine.run()
    engine.reset()
    engine.dispose()

    with open(output_path, "w") as f:
        json.dump({"year": year, "month": month, "trades": trade_results}, f)


# ---------------------------------------------------------------------------
# run: subprocess並列でバックテスト実行
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> None:
    """月並列でNautilusバックテストを実行（subprocessベース）."""
    import os

    symbol = args.symbol
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    cpus = args.cpus
    consensus_threshold = args.consensus_threshold

    _p = lambda *a, **k: print(*a, **k, flush=True)
    _p(f"=== Nautilus Trader バックテスト（月並列） ===")
    _p(f"通貨ペア: {symbol}")
    _p(f"期間: {start.date()} → {end.date()}")
    _p(f"モード: {args.mode}")
    _p(f"CPU: {cpus}")
    max_timeframe = args.max_timeframe  # None=プリセット値を使用
    # 表示用: プリセットから解決
    from autotrader.config.trading_params import get_symbol_overrides
    _sym_ovr = get_symbol_overrides(symbol)
    _display_mt = max_timeframe or _sym_ovr.get("signal", {}).get("max_timeframe", "D1")
    _display_ct = consensus_threshold if consensus_threshold is not None else _sym_ovr.get("signal", {}).get("consensus_threshold", 18.0)
    _p(f"consensus_threshold: {_display_ct}")
    _p(f"max_timeframe: {_display_mt}")
    if args.bot_overrides:
        _p(f"bot_overrides: {args.bot_overrides}")
    _p()

    # 月タスク生成
    months: list[tuple[int, int]] = []
    current = start
    while current < end:
        months.append((current.year, current.month))
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=UTC)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=UTC)

    _p(f"月タスク数: {len(months)}")
    t0 = _time.time()

    # 一時ディレクトリで結果ファイルを管理
    with tempfile.TemporaryDirectory(prefix="nautilus_bt_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # subprocessで並列実行
        active: dict[tuple[int, int], tuple[subprocess.Popen, Path]] = {}
        pending = list(months)
        completed: list[dict[str, Any]] = []
        failed: list[tuple[int, int, str]] = []

        script_path = Path(__file__).resolve()

        while pending or active:
            # 空きスロットにタスク投入
            while pending and len(active) < cpus:
                year, month = pending.pop(0)
                out_file = tmp_path / f"{year}_{month:02d}.json"
                cmd = [
                    sys.executable, str(script_path), "run-month",
                    "--symbol", symbol,
                    "--year", str(year),
                    "--month", str(month),
                    "--output", str(out_file),
                ]
                if consensus_threshold is not None:
                    cmd.extend(["--consensus-threshold", str(consensus_threshold)])
                if max_timeframe is not None:
                    cmd.extend(["--max-timeframe", max_timeframe])
                if args.bot_overrides:
                    cmd.extend(["--bot-overrides", args.bot_overrides])

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=str(_ROOT),
                )
                active[(year, month)] = (proc, out_file)

            # 完了チェック
            done_keys = []
            for key, (proc, out_file) in active.items():
                ret = proc.poll()
                if ret is not None:
                    done_keys.append(key)
                    year, month = key
                    if ret == 0 and out_file.exists():
                        with open(out_file) as f:
                            result = json.load(f)
                        n = len(result.get("trades", []))
                        _p(f"  [{len(completed)+len(failed)+1}/{len(months)}] "
                           f"{year}-{month:02d}: {n} trades")
                        completed.append(result)
                    else:
                        _p(f"  [{len(completed)+len(failed)+1}/{len(months)}] "
                           f"{year}-{month:02d}: FAILED (exit={ret})")
                        failed.append((year, month, f"exit={ret}"))

            for k in done_keys:
                del active[k]

            if active:
                _time.sleep(0.5)

    elapsed = _time.time() - t0
    _p(f"\n完了: {elapsed:.1f}s ({len(failed)} failures)")

    # マージ
    all_trades: list[dict[str, Any]] = []
    for r in sorted(completed, key=lambda x: (x["year"], x["month"])):
        all_trades.extend(r.get("trades", []))
    all_trades.sort(key=lambda t: t.get("opened_at", ""))

    # サマリー
    _p(f"\n=== 結果 ===")
    _p(f"トレード数: {len(all_trades)}")

    total_pnl = 0.0
    win_rate = 0.0
    if all_trades:
        wins = [t for t in all_trades if t["pnl"] > 0]
        losses = [t for t in all_trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in all_trades)
        win_rate = len(wins) / len(all_trades) * 100

        _p(f"勝率: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
        _p(f"総損益: {total_pnl:,.0f}")
        avg_pips = sum(t["pnl_pips"] for t in all_trades) / len(all_trades)
        _p(f"平均損益(pips): {avg_pips:.1f}")
        if wins:
            _p(f"平均利益(pips): {sum(t['pnl_pips'] for t in wins) / len(wins):.1f}")
        if losses:
            _p(f"平均損失(pips): {sum(t['pnl_pips'] for t in losses) / len(losses):.1f}")

    # JSON保存
    from autotrader.config.trading_params import get_preset
    preset = get_preset(symbol)
    is_jpy = preset.quote_ccy_rate == 1.0
    initial_balance = 1_000_000 if is_jpy else 10_000

    results_dir = _ROOT / "backtest_results" / "nautilus"
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "engine": "nautilus_trader",
            "symbol": symbol,
            "period": {"start": args.start, "end": args.end},
            "mode": args.mode,
            "consensus_threshold": _display_ct,
            "max_timeframe": _display_mt,
            "initial_balance": initial_balance,
            "elapsed_s": elapsed,
            "trades": all_trades,
            "summary": {
                "total_trades": len(all_trades),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
            },
        }, f, indent=2, ensure_ascii=False, default=str)

    _p(f"\n結果保存: {result_path}")


# ---------------------------------------------------------------------------
# run-multi-month: 1ヶ月分マルチペアシミュレーション（サブプロセスとして実行）
# ---------------------------------------------------------------------------


def cmd_run_multi_month(args: argparse.Namespace) -> None:
    """1ヶ月分のマルチペア同時シミュレーションを実行し、結果をJSONファイルに出力.

    Nautilusエンジンに全ペアを投入し、実行はNautilusに任せる。
    TradeBot（シグナル生成）とEntryGateChecker（ポートフォリオ制約）のみPython側で管理。
    """
    from decimal import Decimal

    import yaml

    from nautilus_trader.backtest.config import BacktestEngineConfig
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import LoggingConfig, StrategyConfig
    from nautilus_trader.model.currencies import JPY
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
    from nautilus_trader.model.identifiers import InstrumentId, StrategyId, TraderId, Venue
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.objects import Money
    from nautilus_trader.persistence.wranglers import (
        BarDataWrangler,
        QuoteTickDataWrangler,
    )
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy

    from autotrader.config.trading_params import (
        get_pip_unit,
        get_preset,
        get_quote_ccy_rate,
        get_symbol_overrides,
    )
    from autotrader.constraint.entry_gate import (
        EntryGateChecker,
        EntryGateContext,
    )
    from autotrader.core.entities import Candle
    from autotrader.core.enums import SignalType, Timeframe
    from autotrader.decision.unified.config import UnifiedBotConfig
    from autotrader.decision.unified.mode_selector import TradingPlan
    from autotrader.decision.unified.risk.position_manager import (
        ManagementActionType,
        PositionManager,
        PositionManagerConfig,
    )
    from autotrader.decision.unified.trade_bot import UnifiedTradeBot

    symbols = [s.strip() for s in args.symbols.split(",")]
    year = args.year
    month = args.month
    output_path = Path(args.output)

    month_start = datetime(year, month, 1, tzinfo=UTC)
    month_end = datetime(year + (1 if month == 12 else 0),
                         1 if month == 12 else month + 1, 1, tzinfo=UTC)

    # multi_pair設定読み込み
    with open(_ROOT / "config" / "symbol_presets.yaml") as f:
        raw = yaml.safe_load(f)
    mp_cfg = raw.get("multi_pair", {})

    # --- SharedPortfolioState: 全Strategy間で共有するポートフォリオ状態 ---
    class SharedPortfolioState:
        def __init__(self, mp_config: dict[str, Any]) -> None:
            self.global_max_positions: int = mp_config.get("global_max_positions", 4)
            self.per_pair_max_positions: int = mp_config.get("per_pair_max_positions", 1)
            self.global_max_exposure_lot: float = mp_config.get("global_max_exposure_lot", 5.0)
            self.max_same_direction_jpy: int = mp_config.get("max_same_direction_jpy", 3)

            self.gate_checker = EntryGateChecker()
            self.positions: dict[str, dict[str, Any]] = {}  # symbol -> {direction, lot}
            self.blocked_counts: dict[str, int] = {"global": 0, "exposure": 0, "jpy_direction": 0}
            self.trade_results: list[dict[str, Any]] = []

        def global_position_count(self) -> int:
            return len(self.positions)

        def global_exposure_lot(self) -> float:
            return sum(p["lot"] for p in self.positions.values())

        def jpy_direction_count(self, direction: SignalType) -> int:
            return sum(
                1 for sym, p in self.positions.items()
                if sym.endswith("JPY") and p["direction"] == direction
            )

        def register_position(self, symbol: str, direction: SignalType, lot: float) -> None:
            self.positions[symbol] = {"direction": direction, "lot": lot}

        def unregister_position(self, symbol: str) -> None:
            self.positions.pop(symbol, None)

        def record_block(self, deny_code: str) -> None:
            code = deny_code or ""
            if "global_position" in code:
                self.blocked_counts["global"] += 1
            elif "global_exposure" in code:
                self.blocked_counts["exposure"] += 1
            elif "jpy_direction" in code:
                self.blocked_counts["jpy_direction"] += 1

    shared = SharedPortfolioState(mp_cfg)

    # --- StrategyConfig ---
    class _MCfg(StrategyConfig, frozen=True):
        iid: str = ""
        bt: str = ""
        sym: str = "USDJPY"
        pu: float = 0.01
        dl: float = 0.1

    # --- MultiPairStrategy: シングルペアSクラス + ポートフォリオ制約 ---
    class MultiPairStrategy(Strategy):
        def __init__(
            self, config: _MCfg,
            tb: UnifiedTradeBot, pm: PositionManager,
            bot_cfg: UnifiedBotConfig,
            shared_state: SharedPortfolioState,
            preset: Any,
        ) -> None:
            super().__init__(config)
            self._tb = tb
            self._pm = pm
            self._bot_cfg = bot_cfg
            self._shared = shared_state
            self._preset = preset
            self._inst: Instrument | None = None

        def on_start(self) -> None:
            self._last_lot: float = 0.0
            self._pos_id: str | None = None
            self._pos_dir: SignalType | None = None
            self._pos_entry: float = 0.0
            self._pos_sl: float = 0.0
            self._pos_tp: float = 0.0
            self._pos_atr: float = 0.002
            self._pos_score: float = 0.0
            self._partial_trades: list[dict] = []
            self._pending_sig: Any = None
            iid = InstrumentId.from_str(self.config.iid)
            self._inst = self.cache.instrument(iid)
            if self._inst is None:
                self.stop()
                return
            self.subscribe_bars(BarType.from_str(self.config.bt))

        def _has_open_position(self) -> bool:
            if self._inst is None:
                return False
            return not self.portfolio.is_flat(self._inst.id)

        def _close_position_market(self, bar: Bar) -> None:
            if self._inst is None:
                return
            self.cancel_all_orders(self._inst.id)
            self.close_all_positions(self._inst.id)

        def _reduce_position(self, bar: Bar, close_ratio: float) -> None:
            if self._inst is None:
                return
            positions = self.cache.positions_open(instrument_id=self._inst.id)
            if not positions:
                return
            pos = positions[0]
            qty_total = float(pos.quantity)
            close_qty = int(qty_total * close_ratio)
            if close_qty <= 0:
                return
            cl = float(bar.close)
            pu = self.config.pu
            is_buy = self._pos_dir == SignalType.BUY
            e = self._pos_entry
            pips = (cl - e) / pu if is_buy else (e - cl) / pu
            close_lot = self._last_lot * close_ratio
            pnl = pips * 1000.0 * close_lot
            self._partial_trades.append({
                "symbol": self.config.sym,
                "direction": "BUY" if is_buy else "SELL",
                "entry_price": e, "exit_price": cl,
                "volume": close_lot,
                "pnl": pnl, "pnl_pips": pips,
                "opened_at": str(pos.ts_opened),
                "closed_at": str(bar.ts_event),
                "partial": True,
            })
            side = OrderSide.SELL if is_buy else OrderSide.BUY
            order = self.order_factory.market(
                instrument_id=self._inst.id,
                order_side=side,
                quantity=self._inst.make_qty(Decimal(str(close_qty))),
                reduce_only=True,
            )
            self.submit_order(order)
            self._last_lot -= close_lot

        def _check_portfolio_gate(self, sig: Any) -> bool:
            """EntryGateCheckerでポートフォリオ制約を確認."""
            sym = self.config.sym
            ctx = EntryGateContext(
                signal_direction=sig.direction,
                consensus_score=sig.consensus_score,
                symbol_position_count=1 if sym in self._shared.positions else 0,
                global_position_count=self._shared.global_position_count(),
                global_exposure_lot=self._shared.global_exposure_lot(),
                jpy_same_direction_count=self._shared.jpy_direction_count(sig.direction),
                max_positions=self._shared.per_pair_max_positions,
                bonus_max_positions=0,
                bonus_score_threshold=7.0,
                global_max_positions=self._shared.global_max_positions,
                global_max_exposure_lot=self._shared.global_max_exposure_lot,
                max_same_direction_jpy=self._shared.max_same_direction_jpy,
                is_jpy_pair=sym.endswith("JPY"),
                current_spread_pips=self._preset.spread_pips,
                spread_threshold_pips=self._preset.sg_spread_threshold_pips,
                dd_emergency_active=False,
                margin_usage_pct=0.0,
                margin_limit_pct=0.0,
            )
            result = self._shared.gate_checker.evaluate(ctx)
            if not result.allowed:
                self._shared.record_block(result.deny_code or "")
            return result.allowed

        def on_bar(self, bar: Bar) -> None:
            if self._inst is None:
                return

            bt = pd.Timestamp(bar.ts_event, unit="ns", tz="UTC")
            bar_time = bt.to_pydatetime()
            cl = float(bar.close)

            # --- ポジション管理（PM evaluate + intrabar SL/TP）---
            if self._has_open_position() and self._pos_id is not None:
                hi = float(bar.high)
                lo = float(bar.low)
                pu = self.config.pu
                is_buy = self._pos_dir == SignalType.BUY

                if is_buy:
                    if lo <= self._pos_sl:
                        self._close_position_market(bar)
                        return
                    if self._pos_tp > 0 and hi >= self._pos_tp:
                        self._close_position_market(bar)
                        return
                else:
                    if hi >= self._pos_sl:
                        self._close_position_market(bar)
                        return
                    if self._pos_tp > 0 and lo <= self._pos_tp:
                        self._close_position_market(bar)
                        return

                c = Candle(symbol=self.config.sym, timeframe=Timeframe.M1,
                           time=bar_time,
                           open=float(bar.open), high=hi,
                           low=lo, close=cl,
                           volume=float(bar.volume))
                sig = self._tb.generate_signal(bt, c)
                buy_score = sig.buy_score if hasattr(sig, "buy_score") else 0.0
                sell_score = sig.sell_score if hasattr(sig, "sell_score") else 0.0

                action = self._pm.evaluate(
                    position_id=self._pos_id,
                    current_price=cl,
                    current_time=bar_time,
                    atr=self._pos_atr,
                    current_signal=sig.direction,
                    buy_score=buy_score,
                    sell_score=sell_score,
                )

                if action.action_type == ManagementActionType.FULL_CLOSE:
                    self._close_position_market(bar)
                    return
                elif action.action_type == ManagementActionType.PARTIAL_CLOSE:
                    self._reduce_position(bar, action.close_ratio)
                    if action.new_sl is not None:
                        self._pos_sl = action.new_sl
                    return
                elif action.action_type == ManagementActionType.UPDATE_SL:
                    if action.new_sl is not None:
                        self._pos_sl = action.new_sl
                return

            # --- pending signal 約定（次足OPENで実行）---
            if not self._has_open_position() and self._pending_sig is not None:
                sig = self._pending_sig
                self._pending_sig = None
                op = float(bar.open)
                pu = self.config.pu
                if sig.direction == SignalType.BUY:
                    sl, tp = op - sig.sl_pips * pu, op + sig.tp_pips * pu
                else:
                    sl, tp = op + sig.sl_pips * pu, op - sig.tp_pips * pu
                lot = sig.lot if sig.lot else self.config.dl

                # ポートフォリオ制約チェック
                if not self._check_portfolio_gate(sig):
                    return

                qty = int(lot * 100_000)
                side = OrderSide.BUY if sig.direction == SignalType.BUY else OrderSide.SELL
                order = self.order_factory.market(
                    instrument_id=self._inst.id,
                    order_side=side,
                    quantity=self._inst.make_qty(Decimal(str(qty))),
                )
                self._last_lot = lot
                self._pos_dir = sig.direction
                self._pos_entry = op
                self._pos_sl = sl
                self._pos_tp = tp
                self._pos_score = sig.consensus_score or 0.0
                self._partial_trades = []
                self._pos_atr = 0.002
                if hasattr(sig, "indicators_snapshot") and sig.indicators_snapshot:
                    self._pos_atr = sig.indicators_snapshot.get("atr_14", 0.002)
                self.submit_order(order)
                return

            # --- シグナル生成 → pending保存（次足OPENで約定）---
            if self._has_open_position():
                return

            c = Candle(symbol=self.config.sym, timeframe=Timeframe.M1,
                       time=bar_time,
                       open=float(bar.open), high=float(bar.high),
                       low=float(bar.low), close=cl,
                       volume=float(bar.volume))
            sig = self._tb.generate_signal(bt, c)
            if sig.direction == SignalType.HOLD or sig.confidence < 0.5:
                return
            if sig.sl_pips <= 0 or sig.tp_pips <= 0:
                return
            self._pending_sig = sig

        def on_position_opened(self, event: Any) -> None:
            pos = self.cache.position(event.position_id)
            if pos is None:
                return
            self._pos_id = str(event.position_id)
            plan = TradingPlan.create_universal(self._bot_cfg)
            from dataclasses import replace as _dc_replace
            plan = _dc_replace(plan, regime=getattr(
                self._tb, "_last_regime", "TREND"))
            self._pm.register_position(
                position_id=self._pos_id,
                direction=self._pos_dir,
                entry_price=self._pos_entry,
                entry_time=pd.Timestamp(pos.ts_opened, unit="ns", tz="UTC").to_pydatetime(),
                sl=self._pos_sl,
                tp=self._pos_tp,
                volume=self._last_lot,
                plan=plan,
                entry_own_score=self._pos_score,
            )
            # shared_stateにポジション登録
            self._shared.register_position(
                self.config.sym, self._pos_dir, self._last_lot,
            )

        def on_position_closed(self, event: Any) -> None:
            pos = self.cache.position(event.position_id)
            if pos is None:
                return
            e, x = float(pos.avg_px_open), float(pos.avg_px_close)
            pnl = float(pos.realized_pnl)
            pu = self.config.pu
            is_buy = pos.entry == OrderSide.BUY
            pips = (x - e) / pu if is_buy else (e - x) / pu
            for pt in self._partial_trades:
                self._shared.trade_results.append(pt)
            remaining_lot = self._last_lot
            if remaining_lot > 0.001:
                self._shared.trade_results.append({
                    "symbol": self.config.sym,
                    "direction": "BUY" if is_buy else "SELL",
                    "entry_price": e, "exit_price": x,
                    "volume": remaining_lot,
                    "pnl": pnl, "pnl_pips": pips,
                    "opened_at": str(pos.ts_opened),
                    "closed_at": str(pos.ts_closed),
                })
            elif not self._partial_trades:
                self._shared.trade_results.append({
                    "symbol": self.config.sym,
                    "direction": "BUY" if is_buy else "SELL",
                    "entry_price": e, "exit_price": x,
                    "volume": 0.0,
                    "pnl": pnl, "pnl_pips": pips,
                    "opened_at": str(pos.ts_opened),
                    "closed_at": str(pos.ts_closed),
                })
            # PM登録解除 + shared_stateからポジション解除
            if self._pos_id:
                self._pm.unregister_position(self._pos_id)
            self._shared.unregister_position(self.config.sym)
            self._pos_id = None
            self._pos_dir = None

        def on_stop(self) -> None:
            if self._inst:
                self.cancel_all_orders(self._inst.id)
                self.close_all_positions(self._inst.id)

    # --- Nautilusエンジン作成 ---
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(log_level="ERROR"),
    ))

    sim_venue = Venue("SIM")
    engine.add_venue(
        venue=sim_venue, oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN, base_currency=JPY,
        starting_balances=[Money(1_000_000, JPY)],
        fill_model=FillModel(prob_fill_on_limit=0.2, prob_slippage=0.5, random_seed=42),
    )

    # --- ペア別初期化: instrument, data, strategy を追加 ---
    valid_symbols: list[str] = []
    for symbol in symbols:
        nautilus_symbol = _NAUTILUS_SYMBOL_MAP.get(symbol)
        if nautilus_symbol is None:
            continue

        preset = get_preset(symbol)
        pip_unit = get_pip_unit(symbol)
        qcr = get_quote_ccy_rate(symbol)

        sym_ovr = get_symbol_overrides(symbol)
        sym_ovr.pop("multi_consensus_threshold", None)
        max_timeframe = sym_ovr.get("signal", {}).get("max_timeframe", "D1")

        bot_kwargs: dict[str, Any] = {
            "max_positions": preset.max_positions,
            "bonus_max_positions": preset.bonus_max_positions,
            "bonus_score_threshold": preset.bonus_score_threshold,
            "base_risk_pct": preset.base_risk_pct,
            "max_lot_per_trade": preset.max_lot_per_trade,
            "max_total_exposure_lot": preset.max_total_exposure_lot,
            "equity_floor_pct": preset.equity_floor_pct,
            "pip_unit": pip_unit,
            "quote_ccy_rate": qcr,
            "sg_spread_threshold_pips": preset.sg_spread_threshold_pips,
        }
        bot_kwargs.update(sym_ovr.get("signal", {}))
        bot_kwargs.update(sym_ovr.get("filter", {}))
        bot_kwargs.update(sym_ovr.get("risk_mgmt", {}))
        bot_kwargs["max_timeframe"] = max_timeframe
        if args.bot_overrides:
            bot_kwargs.update(json.loads(args.bot_overrides))
        effective_max_tf = bot_kwargs.get("max_timeframe", max_timeframe)
        bot_config = UnifiedBotConfig(**bot_kwargs)

        # データ読み込み
        market_data = _load_market_data(symbol, month_start, month_end,
                                        max_timeframe=effective_max_tf)
        if "M1" not in market_data:
            continue

        bot = UnifiedTradeBot(bot_config)
        bot.set_market_data(market_data)

        pm_config = PositionManagerConfig(
            spread_pips=preset.spread_pips,
            slippage_pips=preset.slippage_pips,
            pip_unit=pip_unit,
        )
        pm = PositionManager(pm_config)

        # Instrument
        instrument = TestInstrumentProvider.default_fx_ccy(nautilus_symbol, sim_venue)
        engine.add_instrument(instrument)

        # QuoteTick
        quote_df = _load_m1_quote_df(
            symbol, month_start, month_end,
            spread_pips=preset.spread_pips, pip_unit=preset.pip_unit,
        )
        if quote_df.empty:
            continue
        ticks = QuoteTickDataWrangler(instrument=instrument).process(quote_df.copy())
        engine.add_data(ticks)

        # M1 EXTERNALバー
        m1_df = market_data["M1"].copy()
        if "time" in m1_df.columns:
            m1_df["time"] = pd.to_datetime(m1_df["time"], utc=True)
            m1_df = m1_df[(m1_df["time"] >= month_start) & (m1_df["time"] < month_end)]
            m1_df = m1_df.set_index("time")
        if "volume" not in m1_df.columns and "tick_volume" in m1_df.columns:
            m1_df["volume"] = m1_df["tick_volume"]
        m1_bt = BarType.from_str(f"{nautilus_symbol}.SIM-1-MINUTE-MID-EXTERNAL")
        m1_bars = BarDataWrangler(bar_type=m1_bt, instrument=instrument).process(
            m1_df[["open", "high", "low", "close", "volume"]])
        engine.add_data(m1_bars)

        # Strategy
        strategy = MultiPairStrategy(
            config=_MCfg(
                iid=f"{nautilus_symbol}.SIM",
                bt=f"{nautilus_symbol}.SIM-1-MINUTE-MID-EXTERNAL",
                sym=symbol, pu=pip_unit, dl=preset.min_lot,
                strategy_id=StrategyId(f"MultiPair-{symbol}"),
            ),
            tb=bot, pm=pm, bot_cfg=bot_config,
            shared_state=shared, preset=preset,
        )
        engine.add_strategy(strategy)
        valid_symbols.append(symbol)

    if not valid_symbols:
        with open(output_path, "w") as f:
            json.dump({"year": year, "month": month, "trades": [],
                       "blocked": shared.blocked_counts}, f)
        engine.dispose()
        return

    # --- 実行 ---
    engine.run()
    engine.reset()
    engine.dispose()

    # 結果出力
    with open(output_path, "w") as f:
        json.dump({
            "year": year,
            "month": month,
            "trades": shared.trade_results,
            "blocked": shared.blocked_counts,
        }, f, default=str)


# ---------------------------------------------------------------------------
# run-multi: subprocess並列でマルチペアバックテスト実行
# ---------------------------------------------------------------------------


def cmd_run_multi(args: argparse.Namespace) -> None:
    """マルチペアバックテストを月並列（subprocess）で実行."""

    symbols = [s.strip() for s in args.symbols.split(",")]
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    cpus = args.cpus

    _p = lambda *a, **k: print(*a, **k, flush=True)
    _p("=== Nautilus マルチペアバックテスト（月並列） ===")
    _p(f"通貨ペア: {', '.join(symbols)}")
    _p(f"期間: {start.date()} -> {end.date()}")
    _p(f"CPU: {cpus}")

    # multi_pair設定読み込み（表示用）
    import yaml
    with open(_ROOT / "config" / "symbol_presets.yaml") as f:
        raw = yaml.safe_load(f)
    mp_cfg = raw.get("multi_pair", {})
    _p(f"global_max_positions: {mp_cfg.get('global_max_positions', 4)}")
    _p(f"per_pair_max_positions: {mp_cfg.get('per_pair_max_positions', 1)}")
    _p(f"global_max_exposure_lot: {mp_cfg.get('global_max_exposure_lot', 5.0)}")
    _p(f"max_same_direction_jpy: {mp_cfg.get('max_same_direction_jpy', 3)}")
    if args.bot_overrides:
        _p(f"bot_overrides: {args.bot_overrides}")
    _p()

    # 月タスク生成
    months: list[tuple[int, int]] = []
    current = start
    while current < end:
        months.append((current.year, current.month))
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1, tzinfo=UTC)
        else:
            current = datetime(current.year, current.month + 1, 1, tzinfo=UTC)

    _p(f"月タスク数: {len(months)}")
    t0 = _time.time()

    # 一時ディレクトリで結果ファイルを管理
    with tempfile.TemporaryDirectory(prefix="nautilus_multi_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        active: dict[tuple[int, int], tuple[subprocess.Popen, Path]] = {}
        pending = list(months)
        completed: list[dict[str, Any]] = []
        failed: list[tuple[int, int, str]] = []

        script_path = Path(__file__).resolve()

        while pending or active:
            # 空きスロットにタスク投入
            while pending and len(active) < cpus:
                year, month = pending.pop(0)
                out_file = tmp_path / f"{year}_{month:02d}.json"
                cmd = [
                    sys.executable, str(script_path), "run-multi-month",
                    "--symbols", ",".join(symbols),
                    "--year", str(year),
                    "--month", str(month),
                    "--output", str(out_file),
                ]
                if args.bot_overrides:
                    cmd.extend(["--bot-overrides", args.bot_overrides])

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=str(_ROOT),
                )
                active[(year, month)] = (proc, out_file)

            # 完了チェック
            done_keys = []
            for key, (proc, out_file) in active.items():
                ret = proc.poll()
                if ret is not None:
                    done_keys.append(key)
                    year, month = key
                    if ret == 0 and out_file.exists():
                        with open(out_file) as f:
                            result = json.load(f)
                        n = len(result.get("trades", []))
                        _p(f"  [{len(completed)+len(failed)+1}/{len(months)}] "
                           f"{year}-{month:02d}: {n} trades")
                        completed.append(result)
                    else:
                        _p(f"  [{len(completed)+len(failed)+1}/{len(months)}] "
                           f"{year}-{month:02d}: FAILED (exit={ret})")
                        failed.append((year, month, f"exit={ret}"))

            for k in done_keys:
                del active[k]

            if active:
                _time.sleep(0.5)

    elapsed = _time.time() - t0
    _p(f"\n完了: {elapsed:.1f}s ({len(failed)} failures)")

    # マージ
    all_trades: list[dict[str, Any]] = []
    total_blocked = {"global": 0, "exposure": 0, "jpy_direction": 0}
    for r in sorted(completed, key=lambda x: (x["year"], x["month"])):
        all_trades.extend(r.get("trades", []))
        blk = r.get("blocked", {})
        for k in total_blocked:
            total_blocked[k] += blk.get(k, 0)
    all_trades.sort(key=lambda t: t.get("opened_at", ""))

    # --- 結果集計 ---
    _p(f"\n=== 結果 ===")
    _p(f"トレード数: {len(all_trades)}")

    # ペア別サマリー
    per_symbol: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        sym_trades = [t for t in all_trades if t["symbol"] == sym]
        if not sym_trades:
            per_symbol[sym] = {"total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0}
            continue
        wins = [t for t in sym_trades if t["pnl"] > 0]
        total_pnl = sum(t["pnl"] for t in sym_trades)
        wr = len(wins) / len(sym_trades) * 100
        per_symbol[sym] = {
            "total_trades": len(sym_trades),
            "win_rate": wr,
            "total_pnl": total_pnl,
        }
        _p(f"  {sym}: {len(sym_trades)} trades, WR={wr:.1f}%, PnL={total_pnl:,.0f}")

    # ポートフォリオサマリー
    total_pnl = sum(t["pnl"] for t in all_trades) if all_trades else 0.0
    win_rate = 0.0
    if all_trades:
        wins = [t for t in all_trades if t["pnl"] > 0]
        win_rate = len(wins) / len(all_trades) * 100

    _p(f"\n  ポートフォリオ: {len(all_trades)} trades, WR={win_rate:.1f}%, PnL={total_pnl:,.0f}")
    _p(f"  ブロック: global={total_blocked['global']}, "
       f"exposure={total_blocked['exposure']}, "
       f"jpy_direction={total_blocked['jpy_direction']}")

    # JSON保存
    results_dir = _ROOT / "backtest_results" / "nautilus"
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"multi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    output = {
        "engine": "nautilus_multi",
        "symbols": symbols,
        "period": {"start": args.start, "end": args.end},
        "multi_pair_config": {
            "global_max_positions": mp_cfg.get("global_max_positions", 4),
            "per_pair_max_positions": mp_cfg.get("per_pair_max_positions", 1),
            "global_max_exposure_lot": mp_cfg.get("global_max_exposure_lot", 5.0),
            "max_same_direction_jpy": mp_cfg.get("max_same_direction_jpy", 3),
        },
        "elapsed_s": elapsed,
        "trades": all_trades,
        "per_symbol_summary": per_symbol,
        "portfolio_summary": {
            "total_trades": len(all_trades),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "blocked_global": total_blocked["global"],
            "blocked_exposure": total_blocked["exposure"],
            "blocked_jpy_direction": total_blocked["jpy_direction"],
        },
    }

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    _p(f"\n結果保存: {result_path}")


# ---------------------------------------------------------------------------
# compare: 結果比較
# ---------------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> None:
    """独自BT結果とNautilus結果を比較."""
    native_path = Path(args.native)
    nautilus_path = Path(args.nautilus)

    for p in [native_path, nautilus_path]:
        if not p.exists():
            print(f"ERROR: ファイルが見つかりません: {p}")
            sys.exit(1)

    with open(native_path, encoding="utf-8") as f:
        native = json.load(f)
    with open(nautilus_path, encoding="utf-8") as f:
        nautilus = json.load(f)

    nt = native.get("trades", [])
    naut = nautilus.get("trades", [])
    ns = native.get("summary", {})
    naus = nautilus.get("summary", {})

    print("=== バックテスト結果比較 ===")
    print(f"独自BT:   {native_path.name}")
    print(f"Nautilus: {nautilus_path.name}\n")

    pnl_n = ns.get("total_pnl", 0)
    pnl_nau = naus.get("total_pnl", 0)
    pnl_diff = (pnl_nau - pnl_n) / abs(pnl_n) * 100 if pnl_n != 0 else 0

    rows = [
        ("トレード数", str(len(nt)), str(len(naut)), str(len(naut) - len(nt))),
        ("勝率(%)", f"{ns.get('win_rate', 0):.1f}", f"{naus.get('win_rate', 0):.1f}",
         f"{naus.get('win_rate', 0) - ns.get('win_rate', 0):+.1f}"),
        ("総損益", f"{pnl_n:,.0f}", f"{pnl_nau:,.0f}", f"{pnl_diff:+.1f}%"),
    ]

    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    widths = [max(w, len(h)) for w, h in zip(widths, ["指標", "独自BT", "Nautilus", "差異"])]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format("指標", "独自BT", "Nautilus", "差異"))
    print("-" * (sum(widths) + 6))
    for r in rows:
        print(fmt.format(*r))

    print()
    if abs(pnl_diff) <= 5:
        print("判定: PASS（差異5%以内）→ シミュレーター信頼OK")
    else:
        print("判定: FAIL（差異5%超）→ シミュレーターに問題あり")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Nautilus Trader バックテスト（月並列）")
    sub = parser.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", help="バックテスト実行（月並列）")
    run_p.add_argument("--symbol", default="USDJPY", choices=SUPPORTED_SYMBOLS)
    run_p.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    run_p.add_argument("--end", required=True, help="終了日 (YYYY-MM-DD)")
    run_p.add_argument("--mode", default="bar", choices=["bar", "tick"])
    run_p.add_argument("--cpus", type=int, default=12, help="並列CPU数")
    run_p.add_argument("--consensus-threshold", type=float, default=None)
    run_p.add_argument("--max-timeframe", default=None,
                       help="最大時間足キャップ（省略=プリセット値, D1=既存, H4=マイクロモード）")
    run_p.add_argument("--bot-overrides", default=None,
                       help="UnifiedBotConfig上書きJSON (例: '{\"bca_min_edge\":0.70}')")

    # run-month（内部用サブコマンド: subprocessから呼ばれる）
    rm_p = sub.add_parser("run-month", help=argparse.SUPPRESS)
    rm_p.add_argument("--symbol", required=True)
    rm_p.add_argument("--year", type=int, required=True)
    rm_p.add_argument("--month", type=int, required=True)
    rm_p.add_argument("--output", required=True)
    rm_p.add_argument("--consensus-threshold", type=float, default=None)
    rm_p.add_argument("--max-timeframe", default=None)
    rm_p.add_argument("--bot-overrides", default=None)

    # run-multi
    rm_p2 = sub.add_parser("run-multi", help="マルチペア同時シミュレーション（月並列）")
    _default_symbols = "USDJPY,EURJPY,GBPJPY,AUDJPY,CADJPY,CHFJPY,EURUSD,GBPUSD"
    rm_p2.add_argument("--symbols", default=_default_symbols,
                        help=f"カンマ区切りの通貨ペアリスト (デフォルト: {_default_symbols})")
    rm_p2.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    rm_p2.add_argument("--end", required=True, help="終了日 (YYYY-MM-DD)")
    rm_p2.add_argument("--cpus", type=int, default=12, help="並列CPU数")
    rm_p2.add_argument("--bot-overrides", default=None,
                        help="全ペア共通のUnifiedBotConfig上書きJSON")

    # run-multi-month（内部用サブコマンド: subprocessから呼ばれる）
    rmm_p = sub.add_parser("run-multi-month", help=argparse.SUPPRESS)
    rmm_p.add_argument("--symbols", required=True)
    rmm_p.add_argument("--year", type=int, required=True)
    rmm_p.add_argument("--month", type=int, required=True)
    rmm_p.add_argument("--output", required=True)
    rmm_p.add_argument("--bot-overrides", default=None)

    # compare
    cmp_p = sub.add_parser("compare", help="結果比較")
    cmp_p.add_argument("--native", required=True, help="独自BT結果JSON")
    cmp_p.add_argument("--nautilus", required=True, help="Nautilus結果JSON")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cmds = {
        "run": cmd_run, "run-month": cmd_run_month,
        "run-multi": cmd_run_multi, "run-multi-month": cmd_run_multi_month,
        "compare": cmd_compare,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
