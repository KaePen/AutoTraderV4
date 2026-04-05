"""IS期間 (2023-2025) スコアリング品質構造の再現性検証

#0000081 (2024年) で発見した以下の構造的パターンが
IS期間全体で再現するかを検証する:

1. RSI=0 vs RSI=1 の品質差（年別）
2. 同スコアバンドでの RSI=0 > RSI=1 の一貫性
3. S2スコアリング（RSI bonus=0, stoch bonus=0）の予測力改善
4. 品質%→WR マッピング（原理的閾値設定の基礎データ）

Usage:
    uv run python plans/is_quality_analysis.py [result_id]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path("D:/Projects/AutoTraderV4_data/backtest/results")


def load_trades(result_id: str) -> pd.DataFrame:
    csv_path = DATA_DIR / result_id / "trades.csv"
    df = pd.read_csv(csv_path)
    df = df[df.score_rsi > -900].copy()
    # entry_time から年を抽出
    df["year"] = pd.to_datetime(df.entry_time).dt.year
    df["win"] = (df.profit_loss > 0).astype(int)
    return df


def calc_metrics(df: pd.DataFrame) -> dict:
    if len(df) == 0:
        return {"trades": 0, "wr": 0, "pf": 0, "net": 0}
    winners = df[df.profit_loss > 0]
    losers = df[df.profit_loss <= 0]
    wr = len(winners) / len(df) * 100
    tw = winners.profit_loss.sum() if len(winners) > 0 else 0
    tl = abs(losers.profit_loss.sum()) if len(losers) > 0 else 0.001
    return {"trades": len(df), "wr": wr, "pf": tw / tl, "net": df.profit_loss.sum()}


# ============================================================
# 1. RSI=0 vs RSI=1 年別比較
# ============================================================
def rsi_yearly_comparison(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("1. RSI=0 vs RSI=1: 年別品質比較")
    print("=" * 70)

    for year in sorted(df.year.unique()):
        ydf = df[df.year == year]
        rsi0 = calc_metrics(ydf[ydf.score_rsi == 0])
        rsi1 = calc_metrics(ydf[ydf.score_rsi == 1])
        delta_wr = rsi0["wr"] - rsi1["wr"] if rsi1["trades"] > 0 else float("nan")

        print(f"\n--- {year} ---")
        print(f"  RSI=0: {rsi0['trades']:>5} trades, WR {rsi0['wr']:>5.1f}%, PF {rsi0['pf']:>5.2f}")
        print(f"  RSI=1: {rsi1['trades']:>5} trades, WR {rsi1['wr']:>5.1f}%, PF {rsi1['pf']:>5.2f}")
        if not np.isnan(delta_wr):
            direction = "RSI=0 優位" if delta_wr > 0 else "RSI=1 優位"
            print(f"  Delta WR: {delta_wr:>+5.1f}pp ({direction})")

    # 全期間集計
    rsi0_all = calc_metrics(df[df.score_rsi == 0])
    rsi1_all = calc_metrics(df[df.score_rsi == 1])
    print(f"\n--- 全期間 ---")
    print(f"  RSI=0: {rsi0_all['trades']:>5} trades, WR {rsi0_all['wr']:>5.1f}%, PF {rsi0_all['pf']:>5.2f}")
    print(f"  RSI=1: {rsi1_all['trades']:>5} trades, WR {rsi1_all['wr']:>5.1f}%, PF {rsi1_all['pf']:>5.2f}")


# ============================================================
# 2. 同スコアバンドでの RSI=0 vs RSI=1
# ============================================================
def same_band_comparison(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("2. 同スコアバンドでの RSI=0 vs RSI=1 品質比較")
    print("=" * 70)

    bands = [(10, 12), (12, 14), (14, 16), (16, 18), (18, 20), (20, 25)]

    print(f"\n{'Band':>10} {'RSI0_N':>7} {'RSI0_WR':>8} {'RSI1_N':>7} {'RSI1_WR':>8} {'Delta':>7} {'Consistent':>11}")
    print("-" * 65)

    consistent_count = 0
    total_bands = 0

    for lo, hi in bands:
        band_df = df[(df.consensus_score >= lo) & (df.consensus_score < hi)]
        r0 = band_df[band_df.score_rsi == 0]
        r1 = band_df[band_df.score_rsi == 1]

        if len(r0) < 10 or len(r1) < 10:
            continue

        total_bands += 1
        m0 = calc_metrics(r0)
        m1 = calc_metrics(r1)
        delta = m0["wr"] - m1["wr"]
        consistent = delta > 0
        if consistent:
            consistent_count += 1

        mark = "YES" if consistent else "NO"
        print(
            f"  [{lo:>2}-{hi:>2}) {m0['trades']:>7} {m0['wr']:>7.1f}% "
            f"{m1['trades']:>7} {m1['wr']:>7.1f}% {delta:>+6.1f}pp {mark:>11}"
        )

    if total_bands > 0:
        print(f"\n一貫性: {consistent_count}/{total_bands} バンドでRSI=0優位")

    # 年別でも確認
    print("\n--- 年別×バンド ---")
    for year in sorted(df.year.unique()):
        ydf = df[df.year == year]
        year_consistent = 0
        year_total = 0
        for lo, hi in bands:
            band_df = ydf[(ydf.consensus_score >= lo) & (ydf.consensus_score < hi)]
            r0 = band_df[band_df.score_rsi == 0]
            r1 = band_df[band_df.score_rsi == 1]
            if len(r0) < 5 or len(r1) < 5:
                continue
            year_total += 1
            m0 = calc_metrics(r0)
            m1 = calc_metrics(r1)
            if m0["wr"] > m1["wr"]:
                year_consistent += 1
        if year_total > 0:
            print(f"  {year}: RSI=0優位 {year_consistent}/{year_total} バンド")


# ============================================================
# 3. S2スコアリングの予測力改善
# ============================================================
def s2_predictiveness(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("3. S2スコアリング予測力: r(score, win) の比較")
    print("=" * 70)

    # S2: RSI bonus=0, stoch bonus=0 → M15のscoreから差し引く
    df = df.copy()
    # S2調整: score_rsi → 0 (ボーナスのみ; 元が1.0のものを0に)
    rsi_adj = df["score_rsi"].clip(lower=0)  # ボーナス部分のみ（正の値）
    stoch_adj = df["score_stochastic"].clip(lower=0)  # ボーナス部分のみ

    # M15からの調整をconsensusに反映
    df["s2_consensus"] = df["consensus_score"] - rsi_adj - stoch_adj

    # 相関係数
    r_current = df["consensus_score"].corr(df["win"])
    r_s2 = df["s2_consensus"].corr(df["win"])

    print(f"\n全期間:")
    print(f"  現行スコアリング: r = {r_current:.4f}")
    print(f"  S2スコアリング:   r = {r_s2:.4f}")
    print(f"  改善率: {(r_s2 - r_current) / abs(r_current) * 100:+.1f}%" if r_current != 0 else "")

    # 年別
    for year in sorted(df.year.unique()):
        ydf = df[df.year == year]
        r_cur_y = ydf["consensus_score"].corr(ydf["win"])
        r_s2_y = ydf["s2_consensus"].corr(ydf["win"])
        print(f"  {year}: 現行 r={r_cur_y:.4f}, S2 r={r_s2_y:.4f}")

    # S2での閾値スイープ
    print(f"\n--- S2スコア閾値スイープ ---")
    print(f"{'Thresh':>7} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Net':>12}")
    print("-" * 45)

    for threshold in np.arange(10.0, 22.0, 1.0):
        filtered = df[df["s2_consensus"] >= threshold]
        if len(filtered) < 20:
            break
        m = calc_metrics(filtered)
        print(f"{threshold:>7.1f} {m['trades']:>7} {m['wr']:>6.1f} {m['pf']:>6.2f} {m['net']:>12,.0f}")


# ============================================================
# 4. 品質%→WR マッピング
# ============================================================
def quality_wr_mapping(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("4. 品質パーセンタイル → WR マッピング（原理的閾値設定）")
    print("=" * 70)

    # consensus_scoreのパーセンタイルとWRの関係
    percentiles = [10, 20, 30, 40, 50, 60, 70, 80, 90, 95]

    print(f"\n--- 現行スコアリング ---")
    print(f"{'Percentile':>11} {'Threshold':>10} {'Trades':>7} {'WR%':>6} {'PF':>6}")
    print("-" * 45)

    for pct in percentiles:
        threshold = np.percentile(df.consensus_score, pct)
        filtered = df[df.consensus_score >= threshold]
        m = calc_metrics(filtered)
        print(f"  Top {100-pct:>3}% {threshold:>10.2f} {m['trades']:>7} {m['wr']:>6.1f} {m['pf']:>6.2f}")

    # S2スコアリングでの同じ分析
    df = df.copy()
    rsi_adj = df["score_rsi"].clip(lower=0)
    stoch_adj = df["score_stochastic"].clip(lower=0)
    df["s2_consensus"] = df["consensus_score"] - rsi_adj - stoch_adj

    print(f"\n--- S2スコアリング ---")
    print(f"{'Percentile':>11} {'Threshold':>10} {'Trades':>7} {'WR%':>6} {'PF':>6}")
    print("-" * 45)

    for pct in percentiles:
        threshold = np.percentile(df.s2_consensus, pct)
        filtered = df[df.s2_consensus >= threshold]
        m = calc_metrics(filtered)
        print(f"  Top {100-pct:>3}% {threshold:>10.2f} {m['trades']:>7} {m['wr']:>6.1f} {m['pf']:>6.2f}")


# ============================================================
# 5. コンポーネント予測力（年別）
# ============================================================
def component_predictiveness_yearly(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("5. スコアコンポーネント予測力: W/Lデルタ（年別）")
    print("=" * 70)

    score_cols = [c for c in df.columns if c.startswith("score_")]

    for year in sorted(df.year.unique()):
        ydf = df[df.year == year]
        winners = ydf[ydf.pips > 0]
        losers = ydf[ydf.pips <= 0]

        print(f"\n--- {year} ({len(ydf)} trades) ---")
        print(f"{'Component':>20} {'Win_mean':>9} {'Loss_mean':>10} {'Delta':>7} {'Dir':>8}")
        print("-" * 58)

        for col in score_cols:
            wm = winners[col].mean()
            lm = losers[col].mean()
            delta = wm - lm
            d = "CORRECT" if delta > 0.05 else "INVERSE" if delta < -0.05 else "neutral"
            print(f"{col:>20} {wm:>9.3f} {lm:>10.3f} {delta:>+7.3f} {d:>8}")


# ============================================================
# 6. Stoch値別品質（年別）
# ============================================================
def stoch_yearly_analysis(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("6. ストキャスティクス値別品質（年別）")
    print("=" * 70)

    for year in sorted(df.year.unique()):
        ydf = df[df.year == year]
        print(f"\n--- {year} ---")
        for val in sorted(ydf.score_stochastic.unique()):
            grp = ydf[ydf.score_stochastic == val]
            if len(grp) < 5:
                continue
            m = calc_metrics(grp)
            print(f"  Stoch={val:>5.1f}: {m['trades']:>5} trades, WR {m['wr']:>5.1f}%, PF {m['pf']:>5.2f}")


# ============================================================
# 7. 理論最大値からの品質%マッピング
# ============================================================
def theoretical_quality_mapping(df: pd.DataFrame) -> None:
    print("\n" + "=" * 70)
    print("7. 理論最大値からの品質%→WR マッピング")
    print("=" * 70)

    # 理論最大値: 21.5 (全TF role_weight合計)
    THEORETICAL_MAX = 21.5

    df = df.copy()
    df["quality_pct"] = df["consensus_score"] / THEORETICAL_MAX * 100

    # S2版
    rsi_adj = df["score_rsi"].clip(lower=0)
    stoch_adj = df["score_stochastic"].clip(lower=0)
    df["s2_consensus"] = df["consensus_score"] - rsi_adj - stoch_adj
    # S2の理論最大値はボーナス分減る: RSI=1.0, stoch=0.5 → per TF -1.5
    # ただし全TFで同じ減少ではない。M15のみの調整。
    # S2理論最大 ≈ 21.5 - 1.5(M15のRSI+stochボーナス分が影響) ≈ 21.5 のまま使う
    df["s2_quality_pct"] = df["s2_consensus"] / THEORETICAL_MAX * 100

    # 品質% バケットごとのWR
    print(f"\n--- 現行スコアリング ---")
    print(f"{'Quality%':>10} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Avg_Score':>10}")
    print("-" * 45)

    for lo_pct in range(40, 100, 5):
        hi_pct = lo_pct + 5
        lo_score = lo_pct / 100 * THEORETICAL_MAX
        hi_score = hi_pct / 100 * THEORETICAL_MAX
        grp = df[(df.consensus_score >= lo_score) & (df.consensus_score < hi_score)]
        if len(grp) < 10:
            continue
        m = calc_metrics(grp)
        avg_s = grp.consensus_score.mean()
        print(
            f"  {lo_pct:>3}-{hi_pct:>3}% {m['trades']:>7} {m['wr']:>6.1f} "
            f"{m['pf']:>6.2f} {avg_s:>10.2f}"
        )

    # 累積（Top X%以上）
    print(f"\n--- 累積: 品質%以上の全トレード ---")
    print(f"{'Quality>=':>10} {'Score>=':>8} {'Trades':>7} {'WR%':>6} {'PF':>6}")
    print("-" * 45)

    for pct in [50, 55, 60, 65, 70, 75, 80, 85, 90, 95]:
        score = pct / 100 * THEORETICAL_MAX
        filtered = df[df.consensus_score >= score]
        if len(filtered) < 10:
            continue
        m = calc_metrics(filtered)
        print(f"  >= {pct:>3}% {score:>8.2f} {m['trades']:>7} {m['wr']:>6.1f} {m['pf']:>6.2f}")


def main():
    result_id = sys.argv[1] if len(sys.argv) > 1 else "0000083"

    print(f"Loading trades from #{result_id}...")
    df = load_trades(result_id)
    print(f"Total trades: {len(df)}")
    print(f"Years: {sorted(df.year.unique())}")
    print(f"Score range: {df.consensus_score.min():.1f} - {df.consensus_score.max():.1f}")

    # 年別サマリ
    print(f"\n--- 年別サマリ ---")
    for year in sorted(df.year.unique()):
        ydf = df[df.year == year]
        m = calc_metrics(ydf)
        print(f"  {year}: {m['trades']:>5} trades, WR {m['wr']:>5.1f}%, PF {m['pf']:>5.2f}")

    # 各分析実行
    rsi_yearly_comparison(df)
    same_band_comparison(df)
    s2_predictiveness(df)
    quality_wr_mapping(df)
    component_predictiveness_yearly(df)
    stoch_yearly_analysis(df)
    theoretical_quality_mapping(df)

    print("\n" + "=" * 70)
    print("分析完了")
    print("=" * 70)


if __name__ == "__main__":
    main()
