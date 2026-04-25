"""同方向再エントリー分析スクリプト

バックテスト結果のtrades.csvから再エントリーパターンを統計分析する。
課題1: DECAY/SL後の同方向再エントリー勝率・損益
課題2: 再エントリーまでの時間と結果の相関
課題3: 同方向連敗数と損益の関係
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", "{:.2f}".format)


def load_trades(csv_path: str) -> pd.DataFrame:
    """trades.csvを読み込み、型変換"""
    df = pd.read_csv(csv_path)
    # 型変換
    for col in ["pips", "profit_loss", "prev_same_dir_pnl_pips",
                "minutes_since_prev_same_dir_close", "mfe_pips", "mae_pips",
                "consensus_score", "lot"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["is_reentry", "same_dir_consecutive_losses", "consecutive_losses"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["win"] = df["pips"] > 0
    df["pnl_yen"] = pd.to_numeric(df["profit_loss"], errors="coerce").fillna(0)
    return df


def analyze_reentry_by_prev_exit(df: pd.DataFrame) -> None:
    """課題1: 直前Exit理由別の再エントリー成績"""
    print("\n" + "=" * 80)
    print("【課題1】直前Exit理由別の再エントリー成績")
    print("=" * 80)

    reentries = df[df["is_reentry"] == 1].copy()
    fresh = df[df["is_reentry"] == 0].copy()

    print(f"\n全トレード: {len(df)}")
    print(f"初回エントリー: {len(fresh)} ({len(fresh)/len(df)*100:.1f}%)")
    print(f"再エントリー: {len(reentries)} ({len(reentries)/len(df)*100:.1f}%)")

    if fresh.empty or reentries.empty:
        print("データ不足")
        return

    print(f"\n--- 初回 vs 再エントリー ---")
    for label, subset in [("初回", fresh), ("再エントリー", reentries)]:
        wr = subset["win"].mean() * 100
        avg_pips = subset["pips"].mean()
        total_pnl = subset["pnl_yen"].sum()
        avg_mfe = subset["mfe_pips"].mean() if "mfe_pips" in subset else 0
        avg_mae = subset["mae_pips"].mean() if "mae_pips" in subset else 0
        print(f"  {label}: {len(subset)}件, WR {wr:.1f}%, "
              f"平均{avg_pips:+.1f}p, 合計{total_pnl:+,.0f}円, "
              f"MFE {avg_mfe:.1f}p, MAE {avg_mae:.1f}p")

    if "prev_same_dir_exit_reason" not in reentries.columns:
        return

    print(f"\n--- 直前Exit理由別の再エントリー成績 ---")
    for reason, group in reentries.groupby("prev_same_dir_exit_reason"):
        if len(group) < 3:
            continue
        wr = group["win"].mean() * 100
        avg_pips = group["pips"].mean()
        total_pnl = group["pnl_yen"].sum()
        prev_loss = (group["prev_same_dir_pnl_pips"] < 0).mean() * 100
        print(f"  前回{reason}: {len(group)}件, WR {wr:.1f}%, "
              f"平均{avg_pips:+.1f}p, 合計{total_pnl:+,.0f}円 "
              f"(前回負け率 {prev_loss:.0f}%)")

    # 前回が負けだった再エントリー vs 前回が勝ちだった再エントリー
    print(f"\n--- 前回損益別の再エントリー成績 ---")
    prev_loss = reentries[reentries["prev_same_dir_pnl_pips"] < 0]
    prev_win = reentries[reentries["prev_same_dir_pnl_pips"] > 0]
    for label, subset in [("前回負け後", prev_loss), ("前回勝ち後", prev_win)]:
        if subset.empty:
            continue
        wr = subset["win"].mean() * 100
        avg_pips = subset["pips"].mean()
        total_pnl = subset["pnl_yen"].sum()
        print(f"  {label}: {len(subset)}件, WR {wr:.1f}%, "
              f"平均{avg_pips:+.1f}p, 合計{total_pnl:+,.0f}円")


def analyze_reentry_timing(df: pd.DataFrame) -> None:
    """課題2: 再エントリーまでの時間と結果の相関"""
    print("\n" + "=" * 80)
    print("【課題2】再エントリーまでの時間と結果の相関")
    print("=" * 80)

    reentries = df[(df["is_reentry"] == 1) &
                   (df["minutes_since_prev_same_dir_close"] >= 0)].copy()
    if reentries.empty:
        print("再エントリーデータなし")
        return

    # 時間帯別にビン分け
    bins = [0, 15, 30, 60, 120, 240, 480, float("inf")]
    labels = ["0-15m", "15-30m", "30-60m", "1-2h", "2-4h", "4-8h", "8h+"]
    reentries["time_bin"] = pd.cut(
        reentries["minutes_since_prev_same_dir_close"],
        bins=bins, labels=labels, right=False,
    )

    print(f"\n--- 再エントリー間隔別の成績 ---")
    for bin_label in labels:
        group = reentries[reentries["time_bin"] == bin_label]
        if len(group) < 3:
            continue
        wr = group["win"].mean() * 100
        avg_pips = group["pips"].mean()
        total_pnl = group["pnl_yen"].sum()
        print(f"  {bin_label:>7}: {len(group):>4}件, WR {wr:.1f}%, "
              f"平均{avg_pips:+.1f}p, 合計{total_pnl:+,.0f}円")

    # DECAY後の再エントリー間隔別
    decay_reentries = reentries[reentries["prev_same_dir_exit_reason"] == "EDGE_DECAY"]
    if not decay_reentries.empty:
        print(f"\n--- DECAY後の再エントリー間隔別 ---")
        for bin_label in labels:
            group = decay_reentries[decay_reentries["time_bin"] == bin_label]
            if len(group) < 3:
                continue
            wr = group["win"].mean() * 100
            avg_pips = group["pips"].mean()
            print(f"  {bin_label:>7}: {len(group):>4}件, WR {wr:.1f}%, "
                  f"平均{avg_pips:+.1f}p")

    # SL後の再エントリー間隔別
    sl_reentries = reentries[reentries["prev_same_dir_exit_reason"] == "SL_HIT"]
    if not sl_reentries.empty:
        print(f"\n--- SL後の再エントリー間隔別 ---")
        for bin_label in labels:
            group = sl_reentries[sl_reentries["time_bin"] == bin_label]
            if len(group) < 3:
                continue
            wr = group["win"].mean() * 100
            avg_pips = group["pips"].mean()
            print(f"  {bin_label:>7}: {len(group):>4}件, WR {wr:.1f}%, "
                  f"平均{avg_pips:+.1f}p")


def analyze_consecutive_losses(df: pd.DataFrame) -> None:
    """課題3: 同方向連敗数と損益の関係"""
    print("\n" + "=" * 80)
    print("【課題3】同方向連敗数と損益の関係")
    print("=" * 80)

    if "same_dir_consecutive_losses" not in df.columns:
        print("フィールドなし")
        return

    print(f"\n--- 同方向連敗数別の成績 ---")
    for n_losses in sorted(df["same_dir_consecutive_losses"].unique()):
        group = df[df["same_dir_consecutive_losses"] == n_losses]
        if len(group) < 3:
            continue
        wr = group["win"].mean() * 100
        avg_pips = group["pips"].mean()
        total_pnl = group["pnl_yen"].sum()
        print(f"  連敗{int(n_losses):>2}後: {len(group):>4}件, WR {wr:.1f}%, "
              f"平均{avg_pips:+.1f}p, 合計{total_pnl:+,.0f}円")

    # 連敗中のexit reason分布
    consec = df[df["same_dir_consecutive_losses"] >= 2].copy()
    if not consec.empty:
        print(f"\n--- 同方向2連敗以上時のexit reason分布 ---")
        exit_dist = consec["exit_reason"].value_counts()
        for reason, count in exit_dist.items():
            pct = count / len(consec) * 100
            subset = consec[consec["exit_reason"] == reason]
            avg_pips = subset["pips"].mean()
            print(f"  {reason}: {count}件 ({pct:.0f}%), 平均{avg_pips:+.1f}p")


def analyze_session_reentry(df: pd.DataFrame) -> None:
    """セッション別の再エントリー成績"""
    print("\n" + "=" * 80)
    print("【補足】セッション別の再エントリー成績")
    print("=" * 80)

    reentries = df[df["is_reentry"] == 1].copy()
    if reentries.empty or "session" not in reentries.columns:
        print("データなし")
        return

    print(f"\n--- セッション別 ---")
    for session, group in reentries.groupby("session"):
        if len(group) < 3:
            continue
        wr = group["win"].mean() * 100
        avg_pips = group["pips"].mean()
        total_pnl = group["pnl_yen"].sum()
        print(f"  {session:>8}: {len(group):>4}件, WR {wr:.1f}%, "
              f"平均{avg_pips:+.1f}p, 合計{total_pnl:+,.0f}円")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python analyze_reentry.py <trades.csv>")
        sys.exit(1)

    csv_path = sys.argv[1]
    if not Path(csv_path).exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    df = load_trades(csv_path)

    # 新フィールドの存在チェック
    required = ["is_reentry", "prev_same_dir_exit_reason",
                "same_dir_consecutive_losses", "minutes_since_prev_same_dir_close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"Missing columns: {missing}")
        print(f"Available: {list(df.columns)}")
        sys.exit(1)

    print(f"Loaded {len(df)} trades from {csv_path}")

    analyze_reentry_by_prev_exit(df)
    analyze_reentry_timing(df)
    analyze_consecutive_losses(df)
    analyze_session_reentry(df)

    print("\n" + "=" * 80)
    print("分析完了")


if __name__ == "__main__":
    main()
