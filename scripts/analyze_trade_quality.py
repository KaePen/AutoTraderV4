"""トレード品質分析スクリプト.

trades CSV を読み込み、レジーム別品質・損失パターン・
ターンポイント高掴み・短時間損切り・time_to_mfe を分析する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

CSV_PATH = (
    Path(__file__).parent.parent
    / "logs/backtest_log/USDJPY/trades_20260304_201759.csv"
)


def load_df(path: Path) -> pd.DataFrame:
    """CSVを読み込んでDataFrameを返す.

    Args:
        path (Path): CSVファイルパス

    Returns:
        pd.DataFrame: トレードデータ
    """
    df = pd.read_csv(path)
    # 数値型キャスト（読み込み時に文字列になるケースに対応）
    numeric_cols = [
        "profit_loss", "mfe_pips", "mae_pips", "mfe_r", "mae_r",
        "holding_minutes", "time_to_mfe_minutes",
        "pips", "entry_adx", "confidence",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def sep(title: str) -> None:
    """セクション区切りを出力する.

    Args:
        title (str): セクションタイトル
    """
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def fmt_pct(val: float) -> str:
    """パーセント表示フォーマット.

    Args:
        val (float): 0-1 の割合

    Returns:
        str: パーセント文字列
    """
    return f"{val * 100:.1f}%"


# ---------------------------------------------------------------------------
# 1. レジーム別利確品質分析
# ---------------------------------------------------------------------------
def analyze_regime_quality(df: pd.DataFrame) -> None:
    """レジーム別の利確品質を分析する.

    Args:
        df (pd.DataFrame): トレードデータ
    """
    sep("1. レジーム別の利確品質分析")

    df = df.copy()
    df["win"] = (df["profit_loss"] > 0).astype(int)
    df["exit_eff"] = df["pips"] / df["mfe_pips"].replace(0, np.nan)

    for regime, grp in df.groupby("regime"):
        n = len(grp)
        wins = grp["win"].sum()
        wr = wins / n if n else 0
        avg_pl = grp["profit_loss"].mean()
        avg_mfe_pips = grp["mfe_pips"].mean()
        avg_mfe_r = grp["mfe_r"].mean()
        avg_eff = grp["exit_eff"].dropna().mean()

        print(f"\n--- regime: {regime}  (N={n}) ---")
        print(f"  勝率           : {fmt_pct(wr)}  ({wins}W / {n - wins}L)")
        print(f"  平均PL         : {avg_pl:>10,.0f} 円")
        print(f"  平均MFE pips   : {avg_mfe_pips:>8.2f}")
        print(f"  平均MFE_r      : {avg_mfe_r:>8.3f}")
        print(f"  利確効率(exit/MFE): {avg_eff:>6.3f}")

        print(f"\n  [exit_reason別]")
        er_grp = (
            grp.groupby("exit_reason")["profit_loss"]
            .agg(["count", "mean"])
            .rename(columns={"count": "N", "mean": "avg_PL"})
            .sort_values("N", ascending=False)
        )
        for reason, row in er_grp.iterrows():
            print(
                f"    {reason:<20s}  N={int(row['N']):>4d}  "
                f"avg_PL={row['avg_PL']:>10,.0f}"
            )


# ---------------------------------------------------------------------------
# 2. RANGE相場での損失パターン
# ---------------------------------------------------------------------------
def analyze_range_losses(df: pd.DataFrame) -> None:
    """RANGE相場での損失パターンを分析する.

    Args:
        df (pd.DataFrame): トレードデータ
    """
    sep("2. RANGE相場での損失パターン")

    rng = df[df["regime"] == "RANGE"].copy()
    print(f"RANGE トレード数: {len(rng)}")

    # direction x PL
    print("\n[direction x 平均PL / 勝率]")
    for direction, grp in rng.groupby("direction"):
        n = len(grp)
        wr = (grp["profit_loss"] > 0).mean()
        avg_pl = grp["profit_loss"].mean()
        print(
            f"  {direction:<5s}  N={n:>4d}  WR={fmt_pct(wr)}  "
            f"avg_PL={avg_pl:>10,.0f}"
        )

    # MAE分布
    print("\n[MAE pips 分布 (逆行幅)]")
    mae = rng["mae_pips"].dropna()
    quantiles = mae.quantile([0.25, 0.50, 0.75, 0.90, 0.95])
    print(f"  中央値: {mae.median():.2f}  平均: {mae.mean():.2f}")
    for q, v in quantiles.items():
        print(f"  p{int(q * 100):>2d}: {v:.2f} pips")

    # holding_minutes vs profit_loss (分位別)
    print("\n[holding_minutes 分位別 PL / 勝率]")
    bins = [0, 30, 60, 120, 240, 480, float("inf")]
    labels = ["<30", "30-60", "60-120", "120-240", "240-480", "480+"]
    rng["hold_bin"] = pd.cut(
        rng["holding_minutes"], bins=bins, labels=labels
    )
    hgrp = rng.groupby("hold_bin", observed=True)["profit_loss"].agg(
        ["count", "mean", lambda x: (x > 0).mean()]
    )
    hgrp.columns = ["N", "avg_PL", "WR"]
    for label, row in hgrp.iterrows():
        print(
            f"  {label:<10s}  N={int(row['N']):>4d}  "
            f"WR={fmt_pct(row['WR'])}  avg_PL={row['avg_PL']:>10,.0f}"
        )

    # mfe_r < 0.1 の割合
    near_zero_mfe = (rng["mfe_r"] < 0.1).sum()
    pct = near_zero_mfe / len(rng) if len(rng) else 0
    print(
        f"\n  mfe_r < 0.1 のトレード: {near_zero_mfe}件 "
        f"({fmt_pct(pct)}) ← ほぼ利益出ずに退出"
    )


# ---------------------------------------------------------------------------
# 3. ターンポイント高掴み分析
# ---------------------------------------------------------------------------
def analyze_turnpoint_entries(df: pd.DataFrame) -> None:
    """ターンポイント高掴み（|MAE| > MFE）パターンを分析する.

    mae_r は負の値（逆行なので符号が負）のため絶対値で比較する。

    Args:
        df (pd.DataFrame): トレードデータ
    """
    sep("3. ターンポイント高掴み分析")

    total = len(df)
    df = df.copy()
    df["abs_mae_r"] = df["mae_r"].abs()

    # |mae_r| > mfe_r
    inverted = df[df["abs_mae_r"] > df["mfe_r"]]
    pct_inv = len(inverted) / total if total else 0
    print(
        f"|mae_r| > mfe_r (逆行幅 > 順行幅): "
        f"{len(inverted)}件 / {total}件 ({fmt_pct(pct_inv)})"
    )
    if len(inverted):
        print(f"  平均PL: {inverted['profit_loss'].mean():,.0f}")
        print(f"  勝率  : {fmt_pct((inverted['profit_loss'] > 0).mean())}")

    # 典型的ターンポイント高掴み: |mae_r| > 0.5 かつ mfe_r < 0.3
    tp_mask = (df["abs_mae_r"] > 0.5) & (df["mfe_r"] < 0.3)
    tp_trades = df[tp_mask]
    pct_tp = len(tp_trades) / total if total else 0
    print(
        f"\n|mae_r|>0.5 かつ mfe_r<0.3 (典型的ターンポイント高掴み): "
        f"{len(tp_trades)}件 ({fmt_pct(pct_tp)})"
    )
    if len(tp_trades):
        print(f"  平均PL: {tp_trades['profit_loss'].mean():,.0f}")
        print(f"  勝率  : {fmt_pct((tp_trades['profit_loss'] > 0).mean())}")
        print(f"  平均保有時間: {tp_trades['holding_minutes'].mean():.1f} min")

    # regime別
    print("\n[|mae_r| > mfe_r のレジーム別割合]")
    for regime, grp in df.groupby("regime"):
        n = len(grp)
        inv_n = (grp["abs_mae_r"] > grp["mfe_r"]).sum()
        print(
            f"  {regime:<8s}  逆行超: {inv_n:>4d}/{n:>4d}  "
            f"({fmt_pct(inv_n / n if n else 0)})"
        )

    print("\n[|mae_r|>0.5 & mfe_r<0.3 のレジーム別割合]")
    for regime, grp in df.groupby("regime"):
        n = len(grp)
        abs_mae = grp["mae_r"].abs()
        tp_n = ((abs_mae > 0.5) & (grp["mfe_r"] < 0.3)).sum()
        print(
            f"  {regime:<8s}  典型高掴み: {tp_n:>4d}/{n:>4d}  "
            f"({fmt_pct(tp_n / n if n else 0)})"
        )


# ---------------------------------------------------------------------------
# 4. 短時間損切りパターン
# ---------------------------------------------------------------------------
def analyze_quick_stops(df: pd.DataFrame) -> None:
    """短時間（30分未満）損切りパターンを分析する.

    Args:
        df (pd.DataFrame): トレードデータ
    """
    sep("4. 短時間損切りパターン (holding<30min & PL<0)")

    qs = df[(df["holding_minutes"] < 30) & (df["profit_loss"] < 0)]
    total_loss = df[df["profit_loss"] < 0]
    pct_of_losses = len(qs) / len(total_loss) if len(total_loss) else 0

    print(
        f"該当件数: {len(qs)}件  "
        f"(全損失トレードの {fmt_pct(pct_of_losses)})"
    )
    print(f"平均PL      : {qs['profit_loss'].mean():>10,.0f}")
    print(f"平均MAE pips: {qs['mae_pips'].mean():>8.2f}  ← 逆行幅")
    print(f"平均MFE pips: {qs['mfe_pips'].mean():>8.2f}  ← 順行幅")

    print("\n[regime別内訳]")
    for regime, grp in qs.groupby("regime"):
        n = len(grp)
        print(
            f"  {regime:<8s}  N={n:>4d}  "
            f"avg_PL={grp['profit_loss'].mean():>10,.0f}  "
            f"avg_MAE={grp['mae_pips'].mean():.2f}  "
            f"avg_MFE={grp['mfe_pips'].mean():.2f}"
        )


# ---------------------------------------------------------------------------
# 5. time_to_mfe_minutes 分析
# ---------------------------------------------------------------------------
def analyze_time_to_mfe(df: pd.DataFrame) -> None:
    """time_to_mfe_minutes の分布と勝敗別比較を分析する.

    Args:
        df (pd.DataFrame): トレードデータ
    """
    sep("5. time_to_mfe_minutes 分析")

    ttm = df["time_to_mfe_minutes"].dropna()
    print(f"有効データ数: {len(ttm)}")
    print(f"  平均: {ttm.mean():.1f} min  中央値: {ttm.median():.1f} min")
    print(f"  最小: {ttm.min():.1f}  最大: {ttm.max():.1f}")

    print("\n[分位数]")
    for q_label, q_val in [
        ("p10", 0.10), ("p25", 0.25), ("p50", 0.50),
        ("p75", 0.75), ("p90", 0.90), ("p95", 0.95),
    ]:
        print(f"  {q_label}: {ttm.quantile(q_val):.1f} min")

    # 勝敗別比較
    win_ttm = df.loc[df["profit_loss"] > 0, "time_to_mfe_minutes"].dropna()
    loss_ttm = df.loc[df["profit_loss"] <= 0, "time_to_mfe_minutes"].dropna()

    print(f"\n[利益トレード (N={len(win_ttm)})]")
    print(
        f"  平均: {win_ttm.mean():.1f} min  "
        f"中央値: {win_ttm.median():.1f} min"
    )
    for q_label, q_val in [("p25", 0.25), ("p75", 0.75)]:
        print(f"  {q_label}: {win_ttm.quantile(q_val):.1f} min")

    print(f"\n[損失トレード (N={len(loss_ttm)})]")
    print(
        f"  平均: {loss_ttm.mean():.1f} min  "
        f"中央値: {loss_ttm.median():.1f} min"
    )
    for q_label, q_val in [("p25", 0.25), ("p75", 0.75)]:
        print(f"  {q_label}: {loss_ttm.quantile(q_val):.1f} min")

    # MFE到達の早さ別勝率
    print("\n[time_to_mfe 分位別 勝率]")
    bins = [0, 10, 30, 60, 120, 240, float("inf")]
    labels = ["<10", "10-30", "30-60", "60-120", "120-240", "240+"]
    df2 = df.copy()
    df2["ttm_bin"] = pd.cut(
        df2["time_to_mfe_minutes"], bins=bins, labels=labels
    )
    tgrp = df2.groupby("ttm_bin", observed=True)["profit_loss"].agg(
        ["count", lambda x: (x > 0).mean(), "mean"]
    )
    tgrp.columns = ["N", "WR", "avg_PL"]
    for label, row in tgrp.iterrows():
        print(
            f"  {label:<10s}  N={int(row['N']):>4d}  "
            f"WR={fmt_pct(row['WR'])}  avg_PL={row['avg_PL']:>10,.0f}"
        )


def main() -> None:
    """メイン処理."""
    df = load_df(CSV_PATH)
    print(f"総トレード数: {len(df)}")
    print(f"期間: {df['entry_time'].min()} 〜 {df['entry_time'].max()}")

    analyze_regime_quality(df)
    analyze_range_losses(df)
    analyze_turnpoint_entries(df)
    analyze_quick_stops(df)
    analyze_time_to_mfe(df)

    print("\n" + "=" * 70)
    print("  分析完了")
    print("=" * 70)


if __name__ == "__main__":
    main()
