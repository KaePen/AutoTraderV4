"""辛口コメント Round 4 + 統計的分析スクリプト

trades.csvから以下を分析:
- #21: 週末ギャップリスク
- #22: 月跨ぎポジション分析
- #23: 最大連敗ストリーク
- #24: 保有時間の非対称性
- #26: エッジ経年劣化
- #27: 資金効率（同時ポジション数）
- A: モンテカルロDD分布
- B: Kelly Criterion
- C: Risk of Ruin
- D: リターン自己相関

Usage: uv run python scripts/round4_statistical_analysis.py <trades.csv>
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def load_trades(csv_path: str) -> pd.DataFrame:
    """trades.csv読み込み"""
    df = pd.read_csv(
        csv_path, parse_dates=["entry_time", "exit_time"],
    )
    return df


# ── #21: 週末ギャップ分析 ──


def analyze_weekend_risk(df: pd.DataFrame) -> dict:
    """週末ギャップリスク分析"""
    df = df.copy()
    df["entry_dow"] = df["entry_time"].dt.dayofweek
    df["exit_dow"] = df["exit_time"].dt.dayofweek

    friday_entry = df[df["entry_dow"] == 4]
    weekend_crossing = df[
        (df["entry_dow"] == 4) & (df["exit_dow"] == 0)
    ]

    all_sl_rate = (
        (df["exit_reason"] == "SL_HIT").mean() * 100
    )
    fri_sl_rate = (
        (friday_entry["exit_reason"] == "SL_HIT").mean()
        * 100
        if len(friday_entry) > 0
        else 0
    )

    return {
        "friday_entry_count": len(friday_entry),
        "friday_entry_pct": round(
            len(friday_entry) / len(df) * 100, 1,
        ) if len(df) > 0 else 0,
        "weekend_crossing_count": len(weekend_crossing),
        "friday_avg_pnl": round(
            friday_entry["profit_loss"].mean(),
        ) if len(friday_entry) > 0 else 0,
        "friday_wr": round(
            (friday_entry["profit_loss"] > 0).mean()
            * 100,
            1,
        ) if len(friday_entry) > 0 else 0,
        "all_sl_hit_rate": round(all_sl_rate, 1),
        "friday_sl_hit_rate": round(fri_sl_rate, 1),
    }


# ── #22: 月跨ぎポジション分析 ──


def analyze_month_crossing(df: pd.DataFrame) -> dict:
    """月をまたぐポジションの分析"""
    df = df.copy()
    df["entry_month"] = df["entry_time"].dt.to_period("M")
    df["exit_month"] = df["exit_time"].dt.to_period("M")
    crossing = df[df["entry_month"] != df["exit_month"]]
    non_crossing = df[
        df["entry_month"] == df["exit_month"]
    ]

    df["days_to_month_end"] = df.apply(
        lambda r: (
            r["entry_time"] + pd.offsets.MonthEnd(0)
        ).day
        - r["entry_time"].day,
        axis=1,
    )
    late_entry = df[df["days_to_month_end"] <= 2]

    return {
        "total_trades": len(df),
        "crossing_trades": len(crossing),
        "crossing_pct": (
            len(crossing) / len(df) * 100
            if len(df) > 0
            else 0
        ),
        "crossing_avg_pnl": (
            crossing["profit_loss"].mean()
            if len(crossing) > 0
            else 0
        ),
        "non_crossing_avg_pnl": (
            non_crossing["profit_loss"].mean()
            if len(non_crossing) > 0
            else 0
        ),
        "crossing_wr": (
            (crossing["profit_loss"] > 0).mean() * 100
            if len(crossing) > 0
            else 0
        ),
        "non_crossing_wr": (
            (non_crossing["profit_loss"] > 0).mean()
            * 100
            if len(non_crossing) > 0
            else 0
        ),
        "late_entry_trades": len(late_entry),
        "late_entry_avg_pnl": (
            late_entry["profit_loss"].mean()
            if len(late_entry) > 0
            else 0
        ),
        "late_entry_wr": (
            (late_entry["profit_loss"] > 0).mean() * 100
            if len(late_entry) > 0
            else 0
        ),
    }


# ── #23: 最大連敗ストリーク ──


def analyze_loss_streaks(df: pd.DataFrame) -> dict:
    """連敗ストリーク分析"""
    df = df.sort_values("entry_time").reset_index(
        drop=True,
    )
    wins = (df["profit_loss"] > 0).astype(int)

    streaks: list[tuple[int, int]] = []
    current_type: int | None = None
    current_len = 0
    for w in wins:
        if w == current_type:
            current_len += 1
        else:
            if current_type is not None:
                streaks.append((current_type, current_len))
            current_type = w
            current_len = 1
    if current_type is not None:
        streaks.append((current_type, current_len))

    loss_streaks = [
        s[1] for s in streaks if s[0] == 0
    ]
    win_streaks = [
        s[1] for s in streaks if s[0] == 1
    ]

    # 連敗中の合計損失
    loss_streak_losses: list[float] = []
    current_loss = 0.0
    for _, row in df.iterrows():
        if row["profit_loss"] <= 0:
            current_loss += row["profit_loss"]
        else:
            if current_loss < 0:
                loss_streak_losses.append(current_loss)
            current_loss = 0.0
    if current_loss < 0:
        loss_streak_losses.append(current_loss)

    wr = float(wins.mean())
    lr = 1 - wr
    theoretical = {}
    for n in [5, 7, 10, 15]:
        p = 1 - (1 - lr**n) ** len(df)
        theoretical[f"p_{n}_loss_streak"] = (
            min(p, 1.0) * 100
        )

    return {
        "max_loss_streak": (
            max(loss_streaks) if loss_streaks else 0
        ),
        "max_win_streak": (
            max(win_streaks) if win_streaks else 0
        ),
        "avg_loss_streak": (
            float(np.mean(loss_streaks))
            if loss_streaks
            else 0
        ),
        "loss_streak_count": len(loss_streaks),
        "worst_streak_loss": (
            min(loss_streak_losses)
            if loss_streak_losses
            else 0
        ),
        "loss_streak_distribution": {
            k: sum(1 for s in loss_streaks if s >= k)
            for k in [3, 5, 7, 10]
        },
        "theoretical_probability": theoretical,
        "observed_wr": wr * 100,
    }


# ── #24: 保有時間の非対称性 ──


def analyze_hold_time(df: pd.DataFrame) -> dict:
    """勝ち/負けトレードの保有時間分析"""
    df = df.copy()
    df["hold_minutes"] = (
        (df["exit_time"] - df["entry_time"])
        .dt.total_seconds()
        / 60
    )

    winners = df[df["profit_loss"] > 0]
    losers = df[df["profit_loss"] <= 0]

    result: dict = {
        "avg_hold_all": df["hold_minutes"].mean(),
        "median_hold_all": df["hold_minutes"].median(),
        "avg_hold_winners": (
            winners["hold_minutes"].mean()
            if len(winners) > 0
            else 0
        ),
        "avg_hold_losers": (
            losers["hold_minutes"].mean()
            if len(losers) > 0
            else 0
        ),
        "median_hold_winners": (
            winners["hold_minutes"].median()
            if len(winners) > 0
            else 0
        ),
        "median_hold_losers": (
            losers["hold_minutes"].median()
            if len(losers) > 0
            else 0
        ),
        "hold_ratio": (
            losers["hold_minutes"].mean()
            / winners["hold_minutes"].mean()
            if len(winners) > 0
            and winners["hold_minutes"].mean() > 0
            else 0
        ),
    }

    for reason in df["exit_reason"].unique():
        subset = df[df["exit_reason"] == reason]
        result[f"avg_hold_{reason}"] = (
            subset["hold_minutes"].mean()
        )
        result[f"count_{reason}"] = len(subset)

    return result


# ── #26: エッジ経年劣化 ──


def analyze_edge_decay(df: pd.DataFrame) -> dict:
    """年別パフォーマンス推移"""
    df = df.copy()
    df["year"] = df["entry_time"].dt.year

    yearly: dict = {}
    for year, group in df.groupby("year"):
        w = group[group["profit_loss"] > 0]
        l = group[group["profit_loss"] <= 0]
        gp = w["profit_loss"].sum()
        gl = abs(l["profit_loss"].sum())
        pf = gp / gl if gl > 0 else float("inf")
        yearly[int(year)] = {
            "trades": len(group),
            "pf": round(pf, 2),
            "wr": round(len(w) / len(group) * 100, 1),
            "net": round(group["profit_loss"].sum()),
            "avg_win": (
                round(w["profit_loss"].mean())
                if len(w) > 0
                else 0
            ),
            "avg_loss": (
                round(l["profit_loss"].mean())
                if len(l) > 0
                else 0
            ),
        }

    years = sorted(yearly.keys())
    pfs = [yearly[y]["pf"] for y in years]
    if len(years) >= 2:
        x = np.array(years, dtype=float)
        y_vals = np.array(pfs, dtype=float)
        slope, _ = np.polyfit(x, y_vals, 1)
        trend = (
            "improving"
            if slope > 0.01
            else "declining"
            if slope < -0.01
            else "stable"
        )
    else:
        slope = 0.0
        trend = "insufficient_data"

    return {
        "yearly": yearly,
        "pf_trend_slope": round(slope, 4),
        "pf_trend": trend,
    }


# ── #27: 資金効率 ──


def analyze_capital_efficiency(
    df: pd.DataFrame,
) -> dict:
    """同時ポジション数の分布"""
    df = df.sort_values("entry_time").reset_index(
        drop=True,
    )
    if len(df) == 0:
        return {}

    events: list[tuple] = []
    for _, row in df.iterrows():
        events.append((row["entry_time"], 1))
        events.append((row["exit_time"], -1))
    events.sort(key=lambda x: x[0])

    pos_count = 0
    total_weighted = 0.0
    total_time = 0.0
    prev_time = events[0][0]
    pos_time: dict[int, float] = defaultdict(float)

    for t, delta in events:
        dt = (t - prev_time).total_seconds()
        if dt > 0:
            total_weighted += pos_count * dt
            total_time += dt
            pos_time[pos_count] += dt
        pos_count += delta
        prev_time = t

    return {
        "avg_simultaneous_positions": round(
            total_weighted / total_time, 2,
        )
        if total_time > 0
        else 0,
        "position_distribution_pct": {
            n: round(pos_time[n] / total_time * 100, 1)
            for n in sorted(pos_time.keys())
        },
        "total_trading_hours": round(
            total_time / 3600,
        ),
    }


# ── A: モンテカルロDD分布 ──


def _calc_observed_dd(
    pnls: np.ndarray, initial_balance: float,
) -> float:
    """実際のDD計算"""
    equity = initial_balance + np.cumsum(pnls)
    equity = np.insert(equity, 0, initial_balance)
    peak = np.maximum.accumulate(equity)
    dd_pct = (peak - equity) / peak * 100
    return round(float(dd_pct.max()), 2)


def monte_carlo_dd(
    df: pd.DataFrame,
    n_simulations: int = 10000,
    initial_balance: float = 1_000_000,
) -> dict:
    """トレード順序シャッフルによるDD分布推定"""
    pnls = df["profit_loss"].values.copy()
    max_dds = np.zeros(n_simulations)
    rng = np.random.default_rng(42)

    for i in range(n_simulations):
        shuffled = rng.permutation(pnls)
        equity = initial_balance + np.cumsum(shuffled)
        equity = np.insert(equity, 0, initial_balance)
        peak = np.maximum.accumulate(equity)
        dd_pct = (peak - equity) / peak * 100
        max_dds[i] = dd_pct.max()

    obs = _calc_observed_dd(pnls, initial_balance)
    return {
        "observed_dd": obs,
        "mc_mean_dd": round(float(np.mean(max_dds)), 2),
        "mc_median_dd": round(
            float(np.median(max_dds)), 2,
        ),
        "mc_p90_dd": round(
            float(np.percentile(max_dds, 90)), 2,
        ),
        "mc_p95_dd": round(
            float(np.percentile(max_dds, 95)), 2,
        ),
        "mc_p99_dd": round(
            float(np.percentile(max_dds, 99)), 2,
        ),
        "mc_max_dd": round(float(np.max(max_dds)), 2),
        "mc_min_dd": round(float(np.min(max_dds)), 2),
        "p_worse_than_observed": round(
            float(np.mean(max_dds > obs)) * 100, 1,
        ),
    }


# ── B: Kelly Criterion ──


def kelly_criterion(df: pd.DataFrame) -> dict:
    """Kelly基準による最適リスク率"""
    wins = df[df["profit_loss"] > 0]
    losses = df[df["profit_loss"] <= 0]

    if len(losses) == 0 or len(wins) == 0:
        return {"error": "分析不可"}

    wr = len(wins) / len(df)
    avg_win = wins["profit_loss"].mean()
    avg_loss = abs(losses["profit_loss"].mean())
    b = avg_win / avg_loss if avg_loss > 0 else 0

    p, q = wr, 1 - wr
    kelly_full = (b * p - q) / b if b > 0 else 0
    current_risk = 0.005

    return {
        "win_rate": round(wr * 100, 1),
        "payoff_ratio": round(b, 3),
        "kelly_full_pct": round(kelly_full * 100, 2),
        "kelly_half_pct": round(kelly_full / 2 * 100, 2),
        "kelly_quarter_pct": round(
            kelly_full / 4 * 100, 2,
        ),
        "current_risk_pct": current_risk * 100,
        "current_vs_kelly": (
            "conservative"
            if current_risk < kelly_full / 4
            else "moderate"
            if current_risk < kelly_full / 2
            else "aggressive"
            if current_risk < kelly_full
            else "over_kelly"
        ),
    }


# ── C: Risk of Ruin ──


def risk_of_ruin(df: pd.DataFrame) -> dict:
    """DD到達確率（モンテカルロ推定）"""
    wins = df[df["profit_loss"] > 0]
    losses = df[df["profit_loss"] <= 0]

    if len(losses) == 0 or len(wins) == 0:
        return {"error": "分析不可"}

    wr = len(wins) / len(df)
    avg_win = wins["profit_loss"].mean()
    avg_loss = abs(losses["profit_loss"].mean())
    edge = wr * avg_win - (1 - wr) * avg_loss

    pnls = df["profit_loss"].values
    rng = np.random.default_rng(42)
    n_sim = 50000
    initial_balance = 1_000_000

    dd_reach: dict[int, int] = {
        2: 0, 3: 0, 5: 0, 7: 0, 10: 0,
    }
    for _ in range(n_sim):
        shuffled = rng.permutation(pnls)
        equity = initial_balance + np.cumsum(shuffled)
        equity = np.insert(equity, 0, initial_balance)
        peak = np.maximum.accumulate(equity)
        dd_pct = (peak - equity) / peak * 100
        max_dd = dd_pct.max()
        for level in dd_reach:
            if max_dd >= level:
                dd_reach[level] += 1

    return {
        "edge_per_trade": round(edge),
        "avg_trade_size": round(
            df["profit_loss"].abs().mean(),
        ),
        "dd_reach_probability": {
            f"{k}%": round(v / n_sim * 100, 2)
            for k, v in dd_reach.items()
        },
    }


# ── D: リターン自己相関 ──


def return_autocorrelation(df: pd.DataFrame) -> dict:
    """トレードリターンの自己相関分析"""
    df = df.sort_values("entry_time").reset_index(
        drop=True,
    )
    returns = df["profit_loss"].values

    if len(returns) < 50:
        return {"error": "データ不足"}

    n = len(returns)
    mean_r = np.mean(returns)
    var_r = np.var(returns)

    autocorrs: dict[int, float] = {}
    for lag in range(1, 11):
        if var_r == 0:
            autocorrs[lag] = 0.0
            continue
        c = np.sum(
            (returns[: n - lag] - mean_r)
            * (returns[lag:] - mean_r)
        ) / (n * var_r)
        autocorrs[lag] = round(float(c), 4)

    ci_95 = 1.96 / math.sqrt(n)
    significant_lags = [
        lag
        for lag, ac in autocorrs.items()
        if abs(ac) > ci_95
    ]

    # Wald-Wolfowitz runs test
    binary = (returns > 0).astype(int)
    runs = 1
    for i in range(1, len(binary)):
        if binary[i] != binary[i - 1]:
            runs += 1
    n1 = int(binary.sum())
    n0 = len(binary) - n1
    total = n1 + n0
    expected_runs = (
        1 + 2 * n1 * n0 / total if total > 0 else 0
    )
    var_runs = (
        2 * n1 * n0 * (2 * n1 * n0 - total)
        / (total**2 * (total - 1))
        if total > 1
        else 0
    )
    z_runs = (
        (runs - expected_runs) / math.sqrt(var_runs)
        if var_runs > 0
        else 0
    )

    return {
        "autocorrelations": autocorrs,
        "ci_95": round(ci_95, 4),
        "significant_lags": significant_lags,
        "independence": (
            "independent"
            if len(significant_lags) == 0
            else "correlated"
        ),
        "runs_test": {
            "observed_runs": runs,
            "expected_runs": round(expected_runs, 1),
            "z_score": round(z_runs, 2),
            "conclusion": (
                "random"
                if abs(z_runs) < 1.96
                else "clustered"
                if z_runs < -1.96
                else "alternating"
            ),
        },
    }


# ── レポート出力 ──


def format_report(
    results: dict, csv_path: str,
) -> str:
    """Markdown形式のレポート生成"""
    lines = [
        "# Round 4 + 統計的分析レポート",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Data: {csv_path}",
        f"Trades: {results.get('total_trades', '?')}",
        "",
    ]

    # #21
    wr = results.get("weekend_risk", {})
    lines.extend([
        "---", "## #21. 週末ギャップリスク", "",
        "| 指標 | 値 |", "|------|------|",
        f"| 金曜エントリー数 | {wr.get('friday_entry_count', 0)}"
        f" ({wr.get('friday_entry_pct', 0)}%) |",
        f"| 週末跨ぎ数 | {wr.get('weekend_crossing_count', 0)} |",
        f"| 金曜avg PnL | {wr.get('friday_avg_pnl', 0):,} |",
        f"| 金曜WR | {wr.get('friday_wr', 0)}% |",
        f"| 金曜SLヒット率 | {wr.get('friday_sl_hit_rate', 0)}%"
        f" (全体: {wr.get('all_sl_hit_rate', 0)}%) |",
        "",
    ])

    # #22
    mc = results.get("month_crossing", {})
    lines.extend([
        "---", "## #22. 月跨ぎポジション分析", "",
        "| 指標 | 値 |", "|------|------|",
        f"| 月跨ぎトレード | {mc.get('crossing_trades', 0)}"
        f" ({mc.get('crossing_pct', 0):.1f}%) |",
        f"| 月跨ぎavg PnL | {mc.get('crossing_avg_pnl', 0):,.0f} |",
        f"| 通常avg PnL | {mc.get('non_crossing_avg_pnl', 0):,.0f} |",
        f"| 月跨ぎWR | {mc.get('crossing_wr', 0):.1f}% |",
        f"| 通常WR | {mc.get('non_crossing_wr', 0):.1f}% |",
        f"| 月末2日エントリー数 | {mc.get('late_entry_trades', 0)} |",
        f"| 月末2日avg PnL | {mc.get('late_entry_avg_pnl', 0):,.0f} |",
        "",
    ])

    # #23
    ls = results.get("loss_streaks", {})
    lines.extend([
        "---", "## #23. 最大連敗ストリーク分析", "",
        "| 指標 | 値 |", "|------|------|",
        f"| 最大連敗 | **{ls.get('max_loss_streak', 0)}** 連敗 |",
        f"| 最大連勝 | {ls.get('max_win_streak', 0)} 連勝 |",
        f"| 平均連敗 | {ls.get('avg_loss_streak', 0):.1f} |",
        f"| 最悪連敗損失 | {ls.get('worst_streak_loss', 0):,.0f} |",
        f"| 観測WR | {ls.get('observed_wr', 0):.1f}% |",
        "",
    ])
    dist = ls.get("loss_streak_distribution", {})
    if dist:
        lines.extend([
            "### 連敗回数分布",
            "| N連敗以上 | 回数 |",
            "|----------|------|",
        ])
        for k, v in sorted(dist.items()):
            lines.append(f"| {k}+ | {v} |")
        lines.append("")
    theo = ls.get("theoretical_probability", {})
    if theo:
        lines.extend([
            "### 理論的連敗確率（WRベース）",
            "| N連敗 | 発生確率 |",
            "|------|---------|",
        ])
        for k, v in theo.items():
            n = k.split("_")[1]
            lines.append(f"| {n} | {v:.1f}% |")
        lines.append("")

    # #24
    ht = results.get("hold_time", {})
    lines.extend([
        "---", "## #24. 保有時間の非対称性", "",
        "| 指標 | 値 (分) |", "|------|---------|",
        f"| 全体平均 | {ht.get('avg_hold_all', 0):.0f} |",
        f"| 勝ち平均 | {ht.get('avg_hold_winners', 0):.0f} |",
        f"| 負け平均 | {ht.get('avg_hold_losers', 0):.0f} |",
        f"| 勝ち中央値 | {ht.get('median_hold_winners', 0):.0f} |",
        f"| 負け中央値 | {ht.get('median_hold_losers', 0):.0f} |",
        f"| 負け/勝ち比 | **{ht.get('hold_ratio', 0):.2f}x** |",
        "",
    ])
    reasons = [
        k for k in ht
        if k.startswith("avg_hold_")
        and k not in (
            "avg_hold_all",
            "avg_hold_winners",
            "avg_hold_losers",
        )
    ]
    if reasons:
        lines.extend([
            "### 決済理由別保有時間",
            "| 理由 | 平均(分) | 件数 |",
            "|------|---------|------|",
        ])
        for r in sorted(reasons):
            name = r.replace("avg_hold_", "")
            cnt = ht.get(f"count_{name}", "?")
            lines.append(f"| {name} | {ht[r]:.0f} | {cnt} |")
        lines.append("")

    # #26
    ed = results.get("edge_decay", {})
    lines.extend([
        "---", "## #26. エッジ経年劣化分析", "",
        f"PFトレンド: **{ed.get('pf_trend', '?')}**"
        f" (傾き: {ed.get('pf_trend_slope', 0)})",
        "",
    ])
    yearly = ed.get("yearly", {})
    if yearly:
        lines.extend([
            "| 年 | Trades | PF | WR | Net | AvgWin | AvgLoss |",
            "|---|--------|----|----|-----|--------|---------|",
        ])
        for y in sorted(yearly.keys()):
            d = yearly[y]
            lines.append(
                f"| {y} | {d['trades']} | {d['pf']} |"
                f" {d['wr']}% | {d['net']:,} |"
                f" {d['avg_win']:,} | {d['avg_loss']:,} |"
            )
        lines.append("")

    # #27
    ce = results.get("capital_efficiency", {})
    lines.extend([
        "---", "## #27. 資金効率（同時ポジション数）", "",
        f"時間加重平均ポジション数:"
        f" **{ce.get('avg_simultaneous_positions', 0)}**",
        "",
    ])
    pos_dist = ce.get("position_distribution_pct", {})
    if pos_dist:
        lines.extend([
            "| 同時ポジション数 | 時間割合 |",
            "|----------------|---------|",
        ])
        for n in sorted(pos_dist.keys(), key=int):
            lines.append(f"| {n} | {pos_dist[n]}% |")
        lines.append("")

    # A
    mcd = results.get("monte_carlo_dd", {})
    lines.extend([
        "---",
        "## A. モンテカルロDD分布 (10,000回シミュレーション)",
        "",
        "| 指標 | DD% |", "|------|-----|",
        f"| 観測DD | {mcd.get('observed_dd', 0)}% |",
        f"| MC平均 | {mcd.get('mc_mean_dd', 0)}% |",
        f"| MC中央値 | {mcd.get('mc_median_dd', 0)}% |",
        f"| MC P90 | {mcd.get('mc_p90_dd', 0)}% |",
        f"| MC P95 | {mcd.get('mc_p95_dd', 0)}% |",
        f"| MC **P99** | **{mcd.get('mc_p99_dd', 0)}%** |",
        f"| MC最大 | {mcd.get('mc_max_dd', 0)}% |",
        f"| 観測値より悪い確率 | {mcd.get('p_worse_than_observed', 0)}% |",
        "",
    ])

    # B
    kc = results.get("kelly", {})
    lines.extend([
        "---", "## B. Kelly Criterion", "",
        "| 指標 | 値 |", "|------|------|",
        f"| WR | {kc.get('win_rate', 0)}% |",
        f"| Payoff Ratio | {kc.get('payoff_ratio', 0)} |",
        f"| Full Kelly | {kc.get('kelly_full_pct', 0)}% |",
        f"| Half Kelly | {kc.get('kelly_half_pct', 0)}% |",
        f"| Quarter Kelly | {kc.get('kelly_quarter_pct', 0)}% |",
        f"| 現行リスク率 | {kc.get('current_risk_pct', 0)}% |",
        f"| 評価 | **{kc.get('current_vs_kelly', '?')}** |",
        "",
    ])

    # C
    ror = results.get("risk_of_ruin", {})
    lines.extend([
        "---", "## C. Risk of Ruin（DD到達確率）", "",
        f"エッジ/トレード: {ror.get('edge_per_trade', 0):,}円",
        "",
    ])
    dd_prob = ror.get("dd_reach_probability", {})
    if dd_prob:
        lines.extend([
            "| DDレベル | 到達確率 |",
            "|---------|---------|",
        ])
        for level, prob in dd_prob.items():
            lines.append(f"| {level} | {prob}% |")
        lines.append("")

    # D
    ac = results.get("autocorrelation", {})
    lines.extend([
        "---", "## D. リターン自己相関分析", "",
        f"独立性判定: **{ac.get('independence', '?')}**",
        "",
    ])
    acorrs = ac.get("autocorrelations", {})
    ci = ac.get("ci_95", 0)
    if acorrs:
        lines.append(f"95%信頼区間: ±{ci}")
        lines.extend([
            "| ラグ | 自己相関 | 有意 |",
            "|-----|---------|-----|",
        ])
        sig = ac.get("significant_lags", [])
        for lag, val in acorrs.items():
            s = "**YES**" if lag in sig else ""
            lines.append(f"| {lag} | {val} | {s} |")
        lines.append("")
    rt = ac.get("runs_test", {})
    if rt:
        lines.append(
            f"Runs Test: z={rt.get('z_score', 0)},"
            f" 結論=**{rt.get('conclusion', '?')}**"
        )
        lines.append("")

    return "\n".join(lines)


def main():
    """メイン実行"""
    parser = argparse.ArgumentParser(
        description="辛口コメント Round 4 統計分析",
    )
    parser.add_argument("csv_path", help="trades.csvのパス")
    parser.add_argument(
        "--output", default=None,
        help="出力先（省略時は stdout）",
    )
    args = parser.parse_args()

    print(f"読み込み中: {args.csv_path}", file=sys.stderr)
    df = load_trades(args.csv_path)
    print(f"トレード数: {len(df)}", file=sys.stderr)

    results: dict = {"total_trades": len(df)}

    analyses = [
        ("#21: 週末ギャップ", "weekend_risk", analyze_weekend_risk),
        ("#22: 月跨ぎ分析", "month_crossing", analyze_month_crossing),
        ("#23: 連敗ストリーク", "loss_streaks", analyze_loss_streaks),
        ("#24: 保有時間", "hold_time", analyze_hold_time),
        ("#26: エッジ劣化", "edge_decay", analyze_edge_decay),
        ("#27: 資金効率", "capital_efficiency", analyze_capital_efficiency),
        ("A: モンテカルロDD", "monte_carlo_dd", monte_carlo_dd),
        ("B: Kelly Criterion", "kelly", kelly_criterion),
        ("C: Risk of Ruin", "risk_of_ruin", risk_of_ruin),
        ("D: 自己相関分析", "autocorrelation", return_autocorrelation),
    ]

    for label, key, func in analyses:
        print(f"  {label}...", file=sys.stderr)
        results[key] = func(df)

    report = format_report(results, args.csv_path)

    if args.output:
        Path(args.output).write_text(
            report, encoding="utf-8",
        )
        print(
            f"レポート出力: {args.output}", file=sys.stderr,
        )
    else:
        print(report)


if __name__ == "__main__":
    main()
