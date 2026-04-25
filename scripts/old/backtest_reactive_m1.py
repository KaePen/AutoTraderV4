"""M1リアクティブモード専用バックテスト

既存BTパイプラインに依存せず、M1 CSVを直接読み込んで
インジケータを計算し、リアクティブシグナルを評価する。

使い方:
    uv run python scripts/backtest_reactive_m1.py --symbol USDJPY --year 2024
    uv run python scripts/backtest_reactive_m1.py --symbol USDJPY --start 2023 --end 2025
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# プロジェクトルート
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from autotrader.config.paths import get_data_dir
from autotrader.config.trading_params import get_preset


@dataclass
class M1ReactiveConfig:
    """M1リアクティブBT設定"""

    # ドンチャンチャネル
    donchian_period: int = 20  # 20分窓
    # ADXフィルター（M1計算）
    adx_period: int = 14
    adx_min_breakout: float = 15.0  # ブレイクアウト用（M1はADX低めで十分）
    adx_max_swing: float = 12.0  # スイング用（レンジ判定）
    # EMA
    ema_fast: int = 12
    ema_slow: int = 26
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 25.0
    rsi_overbought: float = 75.0
    # ATR
    atr_period: int = 14
    # SL/TP（ATR倍率）
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0
    swing_sl_atr_mult: float = 1.5
    swing_tp_atr_mult: float = 2.0
    # 最小SL/TP (pips)
    min_sl_pips: float = 5.0
    min_tp_pips: float = 5.0
    # クールダウン（足数）
    cooldown_bars: int = 5
    # SMA乖離（ATR比）スイング閾値
    swing_deviation_threshold: float = 1.5
    # 初期資金・ロット
    initial_balance: float = 1_000_000
    lot: float = 1.0
    # スプレッド
    spread_pips: float = 1.5


@dataclass
class Position:
    """シンプルなポジション"""

    direction: str  # BUY or SELL
    entry_price: float
    sl_price: float
    tp_price: float
    lot: float
    entry_time: str
    entry_bar: int
    signal_type: str  # BREAKOUT or SWING


@dataclass
class TradeResult:
    """トレード結果"""

    direction: str
    entry_price: float
    exit_price: float
    sl_price: float
    tp_price: float
    pnl_pips: float
    pnl_yen: float
    entry_time: str
    exit_time: str
    exit_reason: str
    signal_type: str
    holding_bars: int


def compute_indicators(df: pd.DataFrame, cfg: M1ReactiveConfig) -> pd.DataFrame:
    """M1データにインジケータを計算"""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # EMA
    df["ema_fast"] = close.ewm(span=cfg.ema_fast, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=cfg.ema_slow, adjust=False).mean()

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / cfg.rsi_period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / cfg.rsi_period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.ewm(span=cfg.atr_period, adjust=False).mean()

    # ADX
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # DMが相手より小さい場合は0
    plus_dm = plus_dm.where(plus_dm > minus_dm, 0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0)

    atr_smooth = df["atr"].replace(0, np.nan)
    plus_di = 100 * (plus_dm.ewm(span=cfg.adx_period, adjust=False).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.ewm(span=cfg.adx_period, adjust=False).mean() / atr_smooth)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(span=cfg.adx_period, adjust=False).mean()

    # SMA20
    df["sma20"] = close.rolling(20).mean()

    # MACD histogram
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = macd_line - signal_line

    # ドンチャンチャネル（現在足除く）
    df["dc_high"] = high.shift(1).rolling(cfg.donchian_period).max()
    df["dc_low"] = low.shift(1).rolling(cfg.donchian_period).min()

    return df


def run_backtest(
    df: pd.DataFrame,
    cfg: M1ReactiveConfig,
    pip_unit: float = 0.01,
    pip_value: float = 1000,
) -> list[TradeResult]:
    """M1バックテスト実行"""

    results: list[TradeResult] = []
    position: Position | None = None
    bars_since_signal = 999

    for i in range(cfg.donchian_period + cfg.atr_period + 30, len(df)):
        row = df.iloc[i]
        close = row["close"]
        high = row["high"]
        low = row["low"]
        atr = row["atr"]
        adx = row["adx"]
        rsi = row["rsi"]
        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        dc_high = row["dc_high"]
        dc_low = row["dc_low"]
        sma20 = row["sma20"]
        macd_hist = row["macd_hist"]
        bar_time = row["time"]

        if pd.isna(atr) or pd.isna(adx) or atr <= 0:
            continue

        atr_pips = atr / pip_unit

        # --- ポジション評価 ---
        if position is not None:
            exit_reason = None
            exit_price = close

            if position.direction == "BUY":
                if low <= position.sl_price:
                    exit_price = position.sl_price
                    exit_reason = "SL"
                elif high >= position.tp_price:
                    exit_price = position.tp_price
                    exit_reason = "TP"
            else:
                if high >= position.sl_price:
                    exit_price = position.sl_price
                    exit_reason = "SL"
                elif low <= position.tp_price:
                    exit_price = position.tp_price
                    exit_reason = "TP"

            if exit_reason:
                if position.direction == "BUY":
                    pnl_pips = (exit_price - position.entry_price) / pip_unit
                else:
                    pnl_pips = (position.entry_price - exit_price) / pip_unit

                pnl_yen = pnl_pips * pip_value * position.lot

                results.append(
                    TradeResult(
                        direction=position.direction,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        sl_price=position.sl_price,
                        tp_price=position.tp_price,
                        pnl_pips=round(pnl_pips, 1),
                        pnl_yen=round(pnl_yen, 0),
                        entry_time=position.entry_time,
                        exit_time=str(bar_time),
                        exit_reason=exit_reason,
                        signal_type=position.signal_type,
                        holding_bars=i - position.entry_bar,
                    )
                )
                position = None
            continue  # ポジション中はエントリーしない

        # --- クールダウン ---
        bars_since_signal += 1
        if bars_since_signal < cfg.cooldown_bars:
            continue

        # --- スプレッド控除 ---
        spread = cfg.spread_pips * pip_unit

        # --- ブレイクアウト検出 ---
        if not pd.isna(dc_high) and not pd.isna(dc_low):
            if close > dc_high and adx >= cfg.adx_min_breakout:
                # BUY: EMA + MACD確認
                if ema_f > ema_s and macd_hist > 0:
                    sl = close - atr * cfg.sl_atr_mult
                    tp = close + atr * cfg.tp_atr_mult
                    sl_pips = (close - sl) / pip_unit
                    tp_pips = (tp - close) / pip_unit
                    if sl_pips >= cfg.min_sl_pips and tp_pips >= cfg.min_tp_pips:
                        position = Position(
                            direction="BUY",
                            entry_price=close + spread / 2,
                            sl_price=sl,
                            tp_price=tp,
                            lot=cfg.lot,
                            entry_time=str(bar_time),
                            entry_bar=i,
                            signal_type="BREAKOUT",
                        )
                        bars_since_signal = 0
                        continue

            elif close < dc_low and adx >= cfg.adx_min_breakout:
                # SELL: EMA + MACD確認
                if ema_f < ema_s and macd_hist < 0:
                    sl = close + atr * cfg.sl_atr_mult
                    tp = close - atr * cfg.tp_atr_mult
                    sl_pips = (sl - close) / pip_unit
                    tp_pips = (close - tp) / pip_unit
                    if sl_pips >= cfg.min_sl_pips and tp_pips >= cfg.min_tp_pips:
                        position = Position(
                            direction="SELL",
                            entry_price=close - spread / 2,
                            sl_price=sl,
                            tp_price=tp,
                            lot=cfg.lot,
                            entry_time=str(bar_time),
                            entry_bar=i,
                            signal_type="BREAKOUT",
                        )
                        bars_since_signal = 0
                        continue

        # --- スイング検出 ---
        if adx <= cfg.adx_max_swing and not pd.isna(sma20) and sma20 > 0:
            deviation = (close - sma20) / atr

            # 下方乖離 + RSI過売 → BUY
            if deviation <= -cfg.swing_deviation_threshold and rsi <= cfg.rsi_oversold:
                sl = close - atr * cfg.swing_sl_atr_mult
                tp = close + atr * cfg.swing_tp_atr_mult
                sl_pips = (close - sl) / pip_unit
                tp_pips = (tp - close) / pip_unit
                if sl_pips >= cfg.min_sl_pips and tp_pips >= cfg.min_tp_pips:
                    position = Position(
                        direction="BUY",
                        entry_price=close + spread / 2,
                        sl_price=sl,
                        tp_price=tp,
                        lot=cfg.lot,
                        entry_time=str(bar_time),
                        entry_bar=i,
                        signal_type="SWING",
                    )
                    bars_since_signal = 0
                    continue

            # 上方乖離 + RSI過買 → SELL
            elif deviation >= cfg.swing_deviation_threshold and rsi >= cfg.rsi_overbought:
                sl = close + atr * cfg.swing_sl_atr_mult
                tp = close - atr * cfg.swing_tp_atr_mult
                sl_pips = (sl - close) / pip_unit
                tp_pips = (close - tp) / pip_unit
                if sl_pips >= cfg.min_sl_pips and tp_pips >= cfg.min_tp_pips:
                    position = Position(
                        direction="SELL",
                        entry_price=close - spread / 2,
                        sl_price=sl,
                        tp_price=tp,
                        lot=cfg.lot,
                        entry_time=str(bar_time),
                        entry_bar=i,
                        signal_type="SWING",
                    )
                    bars_since_signal = 0
                    continue

    return results


def load_m1_data(symbol: str, start_year: int, end_year: int) -> pd.DataFrame:
    """M1 CSVを読み込み"""
    data_dir = Path(get_data_dir()) / symbol / "chart" / "csv"
    m1_files = sorted(data_dir.glob(f"{symbol}_M1_*"))
    if not m1_files:
        raise FileNotFoundError(f"M1 data not found in {data_dir}")

    dfs = []
    for f in m1_files:
        df = pd.read_csv(f, sep="\t")
        df.columns = [c.strip("<>").lower() for c in df.columns]
        df["time"] = pd.to_datetime(df["date"] + " " + df["time"])
        df = df[["time", "open", "high", "low", "close", "tickvol", "spread"]]
        df = df[(df["time"].dt.year >= start_year) & (df["time"].dt.year <= end_year)]
        dfs.append(df)

    result = pd.concat(dfs, ignore_index=True).sort_values("time").reset_index(drop=True)
    print(f"  Loaded {len(result):,} M1 bars ({start_year}-{end_year})")
    return result


def print_results(results: list[TradeResult], cfg: M1ReactiveConfig) -> None:
    """結果サマリー出力"""
    if not results:
        print("  No trades.")
        return

    n = len(results)
    wins = [r for r in results if r.pnl_pips > 0]
    losses = [r for r in results if r.pnl_pips <= 0]
    total_pnl = sum(r.pnl_yen for r in results)
    wr = len(wins) / n * 100
    avg_win = np.mean([r.pnl_pips for r in wins]) if wins else 0
    avg_loss = np.mean([r.pnl_pips for r in losses]) if losses else 0
    avg_hold = np.mean([r.holding_bars for r in results])

    gross_win = sum(r.pnl_yen for r in wins) if wins else 0
    gross_loss = abs(sum(r.pnl_yen for r in losses)) if losses else 1
    pf = gross_win / gross_loss if gross_loss > 0 else 999

    # Signal type breakdown
    breakouts = [r for r in results if r.signal_type == "BREAKOUT"]
    swings = [r for r in results if r.signal_type == "SWING"]

    print(f"\n{'='*60}")
    print(f"  Trades:    {n}")
    print(f"  WR:        {wr:.1f}%")
    print(f"  PF:        {pf:.2f}")
    print(f"  Net P&L:   {total_pnl:+,.0f}円")
    print(f"  Avg win:   {avg_win:+.1f}p")
    print(f"  Avg loss:  {avg_loss:+.1f}p")
    print(f"  Avg hold:  {avg_hold:.0f}bars ({avg_hold:.0f}分)")
    print(f"  DD:        TODO")
    print(f"{'='*60}")

    # Direction
    buys = [r for r in results if r.direction == "BUY"]
    sells = [r for r in results if r.direction == "SELL"]
    buy_wr = len([r for r in buys if r.pnl_pips > 0]) / len(buys) * 100 if buys else 0
    sell_wr = len([r for r in sells if r.pnl_pips > 0]) / len(sells) * 100 if sells else 0
    print(f"  BUY:  {len(buys)} ({buy_wr:.0f}% WR)")
    print(f"  SELL: {len(sells)} ({sell_wr:.0f}% WR)")

    # Signal type
    if breakouts:
        bo_wr = len([r for r in breakouts if r.pnl_pips > 0]) / len(breakouts) * 100
        bo_pnl = sum(r.pnl_yen for r in breakouts)
        print(f"  BREAKOUT: {len(breakouts)} ({bo_wr:.0f}% WR) {bo_pnl:+,.0f}円")
    if swings:
        sw_wr = len([r for r in swings if r.pnl_pips > 0]) / len(swings) * 100
        sw_pnl = sum(r.pnl_yen for r in swings)
        print(f"  SWING:    {len(swings)} ({sw_wr:.0f}% WR) {sw_pnl:+,.0f}円")

    # Exit reason
    for reason in ["TP", "SL"]:
        subset = [r for r in results if r.exit_reason == reason]
        if subset:
            r_wr = len([r for r in subset if r.pnl_pips > 0]) / len(subset) * 100
            print(f"  {reason}: {len(subset)} ({r_wr:.0f}% WR)")


def main() -> None:
    parser = argparse.ArgumentParser(description="M1リアクティブBT")
    parser.add_argument("--symbol", default="USDJPY")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--start", type=int, default=2024)
    parser.add_argument("--end", type=int, default=2024)
    parser.add_argument("--donchian", type=int, default=20)
    parser.add_argument("--adx-min", type=float, default=15.0)
    parser.add_argument("--lot", type=float, default=1.0)
    args = parser.parse_args()

    start_year = args.year or args.start
    end_year = args.year or args.end

    preset = get_preset(args.symbol)
    pip_unit = 0.01 if args.symbol.endswith("JPY") else 0.0001
    pip_value = preset.pip_value

    cfg = M1ReactiveConfig(
        donchian_period=args.donchian,
        adx_min_breakout=args.adx_min,
        lot=args.lot,
        spread_pips=preset.spread_pips,
    )

    print(f"=== M1 REACTIVE BT: {args.symbol} {start_year}-{end_year} ===")
    print(f"  Donchian: {cfg.donchian_period} bars ({cfg.donchian_period} min)")
    print(f"  ADX breakout: >= {cfg.adx_min_breakout}")
    print(f"  ADX swing: <= {cfg.adx_max_swing}")
    print(f"  SL/TP: {cfg.sl_atr_mult}/{cfg.tp_atr_mult} ATR")
    print(f"  Spread: {cfg.spread_pips} pips")

    t0 = time.time()
    df = load_m1_data(args.symbol, start_year, end_year)
    print(f"  Data load: {time.time()-t0:.1f}s")

    t1 = time.time()
    df = compute_indicators(df, cfg)
    print(f"  Indicators: {time.time()-t1:.1f}s")

    t2 = time.time()
    results = run_backtest(df, cfg, pip_unit=pip_unit, pip_value=pip_value)
    print(f"  Backtest: {time.time()-t2:.1f}s")

    print_results(results, cfg)


if __name__ == "__main__":
    main()
