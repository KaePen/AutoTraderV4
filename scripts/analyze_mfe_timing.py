"""MFE到達時間分析 — D1シグナルの「賞味期限」を測定する.

既存BT結果のトレードデータ + M1ローソク足から、
各トレードのMFE（最大含み益）到達時刻を事後計算し、
D1シグナルの方向予測の有効期間を定量化する。
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
    "USDJPY": 0.01,
    "EURJPY": 0.01,
    "GBPJPY": 0.01,
    "AUDJPY": 0.01,
    "CADJPY": 0.01,
    "CHFJPY": 0.01,
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "AUDUSD": 0.0001,
    "NZDUSD": 0.0001,
    "USDCHF": 0.0001,
    "USDCAD": 0.0001,
}

SPREAD_PIPS: dict[str, float] = {
    "USDJPY": 1.5,
    "EURJPY": 2.0,
    "GBPJPY": 3.0,
    "AUDJPY": 2.0,
    "CADJPY": 2.5,
    "CHFJPY": 2.5,
    "EURUSD": 1.5,
    "GBPUSD": 2.0,
}


def load_all_trades() -> pd.DataFrame:
    """全月別JSONからtrade_rowsをロードしDataFrameに統合."""
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
    print(f"Loaded {len(df)} trades from {len(list(RESULT_DIR.glob('20??_??.json')))} month files")
    print(f"  Symbols: {sorted(df['symbol'].unique())}")
    print(f"  Period: {df['entry_time'].min()} ~ {df['exit_time'].max()}")
    print(f"  Win rate: {df['is_win'].mean():.1%}")
    return df


def load_m1_data(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """指定ペアのM1データを対象期間だけロード."""
    csv_dir = CHART_DIR / symbol / "chart" / "csv"
    m1_files = list(csv_dir.glob(f"{symbol}_M1_*.csv"))
    if not m1_files:
        print(f"  WARNING: No M1 data for {symbol}")
        return pd.DataFrame()

    m1_path = m1_files[0]
    df = pd.read_csv(
        m1_path,
        sep="\t",
        names=["date", "time", "open", "high", "low", "close", "tickvol", "vol", "spread"],
        skiprows=1,
    )
    df["date"] = pd.to_datetime(
        df["date"] + " " + df["time"], format="%Y.%m.%d %H:%M:%S"
    )
    df.drop(columns=["time"], inplace=True)
    # 期間フィルタ（前後1日のマージン）
    mask = (df["date"] >= start - timedelta(days=1)) & (
        df["date"] <= end + timedelta(days=1)
    )
    df = df.loc[mask].reset_index(drop=True)
    return df


def compute_mfe_timing(
    trades: pd.DataFrame, m1: pd.DataFrame, symbol: str
) -> list[dict]:
    """各トレードのMFE到達時刻をM1データから計算."""
    pip_unit = PIP_UNITS.get(symbol, 0.01)
    results: list[dict] = []

    m1_times = m1["date"].values
    m1_highs = m1["high"].values
    m1_lows = m1["low"].values

    sym_trades = trades[trades["symbol"] == symbol]

    for _, trade in sym_trades.iterrows():
        entry_time = trade["entry_time"]
        exit_time = trade["exit_time"]
        entry_price = trade["entry_price"]
        direction = trade["direction"]
        recorded_mfe = trade["mfe_pips"]

        # M1キャンドルを抽出
        mask = (m1_times >= np.datetime64(entry_time)) & (
            m1_times <= np.datetime64(exit_time)
        )
        candle_idx = np.where(mask)[0]

        if len(candle_idx) == 0:
            results.append(
                {
                    "trade_id": trade["trade_id"],
                    "symbol": symbol,
                    "direction": direction,
                    "entry_time": entry_time,
                    "exit_time": exit_time,
                    "holding_minutes": trade["holding_minutes"],
                    "recorded_mfe_pips": recorded_mfe,
                    "computed_mfe_pips": 0.0,
                    "time_to_mfe_minutes": 0.0,
                    "pips": trade["pips"],
                    "sl_pips": trade["sl_pips"],
                    "regime": trade["regime"],
                    "exit_reason": trade["exit_reason"],
                    "is_win": trade["is_win"],
                    # MFE到達時の含み益カーブ（5分刻み）
                    "mfe_curve_5m": [],
                }
            )
            continue

        # MFE計算
        best_fav = 0.0
        best_fav_time = entry_time
        fav_curve_5m: list[float] = []
        running_max = 0.0

        for idx in candle_idx:
            if direction == "BUY":
                fav = (m1_highs[idx] - entry_price) / pip_unit
            else:
                fav = (entry_price - m1_lows[idx]) / pip_unit

            if fav > best_fav:
                best_fav = fav
                best_fav_time = pd.Timestamp(m1_times[idx])

            # 5分刻みのカーブ
            minutes_elapsed = (
                pd.Timestamp(m1_times[idx]) - entry_time
            ).total_seconds() / 60.0
            running_max = max(running_max, fav)
            if len(fav_curve_5m) == 0 or minutes_elapsed >= len(fav_curve_5m) * 5:
                fav_curve_5m.append(round(running_max, 2))

        time_to_mfe = (best_fav_time - entry_time).total_seconds() / 60.0

        results.append(
            {
                "trade_id": trade["trade_id"],
                "symbol": symbol,
                "direction": direction,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "holding_minutes": trade["holding_minutes"],
                "recorded_mfe_pips": recorded_mfe,
                "computed_mfe_pips": round(best_fav, 2),
                "time_to_mfe_minutes": round(time_to_mfe, 1),
                "pips": trade["pips"],
                "sl_pips": trade["sl_pips"],
                "regime": trade["regime"],
                "exit_reason": trade["exit_reason"],
                "is_win": trade["is_win"],
                "mfe_curve_5m": fav_curve_5m[:100],  # 最大500分
            }
        )

    return results


def print_mfe_distribution(df: pd.DataFrame) -> None:
    """MFE到達時間の分布統計を出力."""
    print("\n" + "=" * 70)
    print("1. MFE到達時間の分布統計")
    print("=" * 70)

    ttm = df["time_to_mfe_minutes"]

    print(f"\n全トレード (n={len(df)}):")
    print(f"  中央値: {ttm.median():.0f} 分")
    print(f"  平均値: {ttm.mean():.0f} 分")
    print(f"  25%ile: {ttm.quantile(0.25):.0f} 分")
    print(f"  75%ile: {ttm.quantile(0.75):.0f} 分")
    print(f"  90%ile: {ttm.quantile(0.90):.0f} 分")

    for label, mask in [
        ("勝ちトレード", df["is_win"]),
        ("負けトレード", ~df["is_win"]),
    ]:
        sub = df[mask]
        if len(sub) == 0:
            continue
        t = sub["time_to_mfe_minutes"]
        print(f"\n{label} (n={len(sub)}):")
        print(f"  中央値: {t.median():.0f} 分")
        print(f"  平均値: {t.mean():.0f} 分")
        print(f"  25%ile: {t.quantile(0.25):.0f} 分")
        print(f"  75%ile: {t.quantile(0.75):.0f} 分")

    # ペア別
    print("\nペア別 MFE到達時間（中央値）:")
    for sym in sorted(df["symbol"].unique()):
        sub = df[df["symbol"] == sym]
        print(f"  {sym}: {sub['time_to_mfe_minutes'].median():.0f} 分 (n={len(sub)})")

    # レジーム別
    print("\nレジーム別 MFE到達時間（中央値）:")
    for reg in sorted(df["regime"].unique()):
        sub = df[df["regime"] == reg]
        print(f"  {reg}: {sub['time_to_mfe_minutes'].median():.0f} 分 (n={len(sub)})")


def print_mfe_by_time_bucket(df: pd.DataFrame) -> None:
    """時間帯別の到達MFE分析."""
    print("\n" + "=" * 70)
    print("2. 時間帯別の到達MFE（エントリー後X分以内の最大含み益）")
    print("=" * 70)

    buckets = [
        ("0-15分", 0, 15),
        ("15-30分", 15, 30),
        ("30-60分", 30, 60),
        ("1-2時間", 60, 120),
        ("2-4時間", 120, 240),
        ("4-8時間", 240, 480),
        ("8時間+", 480, 99999),
    ]

    print(f"\n{'時間帯':<12} {'MFE到達数':>10} {'割合':>8} {'累積':>8}")
    print("-" * 42)

    cumulative = 0
    total = len(df)
    for label, lo, hi in buckets:
        count = len(
            df[(df["time_to_mfe_minutes"] >= lo) & (df["time_to_mfe_minutes"] < hi)]
        )
        cumulative += count
        pct = count / total * 100 if total > 0 else 0
        cum_pct = cumulative / total * 100 if total > 0 else 0
        print(f"{label:<12} {count:>10} {pct:>7.1f}% {cum_pct:>7.1f}%")

    # 累積MFEカーブ（エントリー後X分時点での平均到達MFE）
    print("\nエントリー後の平均累積MFE（pips）:")
    checkpoints = [5, 10, 15, 30, 60, 90, 120, 180, 240, 360, 480]
    print(f"  {'経過時間':<10} {'平均MFE':>10} {'中央値MFE':>10}")
    print("  " + "-" * 32)

    for mins in checkpoints:
        # MFEカーブから該当時点のMFEを取得
        mfe_at_time = []
        for _, row in df.iterrows():
            curve = row.get("mfe_curve_5m", [])
            idx = mins // 5
            if curve and idx < len(curve):
                mfe_at_time.append(curve[idx])
            elif curve:
                mfe_at_time.append(curve[-1])
        if mfe_at_time:
            arr = np.array(mfe_at_time)
            h_label = f"{mins}分" if mins < 60 else f"{mins // 60}h{mins % 60:02d}m"
            print(f"  {h_label:<10} {arr.mean():>10.1f} {np.median(arr):>10.1f}")


def print_virtual_tp_simulation(df: pd.DataFrame) -> None:
    """仮想TP固定シミュレーション."""
    print("\n" + "=" * 70)
    print("3. 仮想TP固定シミュレーション（TPに到達すれば勝ち、しなければSLで負け）")
    print("=" * 70)

    tp_values = [5, 10, 15, 20, 25, 30, 40]

    print(
        f"\n{'TP(pips)':<10} {'勝率':>8} {'到達数':>8} {'平均到達時間':>12} "
        f"{'期待値(pips)':>12} {'月平均トレード':>14}"
    )
    print("-" * 70)

    total_months = 36  # 2020-2022

    for tp in tp_values:
        # MFE >= TP なら勝ち（TP到達）
        wins = df[df["computed_mfe_pips"] >= tp]
        losses = df[df["computed_mfe_pips"] < tp]
        win_rate = len(wins) / len(df) if len(df) > 0 else 0

        # 勝ちトレードの平均到達時間（MFE到達時間ではなくTP到達時間を近似）
        # TP到達時間 ≈ time_to_mfe_minutes × (tp / computed_mfe_pips) として近似
        avg_time = 0.0
        if len(wins) > 0:
            # TPに早く到達するケースを想定して、MFE到達時間の中央値を使う
            avg_time = wins["time_to_mfe_minutes"].median()

        # 期待値: 勝率×TP - (1-勝率)×平均SL - スプレッド
        avg_sl = df["sl_pips"].mean()
        avg_spread = 1.5  # 概算
        ev = win_rate * tp - (1 - win_rate) * avg_sl - avg_spread

        trades_per_month = len(df) / total_months

        print(
            f"TP={tp:<5} {win_rate:>7.1%} {len(wins):>8} "
            f"{avg_time:>10.0f}分 {ev:>12.1f} {trades_per_month:>14.1f}"
        )


def print_runner_contribution(df: pd.DataFrame) -> None:
    """Runner運用（1R超え）の貢献度分析."""
    print("\n" + "=" * 70)
    print("4. Runner運用の貢献度（1R=TP超えのトレード分析）")
    print("=" * 70)

    # 各トレードの1R = sl_pips × 2（TP/SL比 2.0）
    df = df.copy()
    df["tp_pips"] = df["sl_pips"] * 2  # 現行のTP/SL=2.0
    df["mfe_r"] = df["computed_mfe_pips"] / df["sl_pips"]

    # 1R以上到達したトレード
    over_1r = df[df["mfe_r"] >= 1.0]
    over_2r = df[df["mfe_r"] >= 2.0]
    over_3r = df[df["mfe_r"] >= 3.0]

    print(f"\nMFE到達 R値分布:")
    print(f"  >= 0.5R: {len(df[df['mfe_r'] >= 0.5]):>5} ({len(df[df['mfe_r'] >= 0.5]) / len(df) * 100:.1f}%)")
    print(f"  >= 1.0R: {len(over_1r):>5} ({len(over_1r) / len(df) * 100:.1f}%)")
    print(f"  >= 1.5R: {len(df[df['mfe_r'] >= 1.5]):>5} ({len(df[df['mfe_r'] >= 1.5]) / len(df) * 100:.1f}%)")
    print(f"  >= 2.0R: {len(over_2r):>5} ({len(over_2r) / len(df) * 100:.1f}%)")
    print(f"  >= 3.0R: {len(over_3r):>5} ({len(over_3r) / len(df) * 100:.1f}%)")

    # Runner運用のシナリオ比較
    print("\nシナリオ比較（1トレードあたり平均pips）:")

    # シナリオA: TP固定（1R = tp_pips で全決済）
    scenario_a_pips = []
    for _, t in df.iterrows():
        if t["computed_mfe_pips"] >= t["tp_pips"]:
            scenario_a_pips.append(t["tp_pips"])
        else:
            scenario_a_pips.append(t["pips"])  # SL or 他のexitで終了
    avg_a = np.mean(scenario_a_pips)

    # シナリオB: 現行（実際のpips）
    avg_b = df["pips"].mean()

    print(f"  A) TP固定決済（1R=TP_pipsで全決済）: {avg_a:.2f} pips/trade")
    print(f"  B) 現行（実際の結果）:                 {avg_b:.2f} pips/trade")
    print(f"  差分 (B-A):                             {avg_b - avg_a:+.2f} pips/trade")

    # 1R超えトレードでRunner運用による追加利益
    if len(over_1r) > 0:
        extra_pips = over_1r["pips"] - over_1r["tp_pips"]
        print(f"\n1R超えトレード (n={len(over_1r)}) のRunner追加利益:")
        print(f"  平均: {extra_pips.mean():+.1f} pips")
        print(f"  中央値: {extra_pips.median():+.1f} pips")
        print(f"  最大: {extra_pips.max():+.1f} pips")
        print(f"  合計: {extra_pips.sum():+.0f} pips")


def main() -> None:
    """メインエントリポイント."""
    print("=" * 70)
    print("MFE到達時間分析 — D1シグナルの「賞味期限」測定")
    print("=" * 70)

    # Step 1: トレードデータのロード
    trades = load_all_trades()

    # Step 2: ペア別にM1データロード＋MFE計算
    all_results: list[dict] = []
    symbols = sorted(trades["symbol"].unique())

    for symbol in symbols:
        sym_trades = trades[trades["symbol"] == symbol]
        print(f"\n{symbol}: {len(sym_trades)} trades を処理中...")

        start = sym_trades["entry_time"].min()
        end = sym_trades["exit_time"].max()

        m1 = load_m1_data(symbol, start, end)
        if m1.empty:
            continue

        print(f"  M1データ: {len(m1)} bars ({m1['date'].min()} ~ {m1['date'].max()})")
        results = compute_mfe_timing(trades, m1, symbol)
        all_results.extend(results)
        print(f"  完了: {len(results)} trades 処理済み")

        # メモリ解放
        del m1

    # Step 3: 分析
    result_df = pd.DataFrame(all_results)

    # スポットチェック: computed_mfe vs recorded_mfe
    print("\n--- スポットチェック: MFE一致確認 ---")
    diff = abs(result_df["computed_mfe_pips"] - result_df["recorded_mfe_pips"])
    print(f"  MFE差分 平均: {diff.mean():.2f} pips")
    print(f"  MFE差分 最大: {diff.max():.2f} pips")
    print(f"  差分 < 1pip: {(diff < 1.0).mean():.1%}")

    # 分析出力
    print_mfe_distribution(result_df)
    print_mfe_by_time_bucket(result_df)
    print_virtual_tp_simulation(result_df)
    print_runner_contribution(result_df)

    print("\n" + "=" * 70)
    print("分析完了")
    print("=" * 70)


if __name__ == "__main__":
    main()
