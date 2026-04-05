"""Post-hoc Scoring Weight Optimization

探索BT (#0000081, consensus_threshold=10.0) のtrades.csvを使い、
各スコア成分の重みを変えた場合のWR/PFを閾値スイープで評価する。

Usage:
    uv run python plans/scoring_posthoc_analysis.py [result_id]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path("D:/Projects/AutoTraderV4_data/backtest/results")
BASELINE_ID = "0000077"  # v4.3.0 baseline (2024)


def load_trades(result_id: str) -> pd.DataFrame:
    csv_path = DATA_DIR / result_id / "trades.csv"
    df = pd.read_csv(csv_path)
    # RSI=-999 (ブロック) は外れ値として除外
    score_cols = [c for c in df.columns if c.startswith("score_")]
    df = df[df.score_rsi > -900].copy()
    return df


def calc_metrics(df: pd.DataFrame) -> dict:
    """基本メトリクス計算"""
    if len(df) == 0:
        return {"trades": 0, "wr": 0, "pf": 0, "net": 0, "avg_pips": 0}
    winners = df[df.profit_loss > 0]
    losers = df[df.profit_loss <= 0]
    wr = len(winners) / len(df) * 100
    tw = winners.profit_loss.sum() if len(winners) > 0 else 0
    tl = abs(losers.profit_loss.sum()) if len(losers) > 0 else 0.001
    return {
        "trades": len(df),
        "wr": wr,
        "pf": tw / tl,
        "net": df.profit_loss.sum(),
        "avg_pips": df.pips.mean(),
    }


def component_analysis(df: pd.DataFrame, label: str) -> None:
    """W/L比較による各成分の予測力分析"""
    score_cols = [c for c in df.columns if c.startswith("score_")]
    winners = df[df.pips > 0]
    losers = df[df.pips <= 0]

    print(f"\n=== {label}: Component Predictiveness ===")
    print(f"{'Component':>20} {'Win_mean':>9} {'Loss_mean':>10} {'Delta':>7} {'Direction':>10}")
    print("-" * 60)

    for col in score_cols:
        wm = winners[col].mean()
        lm = losers[col].mean()
        delta = wm - lm
        direction = "CORRECT" if delta > 0.05 else "INVERSE" if delta < -0.05 else "neutral"
        print(f"{col:>20} {wm:>9.3f} {lm:>10.3f} {delta:>+7.3f} {direction:>10}")


def threshold_sweep(
    df: pd.DataFrame,
    score_col: str = "consensus_score",
    label: str = "",
    target_trades: int | None = None,
) -> pd.DataFrame:
    """閾値スイープ"""
    results = []
    min_score = df[score_col].min()
    max_score = df[score_col].max()

    for threshold in np.arange(
        max(min_score, 5.0), min(max_score + 0.5, 25.0), 0.5
    ):
        filtered = df[df[score_col] >= threshold]
        if len(filtered) < 10:
            break
        m = calc_metrics(filtered)
        m["threshold"] = threshold
        results.append(m)

    result_df = pd.DataFrame(results)
    if len(result_df) == 0:
        print(f"  No valid thresholds for {label}")
        return result_df

    print(f"\n=== {label}: Threshold Sweep on '{score_col}' ===")
    print(
        f"{'Thresh':>7} {'Trades':>7} {'WR%':>6} {'PF':>6} "
        f"{'NetPL':>12} {'AvgPips':>8}"
    )
    print("-" * 55)
    for _, row in result_df.iterrows():
        marker = ""
        if target_trades and abs(row["trades"] - target_trades) < 50:
            marker = " <-- ~baseline"
        print(
            f"{row['threshold']:>7.1f} {row['trades']:>7.0f} "
            f"{row['wr']:>6.1f} {row['pf']:>6.2f} "
            f"{row['net']:>12,.0f} {row['avg_pips']:>8.2f}{marker}"
        )
    return result_df


def simulate_weight_changes(df: pd.DataFrame) -> None:
    """異なるスコア重み構成のシミュレーション

    consensus_score は多TFの加重和だが、ScoreBreakdownはprimary TF (M15) のみ。
    RSI/stochの変更はprimary TFだけでなく全TFに影響する。
    M15の寄与は consensus の約2.0/21.5 ≈ 9.3%。

    ここでは以下をシミュレート:
    - M15のscoreコンポーネントを変更 → その分だけconsensusが変わる
    - 全TFで同じ変更が起きる想定で、影響を推定（倍率 ≈ 全TF共通 / primary_only）
    """
    score_cols = [c for c in df.columns if c.startswith("score_")]

    # M15のコンポーネントが全体に占める割合を推定
    # component_sum ≈ M15の寄与（primary TF, weight=2.0）
    # gap ≈ 他の7TFの寄与
    df["component_sum"] = df[score_cols].sum(axis=1)
    df["gap"] = df["consensus_score"] - df["component_sum"]

    # M15のstrengthは component_sum / 10.0 (capped at 1.0)
    # M15のconsensus寄与 = 2.0 * min(component_sum/10, 1.0)
    # ≈ component_sum * 0.2 (for component_sum < 10)
    # = 2.0 (for component_sum >= 10)

    print("\n" + "=" * 70)
    print("SCORING WEIGHT SIMULATION")
    print("=" * 70)

    scenarios = [
        {
            "name": "S2: RSI bonus → 0",
            "changes": {"score_rsi": 0.0},
            "desc": "RSIボーナスを無効化（ペナルティは元々存在しない）",
        },
        {
            "name": "S3: Stoch penalty → 0",
            "changes": {"score_stochastic": 0.0},
            "desc": "ストキャスティクスペナルティ/ボーナスを無効化",
        },
        {
            "name": "S4: RSI=0 + Stoch=0",
            "changes": {"score_rsi": 0.0, "score_stochastic": 0.0},
            "desc": "RSI + ストキャスティクス両方無効化",
        },
        {
            "name": "S5: RSI inverted (-1×)",
            "changes": {"score_rsi": "invert"},
            "desc": "RSIスコアを反転（ボーナス→ペナルティ）",
        },
        {
            "name": "S6: EMA cross → 0",
            "changes": {"score_ema_cross": 0.0},
            "desc": "EMAクロスペナルティ/ボーナスを無効化",
        },
        {
            "name": "S7: All inverse → 0",
            "changes": {
                "score_rsi": 0.0,
                "score_stochastic": 0.0,
                "score_ema_cross": 0.0,
                "score_divergence": 0.0,
            },
            "desc": "全逆相関成分を無効化",
        },
    ]

    # M15のみの調整（実際の影響の下限推定）
    for scenario in scenarios:
        name = scenario["name"]
        changes = scenario["changes"]
        desc = scenario["desc"]

        df_sim = df.copy()
        adjustment = 0.0

        for col, target in changes.items():
            if target == "invert":
                delta = -2.0 * df_sim[col]  # 元の値の反転分
                df_sim[col] = -df_sim[col]
            else:
                delta = target - df_sim[col]
                df_sim[col] = target
            adjustment += delta

        # M15のみの調整: consensus_score += adjustment (primary TFのみ)
        # ただしstrength capping (min(score/10, 1.0)) の影響あり
        # 近似: adjustment をそのままconsensusに加算（capping無視）
        df_sim["adjusted_consensus_m15"] = (
            df_sim["consensus_score"] + adjustment
        )

        # 全TF推定: 全TFで同じ変更が起きた場合
        # 近似倍率: total_weight / primary_weight = 21.5 / 2.0 ≈ 10.75
        # ただし一部TFでは変更なし（RSI=0のTFではdelta=0）
        # → 実効倍率は低い。データなしでの推定は不正確なので省略。

        print(f"\n--- {name}: {desc} ---")
        print("(M15 only adjustment - lower bound estimate)")
        threshold_sweep(
            df_sim,
            "adjusted_consensus_m15",
            f"{name} (M15 only)",
            target_trades=836,
        )


def main():
    result_id = sys.argv[1] if len(sys.argv) > 1 else "0000081"

    print(f"Loading trades from #{result_id}...")
    df = load_trades(result_id)
    print(f"Total trades: {len(df)}")
    print(f"Score range: {df.consensus_score.min():.1f} - {df.consensus_score.max():.1f}")

    # ベースライン読み込み
    try:
        baseline = load_trades(BASELINE_ID)
        baseline_metrics = calc_metrics(baseline)
        print(f"\nBaseline (#{BASELINE_ID}): {baseline_metrics['trades']} trades, "
              f"WR {baseline_metrics['wr']:.1f}%, PF {baseline_metrics['pf']:.2f}")
    except Exception:
        print("Baseline not available")
        baseline_metrics = {"trades": 836}

    # 1. コンポーネント分析
    component_analysis(df, f"Explore #{result_id}")

    # 2. 現行スコアでの閾値スイープ（現行scoring）
    threshold_sweep(
        df, "consensus_score", "Original Scoring",
        target_trades=baseline_metrics.get("trades", 836),
    )

    # 3. 重み変更シミュレーション
    simulate_weight_changes(df)

    # 4. RSI値別の品質分析
    print("\n=== RSI Score Distribution & Quality ===")
    for val in sorted(df.score_rsi.unique()):
        grp = df[df.score_rsi == val]
        if len(grp) < 5:
            continue
        m = calc_metrics(grp)
        print(f"  RSI={val:>5.1f}: {m['trades']:>5} trades, "
              f"WR {m['wr']:>5.1f}%, PF {m['pf']:>5.2f}")

    # 5. Stoch値別の品質分析
    print("\n=== Stochastic Score Distribution & Quality ===")
    for val in sorted(df.score_stochastic.unique()):
        grp = df[df.score_stochastic == val]
        if len(grp) < 5:
            continue
        m = calc_metrics(grp)
        print(f"  Stoch={val:>5.1f}: {m['trades']:>5} trades, "
              f"WR {m['wr']:>5.1f}%, PF {m['pf']:>5.2f}")

    # 6. EMA cross値別
    print("\n=== EMA Cross Score Distribution & Quality ===")
    for val in sorted(df.score_ema_cross.unique()):
        grp = df[df.score_ema_cross == val]
        if len(grp) < 5:
            continue
        m = calc_metrics(grp)
        print(f"  EMA={val:>5.1f}: {m['trades']:>5} trades, "
              f"WR {m['wr']:>5.1f}%, PF {m['pf']:>5.2f}")

    # 7. ベスト組み���わせ探索
    print("\n=== Best Combinations (RSI × Stoch) ===")
    for rsi_val in sorted(df.score_rsi.unique()):
        for stoch_val in sorted(df.score_stochastic.unique()):
            grp = df[
                (df.score_rsi == rsi_val)
                & (df.score_stochastic == stoch_val)
            ]
            if len(grp) < 10:
                continue
            m = calc_metrics(grp)
            quality = "***" if m["pf"] > 4.0 else "**" if m["pf"] > 3.0 else "*" if m["pf"] > 2.0 else ""
            print(
                f"  RSI={rsi_val:>5.1f} × Stoch={stoch_val:>5.1f}: "
                f"{m['trades']:>5} trades, WR {m['wr']:>5.1f}%, "
                f"PF {m['pf']:>5.2f} {quality}"
            )


if __name__ == "__main__":
    main()
