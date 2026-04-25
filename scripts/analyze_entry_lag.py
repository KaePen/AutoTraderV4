"""エントリー後のラグ分析 — スコア方向への価格移動タイミングを検証.

仮説: エントリー後30-50分程度で市場がスコア方向に動き始める
検証: M1データからエントリー後の含み損益カーブを時間軸で集計し、
      方向性が出るタイミングを特定する。
"""

from __future__ import annotations

import json
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

# 分析する時間ポイント（分）
TIME_POINTS = [1, 2, 3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60,
               75, 90, 120, 150, 180, 240, 300, 360, 480]


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
        m1_path, sep="\t",
        names=["date", "time", "open", "high", "low", "close", "tickvol", "vol", "spread"],
        skiprows=1,
    )
    df["date"] = pd.to_datetime(df["date"] + " " + df["time"], format="%Y.%m.%d %H:%M:%S")
    df.drop(columns=["time"], inplace=True)
    mask = (df["date"] >= start - timedelta(days=1)) & (df["date"] <= end + timedelta(days=1))
    df = df.loc[mask].reset_index(drop=True)
    return df


def compute_price_curves(
    trades: pd.DataFrame, m1: pd.DataFrame, symbol: str
) -> list[dict]:
    """各トレードのエントリー後の含み損益カーブ（1分刻み）を計算.

    ポジション決済後もM1データが続く限り追跡し、
    「もしホールドし続けていたらどうなったか」を確認する。
    """
    pip_unit = PIP_UNITS.get(symbol, 0.01)
    results: list[dict] = []

    m1_dates = m1["date"].values
    m1_opens = m1["open"].values
    m1_highs = m1["high"].values
    m1_lows = m1["low"].values
    m1_closes = m1["close"].values

    sym_trades = trades[trades["symbol"] == symbol]

    for _, trade in sym_trades.iterrows():
        entry_time = trade["entry_time"]
        entry_price = trade["entry_price"]
        direction = trade["direction"]

        # エントリー後最大480分（8時間）まで追跡
        max_end = entry_time + timedelta(minutes=max(TIME_POINTS) + 5)

        mask = (m1_dates >= np.datetime64(entry_time)) & (m1_dates <= np.datetime64(max_end))
        candle_idx = np.where(mask)[0]

        if len(candle_idx) == 0:
            continue

        # 各TIME_POINTでの含み損益（close価格ベース）と最大含み益を計算
        pnl_at: dict[int, float] = {}
        mfe_at: dict[int, float] = {}
        running_mfe = 0.0

        prev_tp_idx = 0
        for tp_minutes in TIME_POINTS:
            target_time = entry_time + timedelta(minutes=tp_minutes)
            target_np = np.datetime64(target_time)

            # 該当時刻の直前のキャンドルを探す
            matching = [i for i in candle_idx if m1_dates[i] <= target_np]
            if not matching:
                continue

            idx = matching[-1]
            close = m1_closes[idx]

            # close価格ベースの含み損益
            if direction == "BUY":
                pnl = (close - entry_price) / pip_unit
            else:
                pnl = (entry_price - close) / pip_unit

            # running MFE（エントリーからtp_minutesまでの区間のhigh/lowで計算）
            for ci in candle_idx:
                if ci > idx:
                    break
                if ci < candle_idx[prev_tp_idx] if prev_tp_idx < len(candle_idx) else 0:
                    continue
                if direction == "BUY":
                    fav = (m1_highs[ci] - entry_price) / pip_unit
                else:
                    fav = (entry_price - m1_lows[ci]) / pip_unit
                running_mfe = max(running_mfe, fav)

            pnl_at[tp_minutes] = round(pnl, 2)
            mfe_at[tp_minutes] = round(running_mfe, 2)

        if not pnl_at:
            continue

        results.append({
            "trade_id": trade["trade_id"],
            "symbol": symbol,
            "direction": direction,
            "entry_time": entry_time,
            "exit_time": trade["exit_time"],
            "entry_price": entry_price,
            "consensus_score": trade["consensus_score"],
            "holding_minutes": trade["holding_minutes"],
            "pips": trade["pips"],
            "mfe_pips": trade["mfe_pips"],
            "exit_reason": trade["exit_reason"],
            "is_win": trade["is_win"],
            "pnl_at": pnl_at,
            "mfe_at": mfe_at,
        })

    return results


def print_avg_pnl_curve(results: list[dict]) -> None:
    """エントリー後の平均含み損益カーブ."""
    print("\n" + "=" * 80)
    print("1. エントリー後の平均含み損益カーブ（全トレード）")
    print("   → ラグ仮説の核心: 何分後にスコア方向へ動くか")
    print("=" * 80)

    n_total = len(results)
    print(f"\n{'経過時間':>10} {'平均PnL':>10} {'中央値PnL':>10} {'正方向%':>10} "
          f"{'平均MFE':>10} {'n':>6}")
    print("-" * 62)

    for tp in TIME_POINTS:
        pnls = [r["pnl_at"][tp] for r in results if tp in r["pnl_at"]]
        mfes = [r["mfe_at"][tp] for r in results if tp in r["mfe_at"]]
        if not pnls:
            continue
        arr = np.array(pnls)
        mfe_arr = np.array(mfes)
        positive_pct = (arr > 0).mean() * 100

        label = f"{tp}分" if tp < 60 else f"{tp // 60}h{tp % 60:02d}m"
        print(f"{label:>10} {arr.mean():>+10.2f} {np.median(arr):>+10.2f} "
              f"{positive_pct:>9.1f}% {mfe_arr.mean():>10.2f} {len(pnls):>6}")


def print_pnl_curve_by_outcome(results: list[dict]) -> None:
    """勝ちトレード/負けトレード別の含み損益カーブ."""
    print("\n" + "=" * 80)
    print("2. 勝ち/負けトレード別 — エントリー後の含み損益カーブ")
    print("   → 勝つトレードと負けるトレードで初動に違いがあるか")
    print("=" * 80)

    for label, filter_fn in [
        ("勝ちトレード", lambda r: r["is_win"]),
        ("負けトレード", lambda r: not r["is_win"]),
    ]:
        subset = [r for r in results if filter_fn(r)]
        print(f"\n--- {label} (n={len(subset)}) ---")
        print(f"{'経過時間':>10} {'平均PnL':>10} {'中央値PnL':>10} {'正方向%':>10}")
        print("-" * 46)

        for tp in TIME_POINTS:
            pnls = [r["pnl_at"][tp] for r in subset if tp in r["pnl_at"]]
            if not pnls:
                continue
            arr = np.array(pnls)
            positive_pct = (arr > 0).mean() * 100
            label_t = f"{tp}分" if tp < 60 else f"{tp // 60}h{tp % 60:02d}m"
            print(f"{label_t:>10} {arr.mean():>+10.2f} {np.median(arr):>+10.2f} "
                  f"{positive_pct:>9.1f}%")


def print_delayed_entry_simulation(results: list[dict]) -> None:
    """遅延エントリーシミュレーション.

    「もしエントリーをX分遅らせていたら、
     同じ方向にエントリーした場合の結果はどうなるか」を概算。
    """
    print("\n" + "=" * 80)
    print("3. 遅延エントリーシミュレーション")
    print("   → X分遅らせてエントリーしていたら、含み益はどう変わるか")
    print("=" * 80)

    delay_minutes = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60]

    print(f"\n{'遅延':>8} {'改善pips':>12} {'改善率':>10} {'勝率変化':>12}")
    print("-" * 46)

    # 基準: 遅延なし（実際のpips）
    base_pnls = [r["pips"] for r in results]
    base_mean = np.mean(base_pnls)
    base_wins = np.mean([r["is_win"] for r in results])

    for delay in delay_minutes:
        # 遅延エントリーの場合: エントリー時のPnLをdelay分のPnLで差し引く
        # = (最終PnL) - (delay分時点のPnL) ≈ delay分遅れでエントリーした場合の利益
        delayed_pnls = []
        for r in results:
            if delay in r["pnl_at"]:
                # 遅延エントリーの損益 = 実際の最終損益 - delay時点の含み損益
                # （= delay時点のclose価格でエントリーした場合の概算）
                delayed_pnl = r["pips"] - r["pnl_at"][delay]
                delayed_pnls.append(delayed_pnl)

        if not delayed_pnls:
            continue

        arr = np.array(delayed_pnls)
        improvement = arr.mean() - base_mean
        win_rate = (arr > 0).mean()
        win_change = win_rate - base_wins

        label = f"{delay}分"
        print(f"{label:>8} {improvement:>+12.2f} {improvement / abs(base_mean) * 100 if base_mean != 0 else 0:>+9.1f}% "
              f"{win_change:>+11.1%}")


def print_initial_drawdown_analysis(results: list[dict]) -> None:
    """初動の逆行（MAE）が発生してからの回復パターン分析."""
    print("\n" + "=" * 80)
    print("4. 初動逆行 → 回復パターン分析")
    print("   → エントリー直後に逆行したトレードが、いつ回復するか")
    print("=" * 80)

    # エントリー後5分時点でマイナスのトレード
    initially_negative = [r for r in results if 5 in r["pnl_at"] and r["pnl_at"][5] < 0]
    initially_positive = [r for r in results if 5 in r["pnl_at"] and r["pnl_at"][5] >= 0]

    print(f"\nエントリー後5分でマイナス: {len(initially_negative)} / {len(results)} "
          f"({len(initially_negative) / len(results) * 100:.1f}%)")
    print(f"エントリー後5分でプラス:   {len(initially_positive)} / {len(results)} "
          f"({len(initially_positive) / len(results) * 100:.1f}%)")

    # 初動マイナス組の回復タイミング
    print(f"\n--- 初動マイナス組 (n={len(initially_negative)}) の回復状況 ---")
    print(f"{'経過時間':>10} {'平均PnL':>10} {'プラス転換%':>12} {'最終勝率':>10}")
    print("-" * 46)

    final_win_rate = np.mean([r["is_win"] for r in initially_negative])

    for tp in TIME_POINTS:
        pnls = [r["pnl_at"][tp] for r in initially_negative if tp in r["pnl_at"]]
        if not pnls:
            continue
        arr = np.array(pnls)
        positive_pct = (arr > 0).mean() * 100
        label = f"{tp}分" if tp < 60 else f"{tp // 60}h{tp % 60:02d}m"
        print(f"{label:>10} {arr.mean():>+10.2f} {positive_pct:>11.1f}% {final_win_rate:>9.1%}")

    # 初動プラス組
    print(f"\n--- 初動プラス組 (n={len(initially_positive)}) ---")
    print(f"{'経過時間':>10} {'平均PnL':>10} {'プラス維持%':>12} {'最終勝率':>10}")
    print("-" * 46)

    final_win_rate_pos = np.mean([r["is_win"] for r in initially_positive])

    for tp in TIME_POINTS:
        pnls = [r["pnl_at"][tp] for r in initially_positive if tp in r["pnl_at"]]
        if not pnls:
            continue
        arr = np.array(pnls)
        positive_pct = (arr > 0).mean() * 100
        label = f"{tp}分" if tp < 60 else f"{tp // 60}h{tp % 60:02d}m"
        print(f"{label:>10} {arr.mean():>+10.2f} {positive_pct:>11.1f}% {final_win_rate_pos:>9.1%}")


def print_score_strength_analysis(results: list[dict]) -> None:
    """スコア強度別のラグパターン."""
    print("\n" + "=" * 80)
    print("5. スコア強度別のラグパターン")
    print("   → 高スコアほどラグが短い/長い傾向があるか")
    print("=" * 80)

    # スコア帯で分ける
    score_bands = [
        ("低スコア (< 12)", lambda r: r["consensus_score"] < 12),
        ("中スコア (12-16)", lambda r: 12 <= r["consensus_score"] < 16),
        ("高スコア (16-20)", lambda r: 16 <= r["consensus_score"] < 20),
        ("超高スコア (>= 20)", lambda r: r["consensus_score"] >= 20),
    ]

    key_timepoints = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240]

    for band_label, filter_fn in score_bands:
        subset = [r for r in results if filter_fn(r)]
        if not subset:
            continue
        win_rate = np.mean([r["is_win"] for r in subset])
        print(f"\n--- {band_label} (n={len(subset)}, 勝率={win_rate:.1%}) ---")
        print(f"{'経過時間':>10} {'平均PnL':>10} {'正方向%':>10}")
        print("-" * 34)

        for tp in key_timepoints:
            pnls = [r["pnl_at"][tp] for r in subset if tp in r["pnl_at"]]
            if not pnls:
                continue
            arr = np.array(pnls)
            positive_pct = (arr > 0).mean() * 100
            label = f"{tp}分" if tp < 60 else f"{tp // 60}h{tp % 60:02d}m"
            print(f"{label:>10} {arr.mean():>+10.2f} {positive_pct:>9.1f}%")


def print_optimal_wait_analysis(results: list[dict]) -> None:
    """最適待機時間の特定."""
    print("\n" + "=" * 80)
    print("6. 最適待機時間分析 — 遅延エントリーの最適タイミング")
    print("   → 何分待ってからエントリーすれば最大の改善が得られるか")
    print("=" * 80)

    delays = list(range(5, 65, 5))

    # 基準PnL
    base_mean = np.mean([r["pips"] for r in results])

    best_delay = 0
    best_improvement = 0.0
    delay_improvements: list[tuple[int, float, float, int]] = []

    for delay in delays:
        delayed_pnls = []
        for r in results:
            if delay in r["pnl_at"]:
                delayed_pnl = r["pips"] - r["pnl_at"][delay]
                delayed_pnls.append(delayed_pnl)

        if not delayed_pnls:
            continue

        arr = np.array(delayed_pnls)
        improvement = arr.mean() - base_mean
        win_rate = (arr > 0).mean()
        delay_improvements.append((delay, improvement, win_rate, len(delayed_pnls)))

        if improvement > best_improvement:
            best_improvement = improvement
            best_delay = delay

    print(f"\n基準（即時エントリー）: 平均 {base_mean:+.2f} pips")
    print(f"\n{'遅延':>8} {'改善pips':>10} {'遅延後勝率':>12} {'n':>6}")
    print("-" * 40)

    for delay, imp, wr, n in delay_improvements:
        marker = " <<<" if delay == best_delay else ""
        print(f"{delay:>6}分 {imp:>+10.2f} {wr:>11.1%} {n:>6}{marker}")

    if best_delay > 0:
        print(f"\n>>> 最適遅延: {best_delay}分 (改善: {best_improvement:+.2f} pips)")
    else:
        print("\n>>> 遅延による改善なし（即時エントリーが最適）")


def main() -> None:
    """メインエントリポイント."""
    print("=" * 80)
    print("エントリーラグ分析 — 市場反応タイミングの検証")
    print("仮説: エントリー後30-50分で市場がスコア方向に動き始める")
    print("=" * 80)

    # Step 1: トレードデータのロード
    trades = load_all_trades()

    # Step 2: ペア別にM1データロード＋含み損益カーブ計算
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
        curve_results = compute_price_curves(trades, m1, symbol)
        all_results.extend(curve_results)
        print(f"  完了: {len(curve_results)} trades 処理済み")
        del m1

    print(f"\n合計: {len(all_results)} trades の価格カーブを計算完了")

    # Step 3: 分析
    print_avg_pnl_curve(all_results)
    print_pnl_curve_by_outcome(all_results)
    print_delayed_entry_simulation(all_results)
    print_initial_drawdown_analysis(all_results)
    print_score_strength_analysis(all_results)
    print_optimal_wait_analysis(all_results)

    print("\n" + "=" * 80)
    print("分析完了")
    print("=" * 80)


if __name__ == "__main__":
    main()
