"""Nautilus Trader バックテスト実行スクリプト

既存のchart/cache/のParquetデータを直接使用してバックテストを実行する。

モード:
    bar  (デフォルト): M1バーデータで約定シミュレーション
    tick: 実ティックデータで約定シミュレーション（要fetch済み）

Usage:
    # barモード（chart/cache/のデータで実行、すぐ使える）
    uv run python scripts/backtest_nautilus.py run --symbol USDJPY --start 2026-01-01 --end 2026-03-31

    # tickモード（実ティックデータ使用）
    uv run python scripts/backtest_nautilus.py run --symbol USDJPY --start 2026-01-01 --end 2026-03-31 --mode tick

    # 結果比較
    uv run python scripts/backtest_nautilus.py compare --native results/xxx.json --nautilus results/yyy.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from autotrader.backtest.data_loader import DataLoader
from autotrader.calculator.precompute import PrecomputeEngine
from autotrader.config.trading_params import get_preset
from autotrader.core.entities import Candle
from autotrader.core.enums import SignalType, Timeframe
from autotrader.decision.unified.config import UnifiedBotConfig
from autotrader.decision.unified.trade_bot import UnifiedTradeBot

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

_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]
_PRIMARY_TF = "M15"
_PRIMARY_TF_MINUTES = 15


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
) -> dict[str, pd.DataFrame]:
    """chart/cache/ Parquet → CSV フォールバックでインジケータ付きデータを読み込み.

    既存のrunnerと同じパス解決ロジック。期間フィルタ付き（ウォームアップ500本含む）。
    """
    import time as _time

    data_dir = _get_data_dir()
    symbol_dir = data_dir / symbol
    chart_dir = symbol_dir / "chart"
    base_dir = chart_dir if chart_dir.exists() else symbol_dir

    precompute = PrecomputeEngine()
    market_data: dict[str, pd.DataFrame] = {}

    for tf in _TIMEFRAMES:
        print(f"  {tf}: ", end="", flush=True)
        t0 = _time.time()
        df: pd.DataFrame | None = None
        source = ""

        # 優先1: chart/cache/ Parquet
        cache_pq = base_dir / "cache" / f"{symbol}_{tf}.parquet"
        if cache_pq.exists():
            try:
                df = pd.read_parquet(cache_pq)
                source = "cache"
            except Exception as e:
                logger.warning(f"Parquet読込失敗 {cache_pq}: {e}")

        # 優先2: CSV
        if df is None:
            pattern = f"{symbol}_{tf}_*.csv"
            csv_files = sorted(base_dir.glob(pattern))
            if not csv_files and base_dir != symbol_dir:
                csv_files = sorted(symbol_dir.glob(pattern))
            if csv_files:
                dfs = [DataLoader.load_mt5_csv(f) for f in csv_files]
                df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["time"])
                df = df.sort_values("time").reset_index(drop=True)
                source = "csv"

        if df is None:
            print("データなし (skip)")
            continue

        total_rows = len(df)

        # 期間フィルタ（ウォームアップ500本を含めて切り出し）
        if "time" in df.columns:
            time_col = pd.to_datetime(df["time"], utc=True)
            end_mask = time_col < end
            start_idx = time_col.searchsorted(start)
            warmup_start = max(0, start_idx - 500)
            df = df.iloc[warmup_start:].loc[end_mask.iloc[warmup_start:]].reset_index(drop=True)

        # インジケータ列がなければ計算
        if "sma_20" not in df.columns:
            df = precompute.compute_technical_indicators(df)
            source += "+computed"

        market_data[tf] = df
        elapsed = _time.time() - t0
        print(f"{len(df):,}/{total_rows:,} bars ({source}) [{elapsed:.1f}s]")

    return market_data


def _load_m1_bars_for_nautilus(
    symbol: str,
    start: datetime,
    end: datetime,
    spread_pips: float,
    pip_unit: float,
) -> pd.DataFrame:
    """M1バーデータをNautilus用のbid/ask DataFrameに変換.

    M1のclose価格をbidとし、bid + spread をaskとして返す。
    """
    data_dir = _get_data_dir()
    symbol_dir = data_dir / symbol
    chart_dir = symbol_dir / "chart"
    base_dir = chart_dir if chart_dir.exists() else symbol_dir

    df: pd.DataFrame | None = None

    # Parquetキャッシュ優先
    cache_pq = base_dir / "cache" / f"{symbol}_M1.parquet"
    if cache_pq.exists():
        df = pd.read_parquet(cache_pq)
    else:
        csv_files = sorted(base_dir.glob(f"{symbol}_M1_*.csv"))
        if csv_files:
            dfs = [DataLoader.load_mt5_csv(f) for f in csv_files]
            df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["time"])
            df = df.sort_values("time").reset_index(drop=True)

    if df is None:
        raise FileNotFoundError(f"M1データが見つかりません: {base_dir}")

    # 期間フィルタ
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df[(df["time"] >= start) & (df["time"] < end)]
        df = df.set_index("time")
    elif isinstance(df.index, pd.DatetimeIndex):
        df = df.loc[start:end]

    spread = spread_pips * pip_unit

    # bid = close, ask = close + spread でQuoteTick的な2列を作成
    result = pd.DataFrame({
        "bid_price": df["close"],
        "ask_price": df["close"] + spread,
    }, index=df.index)

    return result


def _load_tick_data(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """実ティックデータをParquetから読み込み."""
    tick_dir = _get_data_dir() / symbol / "ticks"

    if not tick_dir.exists():
        raise FileNotFoundError(
            f"ティックデータが見つかりません: {tick_dir}\n"
            f"先に backtest_prepare_data.py fetch で取得してください。"
        )

    parquet_files = sorted(tick_dir.glob("ticks_*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"Parquetファイルなし: {tick_dir}")

    dfs = [pd.read_parquet(f) for f in parquet_files]
    df = pd.concat(dfs)

    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")

    df = df.loc[start:end]

    # bid/ask列名の正規化
    if "bid" in df.columns:
        df = df.rename(columns={"bid": "bid_price", "ask": "ask_price"})

    return df[["bid_price", "ask_price"]]


# ---------------------------------------------------------------------------
# run: Nautilus バックテスト実行
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> None:
    """Nautilus Traderでバックテストを実行."""
    try:
        from nautilus_trader.backtest.config import BacktestEngineConfig
        from nautilus_trader.backtest.engine import BacktestEngine
        from nautilus_trader.backtest.models import FillModel
        from nautilus_trader.config import LoggingConfig
        from nautilus_trader.model.currencies import JPY, USD
        from nautilus_trader.model.enums import AccountType, OmsType
        from nautilus_trader.model.identifiers import TraderId, Venue
        from nautilus_trader.model.objects import Money
        from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
        from nautilus_trader.test_kit.providers import TestInstrumentProvider
    except ImportError:
        print(
            "ERROR: nautilus_trader が見つかりません。\n"
            "pip install nautilus_trader でインストールしてください。"
        )
        sys.exit(1)

    symbol = args.symbol
    mode = args.mode
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    preset = get_preset(symbol)

    print(f"=== Nautilus Trader バックテスト ===")
    print(f"通貨ペア: {symbol}")
    print(f"期間: {start.date()} → {end.date()}")
    print(f"モード: {mode}")
    print()

    # 1. インジケータデータ読み込み（chart/cache/ から直接）
    print("1. マーケットデータ読み込み...")
    market_data = _load_market_data(symbol, start, end)

    if _PRIMARY_TF not in market_data:
        print(f"ERROR: {_PRIMARY_TF}データが見つかりません")
        sys.exit(1)

    # 2. TradeBot初期化
    print("2. TradeBot初期化...")
    bot = UnifiedTradeBot(UnifiedBotConfig())
    bot.set_market_data(market_data)

    # 3. 約定データ読み込み（モード別）
    print(f"3. 約定データ読み込み ({mode})...")
    if mode == "tick":
        quote_df = _load_tick_data(symbol, start, end)
        print(f"   {len(quote_df):,} ticks (実データ)")
    else:
        quote_df = _load_m1_bars_for_nautilus(
            symbol, start, end,
            spread_pips=preset.spread_pips,
            pip_unit=preset.pip_unit,
        )
        print(f"   {len(quote_df):,} M1 bars → quote data")

    # 4. Nautilusエンジン構築
    print("4. Nautilusエンジン構築...")

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(log_level="WARNING"),
    ))

    sim_venue = Venue("SIM")
    is_jpy_account = preset.quote_ccy_rate == 1.0
    base_currency = JPY if is_jpy_account else USD
    initial_balance = 1_000_000 if is_jpy_account else 10_000

    engine.add_venue(
        venue=sim_venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=base_currency,
        starting_balances=[Money(initial_balance, base_currency)],
        fill_model=FillModel(
            prob_fill_on_limit=0.2,
            prob_slippage=0.5,
            random_seed=42,
        ),
    )

    nautilus_symbol = _NAUTILUS_SYMBOL_MAP[symbol]
    instrument = TestInstrumentProvider.default_fx_ccy(nautilus_symbol, sim_venue)
    engine.add_instrument(instrument)

    # QuoteTickデータ追加
    wrangler = QuoteTickDataWrangler(instrument=instrument)
    ticks = wrangler.process(quote_df)
    engine.add_data(ticks)
    print(f"   {len(ticks):,} quote ticks → engine")

    # 5. ストラテジー追加
    print("5. ストラテジー構築...")

    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.orders.list import OrderList
    from nautilus_trader.trading.strategy import Strategy

    class _Config(StrategyConfig, frozen=True):
        instrument_id_str: str = ""
        bar_type_str: str = ""
        symbol: str = "USDJPY"
        pip_unit: float = 0.01
        default_lot: float = 0.1

    class VerificationStrategy(Strategy):
        def __init__(
            self, config: _Config,
            trade_bot: UnifiedTradeBot,
        ) -> None:
            super().__init__(config)
            self._trade_bot = trade_bot
            self._instrument: Instrument | None = None
            self._pip_unit = config.pip_unit
            self._default_lot = config.default_lot
            self._symbol = config.symbol
            self._trade_log: list[dict[str, Any]] = []
            self._bar_count = 0
            self._signal_count = 0
            self._hold_count = 0
            self._low_conf_count = 0
            self._no_sl_tp_count = 0

        def on_start(self) -> None:
            iid = InstrumentId.from_str(self.config.instrument_id_str)
            self._instrument = self.cache.instrument(iid)
            if self._instrument is None:
                self.log.error(f"Instrument not found: {iid}")
                self.stop()
                return
            bar_type = BarType.from_str(self.config.bar_type_str)
            self.subscribe_bars(bar_type)
            self.subscribe_quote_ticks(iid)

        def on_bar(self, bar: Bar) -> None:
            if self._instrument is None:
                return

            self._bar_count += 1

            # 最初の3本でデバッグ出力
            if self._bar_count <= 3:
                bar_time = pd.Timestamp(bar.ts_event, unit="ns", tz="UTC")
                print(f"  [DEBUG] bar #{self._bar_count}: time={bar_time} "
                      f"O={float(bar.open):.3f} H={float(bar.high):.3f} "
                      f"L={float(bar.low):.3f} C={float(bar.close):.3f}")

            if not self.portfolio.is_flat(self._instrument.id):
                return

            bar_time = pd.Timestamp(bar.ts_event, unit="ns", tz="UTC")
            candle = Candle(
                symbol=self._symbol,
                timeframe=Timeframe.M15,
                time=bar_time.to_pydatetime(),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            )

            sig = self._trade_bot.generate_signal(bar_time, candle)

            # 最初の3本でシグナル結果もデバッグ
            if self._bar_count <= 3:
                print(f"  [DEBUG]   → direction={sig.direction.name} "
                      f"confidence={sig.confidence:.3f} "
                      f"consensus={sig.consensus_score} "
                      f"sl_pips={sig.sl_pips} tp_pips={sig.tp_pips}")

            if sig.direction == SignalType.HOLD:
                self._hold_count += 1
                return
            if sig.confidence < 0.5:
                self._low_conf_count += 1
                return
            if sig.sl_pips <= 0 or sig.tp_pips <= 0:
                self._no_sl_tp_count += 1
                return

            self._signal_count += 1

            close = float(bar.close)
            pu = self._pip_unit
            if sig.direction == SignalType.BUY:
                sl_px = close - sig.sl_pips * pu
                tp_px = close + sig.tp_pips * pu
            else:
                sl_px = close + sig.sl_pips * pu
                tp_px = close - sig.tp_pips * pu

            lot = sig.lot if sig.lot else self._default_lot
            side = OrderSide.BUY if sig.direction == SignalType.BUY else OrderSide.SELL

            ol: OrderList = self.order_factory.bracket(
                instrument_id=self._instrument.id,
                order_side=side,
                quantity=self._instrument.make_qty(Decimal(str(lot))),
                sl_trigger_price=self._instrument.make_price(sl_px),
                tp_price=self._instrument.make_price(tp_px),
            )
            self.submit_order_list(ol)

            self.log.info(
                f"SIGNAL: {sig.direction.name} @ {close:.3f} "
                f"SL={sl_px:.3f} TP={tp_px:.3f} lot={lot} "
                f"consensus={sig.consensus_score}"
            )

        def on_position_closed(self, event: Any) -> None:
            pos = self.cache.position(event.position_id)
            if pos is None:
                return
            entry = float(pos.avg_px_open)
            exit_ = float(pos.avg_px_close)
            pnl = float(pos.realized_pnl)
            pips = (exit_ - entry) / self._pip_unit if pos.is_long else (entry - exit_) / self._pip_unit

            rec = {
                "position_id": str(pos.id),
                "symbol": self._symbol,
                "direction": "BUY" if pos.is_long else "SELL",
                "entry_price": entry,
                "exit_price": exit_,
                "volume": float(pos.quantity),
                "pnl": pnl,
                "pnl_pips": pips,
                "opened_at": str(pos.ts_opened),
                "closed_at": str(pos.ts_closed),
                "duration_s": (pos.ts_closed - pos.ts_opened) / 1e9,
            }
            self._trade_log.append(rec)
            self.log.info(
                f"CLOSED: {rec['direction']} entry={entry:.3f} "
                f"exit={exit_:.3f} PnL={pnl:.0f} ({pips:.1f}pips)"
            )

        def on_stop(self) -> None:
            print(f"\n  [DEBUG] === シグナル統計 ===")
            print(f"  on_bar呼び出し: {self._bar_count}")
            print(f"  HOLD: {self._hold_count}")
            print(f"  confidence<0.5: {self._low_conf_count}")
            print(f"  SL/TP無し: {self._no_sl_tp_count}")
            print(f"  エントリー: {self._signal_count}")
            if self._instrument:
                self.cancel_all_orders(self._instrument.id)
                self.close_all_positions(self._instrument.id)

        def get_trade_log(self) -> list[dict[str, Any]]:
            return list(self._trade_log)

    bar_type_str = f"{nautilus_symbol}.SIM-{_PRIMARY_TF_MINUTES}-MINUTE-MID-INTERNAL"
    strategy = VerificationStrategy(
        config=_Config(
            instrument_id_str=f"{nautilus_symbol}.SIM",
            bar_type_str=bar_type_str,
            symbol=symbol,
            pip_unit=preset.pip_unit,
            default_lot=preset.min_lot,
        ),
        trade_bot=bot,
    )
    engine.add_strategy(strategy)

    # 6. 実行
    print("6. バックテスト実行中...")
    engine.run()

    # 7. 結果出力
    print("\n=== 結果 ===")
    trade_log = strategy.get_trade_log()
    print(f"トレード数: {len(trade_log)}")

    total_pnl = 0.0
    win_rate = 0.0

    if trade_log:
        wins = [t for t in trade_log if t["pnl"] > 0]
        losses = [t for t in trade_log if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trade_log)
        win_rate = len(wins) / len(trade_log) * 100

        print(f"勝率: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
        print(f"総損益: {total_pnl:,.0f}")
        avg_pips = sum(t["pnl_pips"] for t in trade_log) / len(trade_log)
        print(f"平均損益(pips): {avg_pips:.1f}")
        if wins:
            print(f"平均利益(pips): {sum(t['pnl_pips'] for t in wins) / len(wins):.1f}")
        if losses:
            print(f"平均損失(pips): {sum(t['pnl_pips'] for t in losses) / len(losses):.1f}")

    # JSON保存
    results_dir = _ROOT / "backtest_results" / "nautilus"
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({
            "engine": "nautilus_trader",
            "symbol": symbol,
            "period": {"start": args.start, "end": args.end},
            "mode": mode,
            "initial_balance": initial_balance,
            "trades": trade_log,
            "summary": {
                "total_trades": len(trade_log),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
            },
        }, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n結果保存: {result_path}")

    positions_report = engine.trader.generate_positions_report()
    if not positions_report.empty:
        print(f"\n--- Nautilus Positions Report ---")
        print(positions_report.to_string())

    engine.reset()
    engine.dispose()


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

    if len(nt) == len(naut) and len(nt) > 0:
        print(f"\n--- トレード詳細比較 (先頭10件) ---")
        for i in range(min(10, len(nt))):
            ed = abs(float(naut[i].get("entry_price", 0)) - float(nt[i].get("entry_price", 0)))
            xd = abs(float(naut[i].get("exit_price", 0)) - float(nt[i].get("exit_price", 0)))
            pd_ = abs(float(naut[i].get("pnl_pips", 0)) - float(nt[i].get("pnl_pips", 0)))
            print(f"  #{i+1}: entry差={ed:.4f}  exit差={xd:.4f}  PnL差={pd_:.1f}pips")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Nautilus Trader バックテスト")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="バックテスト実行")
    run_p.add_argument("--symbol", default="USDJPY", choices=SUPPORTED_SYMBOLS)
    run_p.add_argument("--start", required=True, help="開始日 (YYYY-MM-DD)")
    run_p.add_argument("--end", required=True, help="終了日 (YYYY-MM-DD)")
    run_p.add_argument(
        "--mode", default="bar", choices=["bar", "tick"],
        help="bar: M1バーで約定 (デフォルト), tick: 実ティックで約定",
    )

    cmp_p = sub.add_parser("compare", help="結果比較")
    cmp_p.add_argument("--native", required=True, help="独自BT結果JSON")
    cmp_p.add_argument("--nautilus", required=True, help="Nautilus結果JSON")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    {"run": cmd_run, "compare": cmd_compare}[args.command](args)


if __name__ == "__main__":
    main()
