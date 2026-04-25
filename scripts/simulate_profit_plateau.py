"""Profit Plateau Exit シミュレーション.

既存BT結果のトレード + M1データを使い、
Profit Plateau exitを適用した場合のpipsを事後シミュレーションする。
各トレードについて「plateauで決済していたら何pipsだったか」を計算し、
実際の結果と比較する。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# --- 定数 ---
DATA_DIR = Path.home() / "projects" / "AutoTraderV4_data"
RESULT_DIR = DATA_DIR / "backtest" / "results" / "memory_v4.3.1_multiBT(2020-2022)"
CHART_DIR = DATA_DIR / "data"

PIP_UNITS: dict[str, float] = {
    "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01,
    "AUDJPY": 0.01, "CADJPY": 0.01, "CHFJPY": 0.01,
    "EURUSD": 0.0001, "GBPUSD": 0.0001,
}

SPREAD_PIPS: dict[str, float] = {
    "USDJPY": 1.5, "EURJPY": 2.0, "GBPJPY": 3.0,
    "AUDJPY": 2.0, "CADJPY": 2.5, "CHFJPY": 2.5,
    "EURUSD": 1.5, "GBPUSD": 2.0,
}


def load_all_trades() -> pd.DataFrame:
    """全月別JSONからtrade_rowsをロード."""
    rows: list[dict] = []
    for jf in sorted(RESULT_DIR.glob("20??_??.json")):
        with open(jf) as f:
            data = json.load(f)
        for tr in data.get("trade_rows", []):
            rows.append(tr)
    df = pd.DataFrame(rows)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["holding_minutes"] = (
        (df["exit_time"] - df["entry_time"]).dt.total_seconds() / 60.0
    )
    df["is_win"] = df["pips"] > 0
    return df


def load_m1_data(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """指定ペアのM1データを対象期間だけロード."""
    csv_dir = CHART_DIR / symbol / "chart" / "csv"
    m1_files = list(csv_dir.glob(f"{symbol}_M1_*.csv"))
    if not m1_files:
        return pd.DataFrame()
    df = pd.read_csv(
        m1_files[0], sep="\t",
        names=["date", "time", "open", "high", "low", "close", "tickvol", "vol", "spread"],
        skiprows=1,
    )
    df["date"] = pd.to_datetime(df["date"] + " " + df["time"], format="%Y.%m.%d %H:%M:%S")
    df.drop(columns=["time"], inplace=True)
    mask = (df["date"] >= start - timedelta(days=1)) & (df["date"] <= end + timedelta(days=1))
    return df.loc[mask].reset_index(drop=True)


def simulate_plateau_exit(
    trade: dict,
    m1: pd.DataFrame,
    pip_unit: float,
    min_mfe_r: float = 0.15,
    stall_minutes: float = 15.0,
    min_current_r: float = 0.0,
    retreat_threshold: float = 0.40,
    retreat_multiplier: float = 0.5,
) -> dict:
    """1トレードに対してPlateau exitをシミュレーション.

    Returns:
        dict with keys:
        - plateau_triggered: bool
        - plateau_exit_time: datetime or None
        - plateau_exit_pips: float
        - plateau_exit_minutes: float (entry→plateau exit)
        - actual_pips: float
        - actual_minutes: float
    """
    entry_time = trade["entry_time"]
    exit_time = trade["exit_time"]
    entry_price = trade["entry_price"]
    direction = trade["direction"]
    sl_pips = trade["sl_pips"]
    r_value = sl_pips * pip_unit  # 1R in price terms

    m1_times = m1["date"].values
    m1_closes = m1["close"].values

    mask = (m1_times >= np.datetime64(entry_time)) & (m1_times <= np.datetime64(exit_time))
    candle_idx = np.where(mask)[0]

    result = {
        "plateau_triggered": False,
        "plateau_exit_time": None,
        "plateau_exit_pips": 0.0,
        "plateau_exit_minutes": 0.0,
        "actual_pips": trade["pips"],
        "actual_minutes": trade["holding_minutes"],
    }

    if len(candle_idx) == 0 or r_value <= 0:
        return result

    highest_r = 0.0
    mfe_last_update_idx = candle_idx[0]

    for idx in candle_idx:
        close = m1_closes[idx]
        if direction == "BUY":
            current_r = (close - entry_price) / r_value
        else:
            current_r = (entry_price - close) / r_value

        # MFE更新
        if current_r > highest_r:
            highest_r = current_r
            mfe_last_update_idx = idx

        # Plateau判定
        if highest_r < min_mfe_r:
            continue
        if current_r < min_current_r:
            continue

        stall_min = (
            pd.Timestamp(m1_times[idx]) - pd.Timestamp(m1_times[mfe_last_update_idx])
        ).total_seconds() / 60.0

        effective_stall = stall_minutes
        if highest_r > 0:
            retreat_ratio = (highest_r - current_r) / highest_r
            if retreat_ratio >= retreat_threshold:
                effective_stall *= retreat_multiplier

        if stall_min >= effective_stall:
            exit_t = pd.Timestamp(m1_times[idx])
            elapsed = (exit_t - entry_time).total_seconds() / 60.0
            exit_pips = current_r * sl_pips
            result["plateau_triggered"] = True
            result["plateau_exit_time"] = exit_t
            result["plateau_exit_pips"] = round(exit_pips, 2)
            result["plateau_exit_minutes"] = round(elapsed, 1)
            break

    return result


def run_simulation(
    stall_minutes: float = 15.0,
    min_mfe_r: float = 0.15,
) -> pd.DataFrame:
    """全トレードに対してシミュレーション実行."""
    trades = load_all_trades()
    print(f"Loaded {len(trades)} trades")

    all_results: list[dict] = []
    symbols = sorted(trades["symbol"].unique())

    for symbol in symbols:
        sym_trades = trades[trades["symbol"] == symbol]
        pip_unit = PIP_UNITS.get(symbol, 0.01)
        start = sym_trades["entry_time"].min()
        end = sym_trades["exit_time"].max()

        m1 = load_m1_data(symbol, start, end)
        if m1.empty:
            continue

        for _, trade in sym_trades.iterrows():
            sim = simulate_plateau_exit(
                trade, m1, pip_unit,
                min_mfe_r=min_mfe_r,
                stall_minutes=stall_minutes,
            )
            sim["symbol"] = symbol
            sim["direction"] = trade["direction"]
            sim["regime"] = trade["regime"]
            sim["exit_reason"] = trade["exit_reason"]
            sim["sl_pips"] = trade["sl_pips"]
            sim["mfe_pips"] = trade["mfe_pips"]
            all_results.append(sim)

        del m1

    return pd.DataFrame(all_results)


def print_comparison(df: pd.DataFrame, label: str) -> None:
    """Plateau vs 実際の比較を出力."""
    triggered = df[df["plateau_triggered"]]
    not_triggered = df[~df["plateau_triggered"]]

    total = len(df)
    n_triggered = len(triggered)
    pct = n_triggered / total * 100 if total > 0 else 0

    # Plateau適用後のpips: triggeredはplateau_exit_pips、それ以外はactual_pips
    df = df.copy()
    df["simulated_pips"] = np.where(
        df["plateau_triggered"],
        df["plateau_exit_pips"],
        df["actual_pips"],
    )

    actual_total = df["actual_pips"].sum()
    sim_total = df["simulated_pips"].sum()
    diff = sim_total - actual_total

    actual_avg = df["actual_pips"].mean()
    sim_avg = df["simulated_pips"].mean()

    actual_wr = (df["actual_pips"] > 0).mean()
    sim_wr = (df["simulated_pips"] > 0).mean()

    # 保有時間比較
    df["simulated_minutes"] = np.where(
        df["plateau_triggered"],
        df["plateau_exit_minutes"],
        df["actual_minutes"],
    )
    actual_hold = df["actual_minutes"].mean()
    sim_hold = df["simulated_minutes"].mean()

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  トレード数: {total}")
    print(f"  Plateau発火: {n_triggered} ({pct:.1f}%)")
    print(f"")
    print(f"  {'指標':<20} {'実際':>12} {'Plateau適用':>12} {'差分':>10}")
    print(f"  {'-'*56}")
    print(f"  {'合計pips':<20} {actual_total:>12.0f} {sim_total:>12.0f} {diff:>+10.0f}")
    print(f"  {'平均pips/trade':<20} {actual_avg:>12.2f} {sim_avg:>12.2f} {sim_avg-actual_avg:>+10.2f}")
    print(f"  {'勝率':<20} {actual_wr:>11.1%} {sim_wr:>11.1%} {(sim_wr-actual_wr)*100:>+9.1f}pp")
    print(f"  {'平均保有(分)':<20} {actual_hold:>12.0f} {sim_hold:>12.0f} {sim_hold-actual_hold:>+10.0f}")

    if n_triggered > 0:
        print(f"\n  Plateau発火トレードの詳細 (n={n_triggered}):")
        t = triggered
        print(f"    実際平均pips:    {t['actual_pips'].mean():>8.2f}")
        print(f"    Plateau平均pips: {t['plateau_exit_pips'].mean():>8.2f}")
        print(f"    差分:            {t['plateau_exit_pips'].mean() - t['actual_pips'].mean():>+8.2f}")
        print(f"    平均exit時間:    {t['plateau_exit_minutes'].mean():>8.0f}分")

        # Plateau exitが有利だったケース
        better = t[t["plateau_exit_pips"] > t["actual_pips"]]
        worse = t[t["plateau_exit_pips"] < t["actual_pips"]]
        print(f"    Plateau有利: {len(better)} ({len(better)/n_triggered*100:.0f}%)")
        print(f"    Plateau不利: {len(worse)} ({len(worse)/n_triggered*100:.0f}%)")


def main() -> None:
    """メイン."""
    print("=" * 60)
    print("Profit Plateau Exit シミュレーション")
    print("=" * 60)

    # 複数パラメータで比較
    configs = [
        {"stall_minutes": 10.0, "min_mfe_r": 0.15, "label": "stall=10min, mfe>=0.15R"},
        {"stall_minutes": 15.0, "min_mfe_r": 0.15, "label": "stall=15min, mfe>=0.15R"},
        {"stall_minutes": 20.0, "min_mfe_r": 0.15, "label": "stall=20min, mfe>=0.15R"},
        {"stall_minutes": 15.0, "min_mfe_r": 0.10, "label": "stall=15min, mfe>=0.10R"},
        {"stall_minutes": 15.0, "min_mfe_r": 0.20, "label": "stall=15min, mfe>=0.20R"},
    ]

    for cfg in configs:
        df = run_simulation(
            stall_minutes=cfg["stall_minutes"],
            min_mfe_r=cfg["min_mfe_r"],
        )
        print_comparison(df, cfg["label"])

    print("\n" + "=" * 60)
    print("シミュレーション完了")
    print("=" * 60)


if __name__ == "__main__":
    main()
