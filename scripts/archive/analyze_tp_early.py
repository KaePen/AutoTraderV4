"""TP_EARLY比較分析スクリプト."""
from __future__ import annotations

import sys
import pandas as pd
import numpy as np

NEW_CSV = "logs/backtest_log/trades_20260215_183531.csv"
LEGACY_CSV = "logs/backtest_log/trades_20260215_183955.csv"


def load(path: str) -> pd.DataFrame:
    """CSVを読み込む."""
    df = pd.read_csv(path)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    return df


def overall_summary(df: pd.DataFrame, label: str) -> dict:
    """全体サマリー."""
    wins = df[df["profit_loss"] > 0]
    losses = df[df["profit_loss"] < 0]
    gross_profit = wins["profit_loss"].sum()
    gross_loss = abs(losses["profit_loss"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "label": label,
        "trades": len(df),
        "win_rate": len(wins) / len(df) * 100,
        "pf": pf,
        "profit": df["profit_loss"].sum(),
        "avg_pips": df["pips"].mean(),
        "max_dd": df["dd_pct_at_entry"].max(),
    }


def tp_early_analysis(df: pd.DataFrame, label: str) -> None:
    """TP_EARLY詳細分析."""
    te = df[df["exit_reason"] == "TP_EARLY"]
    non_te = df[df["exit_reason"] != "TP_EARLY"]

    print(f"\n{'='*60}")
    print(f" TP_EARLY分析: {label}")
    print(f"{'='*60}")
    print(f"  全取引: {len(df)}")
    print(f"  TP_EARLY: {len(te)} ({len(te)/len(df)*100:.1f}%)")
    print(f"  非TP_EARLY: {len(non_te)}")

    if len(te) == 0:
        print("  TP_EARLY取引なし")
        return

    # TP_EARLYの損益
    te_wins = te[te["profit_loss"] > 0]
    te_losses = te[te["profit_loss"] <= 0]
    print(f"\n  TP_EARLY勝率: {len(te_wins)}/{len(te)}"
          f" ({len(te_wins)/len(te)*100:.1f}%)")
    print(f"  TP_EARLY平均pips: {te['pips'].mean():.2f}")
    print(f"  TP_EARLY合計損益: ¥{te['profit_loss'].sum():,.0f}")

    # pips分布
    print(f"\n  pips分布:")
    print(f"    min: {te['pips'].min():.2f}")
    print(f"    25%: {te['pips'].quantile(0.25):.2f}")
    print(f"    50%: {te['pips'].quantile(0.5):.2f}")
    print(f"    75%: {te['pips'].quantile(0.75):.2f}")
    print(f"    max: {te['pips'].max():.2f}")

    # mfe_r分布
    if "mfe_r" in te.columns:
        print(f"\n  mfe_r分布:")
        print(f"    min: {te['mfe_r'].min():.3f}")
        print(f"    25%: {te['mfe_r'].quantile(0.25):.3f}")
        print(f"    50%: {te['mfe_r'].quantile(0.5):.3f}")
        print(f"    75%: {te['mfe_r'].quantile(0.75):.3f}")
        print(f"    max: {te['mfe_r'].max():.3f}")

    # holding_minutes分布
    print(f"\n  holding_minutes分布:")
    print(f"    min: {te['holding_minutes'].min():.0f}")
    print(f"    25%: {te['holding_minutes'].quantile(0.25):.0f}")
    print(f"    50%: {te['holding_minutes'].quantile(0.5):.0f}")
    print(f"    75%: {te['holding_minutes'].quantile(0.75):.0f}")
    print(f"    max: {te['holding_minutes'].max():.0f}")

    # mode × regime クロス集計
    print(f"\n  mode × regime クロス集計:")
    cross = te.groupby(["mode", "regime"]).agg(
        count=("profit_loss", "size"),
        avg_pips=("pips", "mean"),
        total_pl=("profit_loss", "sum"),
        win_rate=("profit_loss", lambda x: (x > 0).mean() * 100),
    ).reset_index()
    for _, row in cross.iterrows():
        print(f"    {row['mode']:15s} × {row['regime']:10s}: "
              f"{int(row['count']):4d}件, "
              f"勝率{row['win_rate']:.1f}%, "
              f"平均{row['avg_pips']:.2f}pips, "
              f"合計¥{row['total_pl']:,.0f}")

    # session別
    if "session" in te.columns:
        print(f"\n  session別:")
        sess = te.groupby("session").agg(
            count=("profit_loss", "size"),
            avg_pips=("pips", "mean"),
            total_pl=("profit_loss", "sum"),
            win_rate=("profit_loss", lambda x: (x > 0).mean() * 100),
        ).reset_index()
        for _, row in sess.iterrows():
            print(f"    {row['session']:15s}: "
                  f"{int(row['count']):4d}件, "
                  f"勝率{row['win_rate']:.1f}%, "
                  f"平均{row['avg_pips']:.2f}pips, "
                  f"合計¥{row['total_pl']:,.0f}")


def exit_reason_comparison(
    df_new: pd.DataFrame, df_legacy: pd.DataFrame
) -> None:
    """exit_reason別比較."""
    print(f"\n{'='*60}")
    print(f" exit_reason別比較")
    print(f"{'='*60}")

    reasons_new = df_new["exit_reason"].value_counts()
    reasons_leg = df_legacy["exit_reason"].value_counts()
    all_reasons = sorted(
        set(reasons_new.index) | set(reasons_leg.index)
    )

    print(f"  {'reason':20s} {'新設定':>8s} {'レガシー':>8s} {'差分':>8s}")
    print(f"  {'-'*50}")
    for r in all_reasons:
        n = reasons_new.get(r, 0)
        l = reasons_leg.get(r, 0)
        print(f"  {r:20s} {n:8d} {l:8d} {n-l:+8d}")


def main() -> None:
    """メイン."""
    df_new = load(NEW_CSV)
    df_legacy = load(LEGACY_CSV)

    # 全体比較
    s_new = overall_summary(df_new, "新設定")
    s_leg = overall_summary(df_legacy, "レガシー")

    print("=" * 60)
    print(" 全体比較: 新設定 vs レガシー")
    print("=" * 60)
    print(f"  {'指標':15s} {'新設定':>12s} {'レガシー':>12s} {'差分':>12s}")
    print(f"  {'-'*55}")
    print(f"  {'取引数':15s} {s_new['trades']:12d}"
          f" {s_leg['trades']:12d}"
          f" {s_new['trades']-s_leg['trades']:+12d}")
    print(f"  {'勝率':15s} {s_new['win_rate']:11.1f}%"
          f" {s_leg['win_rate']:11.1f}%"
          f" {s_new['win_rate']-s_leg['win_rate']:+11.1f}%")
    print(f"  {'PF':15s} {s_new['pf']:12.2f}"
          f" {s_leg['pf']:12.2f}"
          f" {s_new['pf']-s_leg['pf']:+12.2f}")
    print(f"  {'純利益':15s} ¥{s_new['profit']:>10,.0f}"
          f" ¥{s_leg['profit']:>10,.0f}"
          f" ¥{s_new['profit']-s_leg['profit']:>+10,.0f}")
    print(f"  {'平均pips':15s} {s_new['avg_pips']:12.2f}"
          f" {s_leg['avg_pips']:12.2f}"
          f" {s_new['avg_pips']-s_leg['avg_pips']:+12.2f}")

    # exit_reason比較
    exit_reason_comparison(df_new, df_legacy)

    # TP_EARLY分析
    tp_early_analysis(df_new, "新設定")
    tp_early_analysis(df_legacy, "レガシー")


if __name__ == "__main__":
    main()
