"""Nautilus Trader バックテスト検証スクリプト

独自シミュレーターの精度を検証するため、同じシグナル生成パイプラインを
Nautilus Traderの約定エンジンで実行し、結果を比較する。

Usage:
    # ティックデータ取得（Windows MT5環境で実行）
    uv run python scripts/nautilus_backtest.py fetch --symbol USDJPY --start 2026-01-01 --end 2026-03-31

    # バックテスト実行
    uv run python scripts/nautilus_backtest.py run --symbol USDJPY --start 2026-01-01 --end 2026-03-31

    # 結果比較
    uv run python scripts/nautilus_backtest.py compare --native results/xxx.json --nautilus results/yyy.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

# AutoTraderV4 のルートをパスに追加
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

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# サポート通貨ペア（JPYクロス + 主要ペア）
SUPPORTED_SYMBOLS = [
    "USDJPY",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "CADJPY",
    "CHFJPY",
    "EURUSD",
    "GBPUSD",
]

# Nautilus形式のシンボル名マッピング（MT5 → Nautilus）
_NAUTILUS_SYMBOL_MAP = {
    "USDJPY": "USD/JPY",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "AUDJPY": "AUD/JPY",
    "CADJPY": "CAD/JPY",
    "CHFJPY": "CHF/JPY",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
}

# バックテスト対象タイムフレーム
_TIMEFRAMES_FOR_INDICATORS = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]

# 主要時間足（シグナル生成のトリガー）
_PRIMARY_TF = "M15"
_PRIMARY_TF_MINUTES = 15


def _get_data_dir() -> Path:
    """データディレクトリを取得（プラットフォーム自動検出）."""
    from autotrader.config.paths import get_data_dir

    return Path(get_data_dir())


# ---------------------------------------------------------------------------
# fetch サブコマンド: MT5からティックデータを取得
# ---------------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> None:
    """MT5からティックデータを取得してParquetに保存."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(
            "ERROR: MetaTrader5パッケージが見つかりません。"
            "Windows環境で pip install MetaTrader5 を実行してください。"
        )
        sys.exit(1)

    symbol = args.symbol
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)

    print(f"MT5初期化中...")
    if not mt5.initialize():
        print(f"ERROR: MT5初期化失敗: {mt5.last_error()}")
        sys.exit(1)

    try:
        print(f"ティックデータ取得: {symbol} {start.date()} → {end.date()}")

        # 月ごとに分割取得（メモリ対策）
        current = start
        all_chunks: list[Path] = []

        while current < end:
            month_end = min(
                current.replace(day=1) + timedelta(days=32),
                end,
            )
            month_end = month_end.replace(day=1)
            if month_end <= current:
                month_end = end

            print(f"  取得中: {current.date()} → {month_end.date()} ...", end=" ")

            ticks = mt5.copy_ticks_range(
                symbol,
                current,
                month_end,
                mt5.COPY_TICKS_ALL,
            )

            if ticks is None or len(ticks) == 0:
                print(f"データなし")
                current = month_end
                continue

            # DataFrame変換
            df = pd.DataFrame(ticks)
            # time_msc（ミリ秒）→ datetime
            df["timestamp"] = pd.to_datetime(df["time_msc"], unit="ms", utc=True)
            df = df[["timestamp", "bid", "ask", "volume", "flags"]]
            df = df.set_index("timestamp")

            # Parquet保存
            data_dir = _get_data_dir()
            tick_dir = data_dir / symbol / "ticks"
            tick_dir.mkdir(parents=True, exist_ok=True)

            filename = f"ticks_{current.strftime('%Y%m')}.parquet"
            out_path = tick_dir / filename
            df.to_parquet(out_path, engine="pyarrow", compression="snappy")

            print(f"{len(df):,} ticks → {out_path.name}")
            all_chunks.append(out_path)

            current = month_end

        print(f"\n完了: {len(all_chunks)} ファイル保存")

    finally:
        mt5.shutdown()


# ---------------------------------------------------------------------------
# run サブコマンド: Nautilus Traderでバックテスト実行
# ---------------------------------------------------------------------------


def _load_ohlcv_data(
    symbol: str,
    start: datetime,
    end: datetime,
) -> dict[str, pd.DataFrame]:
    """全タイムフレームのOHLCVデータを読み込み、インジケータを事前計算."""
    data_dir = _get_data_dir()
    symbol_dir = data_dir / symbol
    precompute = PrecomputeEngine()

    market_data: dict[str, pd.DataFrame] = {}

    # chartサブディレクトリの判定（既存runnerと同じロジック）
    chart_dir = symbol_dir / "chart"
    base_dir = chart_dir if chart_dir.exists() else symbol_dir

    for tf in _TIMEFRAMES_FOR_INDICATORS:
        df: pd.DataFrame | None = None

        # 優先1: chart/cache/ Parquet
        cache_pq = base_dir / "cache" / f"{symbol}_{tf}.parquet"
        if cache_pq.exists():
            try:
                df = pd.read_parquet(cache_pq)
                logger.info(f"  {tf}: {len(df)} bars (parquet cache)")
            except Exception as e:
                logger.warning(f"Parquet読込失敗 {cache_pq}: {e}")

        # 優先2: 従来CSV（base_dir内）
        if df is None:
            pattern = f"{symbol}_{tf}_*.csv"
            csv_files = sorted(base_dir.glob(pattern))
            # base_dirとsymbol_dirが異なる場合、symbol_dir側も検索
            if not csv_files and base_dir != symbol_dir:
                csv_files = sorted(symbol_dir.glob(pattern))
            if csv_files:
                dfs = [DataLoader.load_mt5_csv(f) for f in csv_files]
                df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["time"])
                df = df.sort_values("time").reset_index(drop=True)
                logger.info(f"  {tf}: {len(df)} bars (csv)")

        if df is None:
            logger.warning(f"データなし: {symbol} {tf}")
            continue

        # インジケータ事前計算
        df = precompute.compute_technical_indicators(df)

        market_data[tf] = df

    return market_data


def _load_tick_data(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """ティックデータをParquetから読み込み."""
    data_dir = _get_data_dir()
    tick_dir = data_dir / symbol / "ticks"

    if not tick_dir.exists():
        raise FileNotFoundError(
            f"ティックデータが見つかりません: {tick_dir}\n"
            f"先に fetch コマンドでデータを取得してください。"
        )

    parquet_files = sorted(tick_dir.glob("ticks_*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"Parquetファイルなし: {tick_dir}")

    dfs = [pd.read_parquet(f) for f in parquet_files]
    df = pd.concat(dfs)

    # 期間フィルタ
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")

    df = df.loc[start:end]
    logger.info(f"ティックデータ: {len(df):,} ticks ({start.date()} → {end.date()})")

    return df


def _create_nautilus_instrument(symbol: str) -> Any:
    """Nautilus用のFXインストルメントを作成."""
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    venue = Venue("SIM")
    nautilus_symbol = _NAUTILUS_SYMBOL_MAP[symbol]
    return TestInstrumentProvider.default_fx_ccy(nautilus_symbol, venue)


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
    except ImportError:
        print(
            "ERROR: nautilus_trader が見つかりません。\n"
            "pip install nautilus_trader でインストールしてください。"
        )
        sys.exit(1)

    symbol = args.symbol
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    preset = get_preset(symbol)

    print(f"=== Nautilus Trader バックテスト ===")
    print(f"通貨ペア: {symbol}")
    print(f"期間: {start.date()} → {end.date()}")
    print()

    # 1. OHLCVデータ読み込み & インジケータ事前計算
    print("1. OHLCVデータ読み込み & インジケータ計算...")
    market_data = _load_ohlcv_data(symbol, start, end)

    if _PRIMARY_TF not in market_data:
        print(f"ERROR: {_PRIMARY_TF}データが見つかりません")
        sys.exit(1)

    # 2. TradeBot初期化 & データ設定
    print("2. TradeBot初期化...")
    bot_config = UnifiedBotConfig()
    bot = UnifiedTradeBot(bot_config)
    bot.set_market_data(market_data)

    # 3. ティックデータ読み込み
    print("3. ティックデータ読み込み...")
    tick_df = _load_tick_data(symbol, start, end)

    # 4. Nautilusエンジン構築
    print("4. Nautilusエンジン構築...")
    engine_config = BacktestEngineConfig(
        trader_id=TraderId("BACKTESTER-001"),
        logging=LoggingConfig(log_level="WARNING"),
    )
    engine = BacktestEngine(config=engine_config)

    # Venue設定
    sim_venue = Venue("SIM")
    fill_model = FillModel(
        prob_fill_on_limit=0.2,
        prob_slippage=0.5,
        random_seed=42,
    )

    # 口座通貨の判定
    is_jpy_account = preset.quote_ccy_rate == 1.0
    base_currency = JPY if is_jpy_account else USD
    initial_balance = 1_000_000 if is_jpy_account else 10_000

    engine.add_venue(
        venue=sim_venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=base_currency,
        starting_balances=[Money(initial_balance, base_currency)],
        fill_model=fill_model,
    )

    # インストルメント追加
    instrument = _create_nautilus_instrument(symbol)
    engine.add_instrument(instrument)

    # ティックデータ追加
    wrangler = QuoteTickDataWrangler(instrument=instrument)
    # bid/ask列名を確認して変換
    if "bid" in tick_df.columns and "ask" in tick_df.columns:
        wrangler_df = tick_df[["bid", "ask"]].copy()
        wrangler_df.columns = ["bid_price", "ask_price"]
    elif "bid_price" in tick_df.columns:
        wrangler_df = tick_df[["bid_price", "ask_price"]].copy()
    else:
        raise ValueError(
            f"ティックデータに bid/ask 列がありません: {tick_df.columns.tolist()}"
        )

    ticks = wrangler.process(wrangler_df)
    engine.add_data(ticks)
    print(f"   {len(ticks):,} ticks loaded")

    # 5. ストラテジー追加
    print("5. ストラテジー構築...")

    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.data import Bar, BarType, QuoteTick
    from nautilus_trader.model.enums import OrderSide, TimeInForce
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.orders.list import OrderList
    from nautilus_trader.trading.strategy import Strategy

    class _VerificationStrategyConfig(StrategyConfig, frozen=True):
        """検証ストラテジー設定."""

        instrument_id_str: str = ""
        bar_type_str: str = ""
        symbol: str = "USDJPY"
        pip_unit: float = 0.01
        default_lot: float = 0.1

    class VerificationStrategy(Strategy):
        """AutoTraderV4シグナル → Nautilus約定の検証ストラテジー.

        M15バー確定時にAutoTraderV4のgenerate_signal()を呼び出し、
        ブラケット注文（エントリー + SL + TP）をNautilusで実行する。
        """

        def __init__(
            self,
            config: _VerificationStrategyConfig,
            trade_bot: UnifiedTradeBot,
            market_data: dict[str, pd.DataFrame],
        ) -> None:
            super().__init__(config)
            self._trade_bot = trade_bot
            self._market_data = market_data
            self._instrument: Instrument | None = None
            self._pip_unit = config.pip_unit
            self._default_lot = config.default_lot
            self._symbol = config.symbol
            self._trade_log: list[dict[str, Any]] = []

        def on_start(self) -> None:
            instrument_id = self.config.instrument_id_str
            from nautilus_trader.model.identifiers import InstrumentId

            iid = InstrumentId.from_str(instrument_id)
            self._instrument = self.cache.instrument(iid)
            if self._instrument is None:
                self.log.error(f"Instrument not found: {instrument_id}")
                self.stop()
                return

            # M15バーをサブスクライブ（ティックから内部集約）
            bar_type = BarType.from_str(self.config.bar_type_str)
            self.subscribe_bars(bar_type)
            self.subscribe_quote_ticks(iid)
            self.log.info(f"Strategy started: {self._symbol}")

        def on_bar(self, bar: Bar) -> None:
            """M15バー確定時にシグナル生成 → 注文."""
            if self._instrument is None:
                return

            # ポジション保有中はスキップ（段階A: 単一ポジション）
            if not self.portfolio.is_flat(self._instrument.id):
                return

            # 現在時刻をpd.Timestampに変換
            bar_time = pd.Timestamp(
                bar.ts_event, unit="ns", tz="UTC"
            )

            # AutoTraderV4のシグナル生成
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

            consolidated = self._trade_bot.generate_signal(bar_time, candle)

            if consolidated.direction == SignalType.HOLD:
                return
            if consolidated.confidence < 0.5:
                return
            if consolidated.sl_pips <= 0 or consolidated.tp_pips <= 0:
                return

            # SL/TP価格を計算
            close_price = float(bar.close)
            if consolidated.direction == SignalType.BUY:
                sl_price = close_price - consolidated.sl_pips * self._pip_unit
                tp_price = close_price + consolidated.tp_pips * self._pip_unit
            else:
                sl_price = close_price + consolidated.sl_pips * self._pip_unit
                tp_price = close_price - consolidated.tp_pips * self._pip_unit

            # ロットサイズ
            lot = consolidated.lot if consolidated.lot else self._default_lot

            # ブラケット注文（エントリー + SL + TP）
            order_side = (
                OrderSide.BUY
                if consolidated.direction == SignalType.BUY
                else OrderSide.SELL
            )

            order_list: OrderList = self.order_factory.bracket(
                instrument_id=self._instrument.id,
                order_side=order_side,
                quantity=self._instrument.make_qty(Decimal(str(lot))),
                sl_trigger_price=self._instrument.make_price(sl_price),
                tp_price=self._instrument.make_price(tp_price),
            )

            self.submit_order_list(order_list)

            self.log.info(
                f"SIGNAL: {consolidated.direction.name} "
                f"@ {close_price:.3f} "
                f"SL={sl_price:.3f} TP={tp_price:.3f} "
                f"lot={lot} "
                f"consensus={consolidated.consensus_score}"
            )

        def on_position_closed(self, event: Any) -> None:
            """ポジションクローズ時にログ記録."""
            pos = self.cache.position(event.position_id)
            if pos is None:
                return

            entry_price = float(pos.avg_px_open)
            exit_price = float(pos.avg_px_close)
            pnl = float(pos.realized_pnl)

            trade_record = {
                "position_id": str(pos.id),
                "symbol": self._symbol,
                "direction": "BUY" if pos.is_long else "SELL",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "volume": float(pos.quantity),
                "pnl": pnl,
                "pnl_pips": (exit_price - entry_price) / self._pip_unit
                if pos.is_long
                else (entry_price - exit_price) / self._pip_unit,
                "opened_at": str(pos.ts_opened),
                "closed_at": str(pos.ts_closed),
                "duration_s": (pos.ts_closed - pos.ts_opened) / 1e9,
            }
            self._trade_log.append(trade_record)

            self.log.info(
                f"CLOSED: {trade_record['direction']} "
                f"entry={entry_price:.3f} exit={exit_price:.3f} "
                f"PnL={pnl:.0f} ({trade_record['pnl_pips']:.1f} pips)"
            )

        def on_stop(self) -> None:
            """終了時にポジションをクローズ."""
            if self._instrument:
                self.cancel_all_orders(self._instrument.id)
                self.close_all_positions(self._instrument.id)

        def get_trade_log(self) -> list[dict[str, Any]]:
            """トレードログを取得."""
            return list(self._trade_log)

    # ストラテジーインスタンス生成
    nautilus_symbol = _NAUTILUS_SYMBOL_MAP[symbol]
    bar_type_str = f"{nautilus_symbol}.SIM-{_PRIMARY_TF_MINUTES}-MINUTE-MID-INTERNAL"

    strategy_config = _VerificationStrategyConfig(
        instrument_id_str=f"{nautilus_symbol}.SIM",
        bar_type_str=bar_type_str,
        symbol=symbol,
        pip_unit=preset.pip_unit,
        default_lot=preset.min_lot,
    )
    strategy = VerificationStrategy(
        config=strategy_config,
        trade_bot=bot,
        market_data=market_data,
    )
    engine.add_strategy(strategy)

    # 6. バックテスト実行
    print("6. バックテスト実行中...")
    engine.run()

    # 7. 結果出力
    print("\n=== 結果 ===")

    # Nautilusレポート
    positions_report = engine.trader.generate_positions_report()
    fills_report = engine.trader.generate_order_fills_report()

    trade_log = strategy.get_trade_log()
    print(f"トレード数: {len(trade_log)}")

    if trade_log:
        wins = [t for t in trade_log if t["pnl"] > 0]
        losses = [t for t in trade_log if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trade_log)
        win_rate = len(wins) / len(trade_log) * 100

        print(f"勝率: {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
        print(f"総損益: {total_pnl:,.0f}")
        print(f"平均損益(pips): {sum(t['pnl_pips'] for t in trade_log) / len(trade_log):.1f}")

        if wins:
            print(f"平均利益(pips): {sum(t['pnl_pips'] for t in wins) / len(wins):.1f}")
        if losses:
            print(f"平均損失(pips): {sum(t['pnl_pips'] for t in losses) / len(losses):.1f}")

    # 結果をJSONに保存
    results_dir = _ROOT / "backtest_results" / "nautilus"
    results_dir.mkdir(parents=True, exist_ok=True)

    result_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = results_dir / f"{symbol}_{result_id}.json"

    result_data = {
        "engine": "nautilus_trader",
        "symbol": symbol,
        "period": {"start": args.start, "end": args.end},
        "initial_balance": initial_balance,
        "trades": trade_log,
        "summary": {
            "total_trades": len(trade_log),
            "win_rate": win_rate if trade_log else 0,
            "total_pnl": total_pnl if trade_log else 0,
        },
    }

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n結果保存: {result_path}")

    # Nautilusポジションレポート
    if not positions_report.empty:
        print(f"\n--- Nautilus Positions Report ---")
        print(positions_report.to_string())

    engine.reset()
    engine.dispose()


# ---------------------------------------------------------------------------
# compare サブコマンド: 独自BT結果 vs Nautilus結果を比較
# ---------------------------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> None:
    """独自BT結果とNautilus結果を比較."""
    native_path = Path(args.native)
    nautilus_path = Path(args.nautilus)

    if not native_path.exists():
        print(f"ERROR: ファイルが見つかりません: {native_path}")
        sys.exit(1)
    if not nautilus_path.exists():
        print(f"ERROR: ファイルが見つかりません: {nautilus_path}")
        sys.exit(1)

    with open(native_path, encoding="utf-8") as f:
        native = json.load(f)
    with open(nautilus_path, encoding="utf-8") as f:
        nautilus = json.load(f)

    native_trades = native.get("trades", [])
    nautilus_trades = nautilus.get("trades", [])

    print("=== バックテスト結果比較 ===")
    print(f"独自BT: {native_path.name}")
    print(f"Nautilus: {nautilus_path.name}")
    print()

    # サマリー比較
    headers = ["指標", "独自BT", "Nautilus", "差異"]
    rows: list[list[str]] = []

    n_native = len(native_trades)
    n_nautilus = len(nautilus_trades)
    rows.append([
        "トレード数",
        str(n_native),
        str(n_nautilus),
        str(n_nautilus - n_native),
    ])

    native_summary = native.get("summary", {})
    nautilus_summary = nautilus.get("summary", {})

    wr_native = native_summary.get("win_rate", 0)
    wr_nautilus = nautilus_summary.get("win_rate", 0)
    rows.append([
        "勝率(%)",
        f"{wr_native:.1f}",
        f"{wr_nautilus:.1f}",
        f"{wr_nautilus - wr_native:+.1f}",
    ])

    pnl_native = native_summary.get("total_pnl", 0)
    pnl_nautilus = nautilus_summary.get("total_pnl", 0)
    pnl_diff_pct = (
        (pnl_nautilus - pnl_native) / abs(pnl_native) * 100
        if pnl_native != 0
        else 0
    )
    rows.append([
        "総損益",
        f"{pnl_native:,.0f}",
        f"{pnl_nautilus:,.0f}",
        f"{pnl_diff_pct:+.1f}%",
    ])

    # テーブル表示
    col_widths = [
        max(len(str(row[i])) for row in [headers] + rows)
        for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("-" * sum(col_widths + [2 * (len(headers) - 1)]))
    for row in rows:
        print(fmt.format(*row))

    # 判定
    print()
    if abs(pnl_diff_pct) <= 5:
        print("判定: ✓ 一致（差異5%以内）")
        print("→ 独自シミュレーターは信頼できます。")
        print("→ BT/ライブ乖離は外部要因（ファンダフィルター、エントリーゲート等）が原因です。")
    else:
        print("判定: ✗ 不一致（差異5%超）")
        print("→ 独自シミュレーターに問題がある可能性があります。")
        print("→ Nautilus結果を正として、シミュレーターの修正箇所を特定してください。")

    # トレード詳細比較（トレード数が一致する場合）
    if n_native == n_nautilus and n_native > 0:
        print(f"\n--- トレード詳細比較 (先頭10件) ---")
        for i in range(min(10, n_native)):
            nt = native_trades[i]
            naut = nautilus_trades[i]

            entry_diff = abs(
                float(naut.get("entry_price", 0)) - float(nt.get("entry_price", 0))
            )
            exit_diff = abs(
                float(naut.get("exit_price", 0)) - float(nt.get("exit_price", 0))
            )
            pnl_diff = abs(
                float(naut.get("pnl_pips", 0)) - float(nt.get("pnl_pips", 0))
            )

            print(
                f"  #{i + 1}: "
                f"entry差={entry_diff:.4f} "
                f"exit差={exit_diff:.4f} "
                f"PnL差={pnl_diff:.1f}pips"
            )


# ---------------------------------------------------------------------------
# CLI エントリーポイント
# ---------------------------------------------------------------------------


def main() -> None:
    """メインエントリーポイント."""
    parser = argparse.ArgumentParser(
        description="Nautilus Trader バックテスト検証",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="サブコマンド")

    # fetch
    fetch_parser = subparsers.add_parser(
        "fetch", help="MT5からティックデータを取得"
    )
    fetch_parser.add_argument(
        "--symbol", default="USDJPY", choices=SUPPORTED_SYMBOLS
    )
    fetch_parser.add_argument(
        "--start", required=True, help="開始日 (YYYY-MM-DD)"
    )
    fetch_parser.add_argument(
        "--end", required=True, help="終了日 (YYYY-MM-DD)"
    )

    # run
    run_parser = subparsers.add_parser(
        "run", help="Nautilusバックテスト実行"
    )
    run_parser.add_argument(
        "--symbol", default="USDJPY", choices=SUPPORTED_SYMBOLS
    )
    run_parser.add_argument(
        "--start", required=True, help="開始日 (YYYY-MM-DD)"
    )
    run_parser.add_argument(
        "--end", required=True, help="終了日 (YYYY-MM-DD)"
    )

    # compare
    compare_parser = subparsers.add_parser(
        "compare", help="結果比較"
    )
    compare_parser.add_argument(
        "--native", required=True, help="独自BT結果JSONパス"
    )
    compare_parser.add_argument(
        "--nautilus", required=True, help="Nautilus結果JSONパス"
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    commands = {
        "fetch": cmd_fetch,
        "run": cmd_run,
        "compare": cmd_compare,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
