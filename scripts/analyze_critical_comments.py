"""辛口コメント検証 - BT結果分析スクリプト

全ジョブ完了後に実行し、辛口コメント各項目への定量的回答を生成する。
Usage: uv run python scripts/analyze_critical_comments.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from autotrader.config.paths import get_results_dir

RESULTS_DIR = get_results_dir()

# 既知のベースライン（MEMORY.mdより、PR #707時点）
BASELINE = {
    "IS_2023_2025": {
        "pf": 3.08, "sharpe": 6.39, "dd": 2.15,
        "wr": 84.6, "monthly_plus": 94.4, "net": 3_480_000,
    },
    "OOS_2020_2022": {
        "pf": 3.15, "sharpe": 8.61, "dd": 1.74,
        "wr": 84.3, "monthly_plus": 100.0, "net": 2_650_000,
    },
}


def load_result(result_id: str) -> dict | None:
    """result.jsonを読み込む。"""
    path = RESULTS_DIR / result_id / "result.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_results_by_desc(keyword: str) -> list[tuple[str, dict]]:
    """descriptionにキーワードを含む結果を検索。"""
    found = []
    for d in sorted(RESULTS_DIR.iterdir()):
        r = load_result(d.name)
        if r and keyword.lower() in r.get("description", "").lower():
            found.append((d.name, r))
    return found


def fmt_metric(r: dict) -> str:
    """結果を1行フォーマット。"""
    return (
        f"PF={r.get('profit_factor', 0):.2f}  "
        f"Sharpe={r.get('sharpe_ratio', 0):.2f}  "
        f"DD={r.get('max_drawdown', 0):.2f}%  "
        f"WR={r.get('win_rate', 0):.1f}%  "
        f"M+={r.get('monthly_plus_rate', 0):.0f}%  "
        f"Net={r.get('net_profit', 0):,.0f}  "
        f"Trades={r.get('trades', 0)}"
    )


def print_comparison(label: str, results: list[tuple[str, dict]]) -> None:
    """複数結果を比較表示。"""
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")
    for rid, r in results:
        desc = r.get("description", "?")
        print(f"\n  [{rid}] {desc}")
        print(f"    {fmt_metric(r)}")


def degradation(base: dict, test: dict, key: str) -> float:
    """ベースラインからの劣化率(%)を計算。"""
    bv = base.get(key, 0)
    tv = test.get(key, 0)
    if not bv:
        return 0.0
    return (tv - bv) / abs(bv) * 100


def analyze_deep_oos() -> None:
    """#1 カーブフィッティング検証: Deep OOS結果分析。"""
    print("\n" + "#" * 80)
    print("# Comment #1: Curve Fitting Risk - Deep OOS Validation")
    print("#" * 80)

    doos1 = find_results_by_desc("Deep OOS 2015-2019")
    doos2 = find_results_by_desc("Extreme OOS 2010-2014")

    if not doos1 and not doos2:
        print("\n  [PENDING] Deep OOS results not yet available")
        return

    print("\n  Baseline (from MEMORY.md):")
    for period, bl in BASELINE.items():
        print(
            f"    {period}: PF={bl['pf']:.2f}  "
            f"Sharpe={bl['sharpe']:.2f}  "
            f"DD={bl['dd']:.2f}%  "
            f"WR={bl['wr']:.1f}%"
        )

    if doos1:
        print_comparison("Deep OOS 2015-2019", doos1)
        _, r = doos1[0]
        pf_deg = degradation(
            {"v": BASELINE["IS_2023_2025"]["pf"]},
            {"v": r.get("profit_factor", 0)},
            "v",
        )
        print(f"\n  PF degradation vs IS: {pf_deg:+.1f}%")
        if r.get("profit_factor", 0) > 1.5:
            print("  -> PASS: PF > 1.5 on untouched data")
        else:
            print("  -> WARNING: PF < 1.5 suggests overfitting")

    if doos2:
        print_comparison("Extreme OOS 2010-2014", doos2)
        _, r = doos2[0]
        if r.get("profit_factor", 0) > 1.0:
            print("  -> PASS: Profitable on extreme OOS")
        else:
            print("  -> FAIL: Not profitable on extreme OOS")


def analyze_spread_stress() -> None:
    """#5 スプレッドストレステスト。"""
    print("\n" + "#" * 80)
    print("# Comment #5/#7: Spread Vulnerability - Stress Tests")
    print("#" * 80)

    sp15 = find_results_by_desc("spread stress x1.5")
    sp20 = find_results_by_desc("spread stress x2.0")
    sp30 = find_results_by_desc("spread stress x3.0")

    all_sp = sp15 + sp20 + sp30
    if not all_sp:
        print("\n  [PENDING] Spread stress results not yet available")
        return

    print("\n  Baseline IS PF: 3.08")
    print(f"\n  {'Multiplier':<12} {'PF':>6} {'DD':>8} {'WR':>8} "
          f"{'Trades':>8} {'Net':>12} {'PF drop':>8}")
    print(f"  {'-'*66}")

    base_pf = BASELINE["IS_2023_2025"]["pf"]
    print(
        f"  {'x1.0':<12} {base_pf:>6.2f} "
        f"{BASELINE['IS_2023_2025']['dd']:>7.2f}% "
        f"{BASELINE['IS_2023_2025']['wr']:>7.1f}% "
        f"{'~945':>8} "
        f"{BASELINE['IS_2023_2025']['net']:>12,} "
        f"{'baseline':>8}"
    )

    for label, results in [
        ("x1.5", sp15),
        ("x2.0", sp20),
        ("x3.0", sp30),
    ]:
        if results:
            _, r = results[0]
            pf = r.get("profit_factor", 0)
            drop = (pf - base_pf) / base_pf * 100
            print(
                f"  {label:<12} {pf:>6.2f} "
                f"{r.get('max_drawdown', 0):>7.2f}% "
                f"{r.get('win_rate', 0):>7.1f}% "
                f"{r.get('trades', 0):>8} "
                f"{r.get('net_profit', 0):>12,.0f} "
                f"{drop:>+7.1f}%"
            )

    # 損益分岐点推定
    pf_values = []
    multipliers = []
    for mult, results in [(1.0, [(None, BASELINE["IS_2023_2025"])]),
                          (1.5, sp15), (2.0, sp20), (3.0, sp30)]:
        if results:
            _, r = results[0]
            pf_val = r.get("pf", r.get("profit_factor", 0))
            if pf_val:
                pf_values.append(pf_val)
                multipliers.append(mult)

    if len(pf_values) >= 2:
        # 線形補間でPF=1.0の損益分岐点を推定
        for i in range(len(pf_values) - 1):
            if pf_values[i] >= 1.0 >= pf_values[i + 1]:
                frac = (pf_values[i] - 1.0) / (
                    pf_values[i] - pf_values[i + 1]
                )
                breakeven = (
                    multipliers[i]
                    + frac * (multipliers[i + 1] - multipliers[i])
                )
                print(
                    f"\n  Break-even spread multiplier: ~{breakeven:.1f}x"
                )
                break
        else:
            if pf_values[-1] > 1.0:
                print(
                    "\n  Break-even: > {:.1f}x "
                    "(still profitable at max tested)".format(
                        multipliers[-1]
                    )
                )


def analyze_correlation() -> None:
    """#6 相関リスク分析。"""
    print("\n" + "#" * 80)
    print("# Comment #6: Correlation Risk - Tight Position Limits")
    print("#" * 80)

    corr_is = find_results_by_desc("tight positions (max4)")
    corr_oos = find_results_by_desc("tight positions OOS")

    if not corr_is and not corr_oos:
        print("\n  [PENDING] Correlation risk results not yet available")
        return

    print("\n  Comparison: global_max_positions=8 vs 4")
    print(
        f"\n  {'Config':<30} {'PF':>6} {'DD':>8} {'WR':>8} "
        f"{'Trades':>8} {'Net':>12}"
    )
    print(f"  {'-'*74}")

    print(
        f"  {'Baseline max=8 IS':<30} "
        f"{BASELINE['IS_2023_2025']['pf']:>6.2f} "
        f"{BASELINE['IS_2023_2025']['dd']:>7.2f}% "
        f"{BASELINE['IS_2023_2025']['wr']:>7.1f}% "
        f"{'~945':>8} "
        f"{BASELINE['IS_2023_2025']['net']:>12,}"
    )

    for label, results in [
        ("Tight max=4 IS", corr_is),
        ("Tight max=4 OOS", corr_oos),
    ]:
        if results:
            _, r = results[0]
            print(
                f"  {label:<30} "
                f"{r.get('profit_factor', 0):>6.2f} "
                f"{r.get('max_drawdown', 0):>7.2f}% "
                f"{r.get('win_rate', 0):>7.1f}% "
                f"{r.get('trades', 0):>8} "
                f"{r.get('net_profit', 0):>12,.0f}"
            )

    if corr_is:
        _, r = corr_is[0]
        dd_base = BASELINE["IS_2023_2025"]["dd"]
        dd_tight = r.get("max_drawdown", 0)
        net_base = BASELINE["IS_2023_2025"]["net"]
        net_tight = r.get("net_profit", 0)
        print(f"\n  DD change: {dd_base:.2f}% -> {dd_tight:.2f}% "
              f"({dd_tight - dd_base:+.2f}%)")
        if net_base:
            print(
                f"  Net change: {net_base:,} -> {net_tight:,.0f} "
                f"({(net_tight - net_base) / net_base * 100:+.1f}%)"
            )


def analyze_w1() -> None:
    """#4 W1採用判断。"""
    print("\n" + "#" * 80)
    print("# W1 (SL Exit Spread) Adoption Decision")
    print("#" * 80)

    w1_8pair = find_results_by_desc("8pair W1 ON")
    if not w1_8pair:
        print("\n  [PENDING] 8pair W1 results not yet available")
        return

    print_comparison("8pair W1 ON vs Baseline", w1_8pair)
    _, r = w1_8pair[0]
    pf_deg = (
        (r.get("profit_factor", 0) - BASELINE["IS_2023_2025"]["pf"])
        / BASELINE["IS_2023_2025"]["pf"]
        * 100
    )
    print(f"\n  PF degradation: {pf_deg:+.1f}%")
    print(f"  W2 status: DEAD CODE (spread/TP never reaches 15%)")

    if abs(pf_deg) < 15:
        print("  -> RECOMMEND: Adopt W1 (< 15% PF impact, "
              "improves BT fairness)")
    else:
        print("  -> WARNING: W1 impact exceeds 15%, "
              "reconsider sl_exit_spread_factor")


def statistical_summary() -> None:
    """#4 トレード数統計分析。"""
    print("\n" + "#" * 80)
    print("# Comment #4: Trade Count Statistical Reliability")
    print("#" * 80)

    # 全結果からトレード数を集計
    total_trades = 0
    total_years = 0
    for d in sorted(RESULTS_DIR.iterdir()):
        r = load_result(d.name)
        if r and "multi_pair" in r.get("job_type", ""):
            total_trades += r.get("trades", 0)
            years = r.get("years", "")
            if "-" in str(years):
                parts = str(years).split("-")
                total_years += int(parts[1]) - int(parts[0]) + 1

    wr = 0.846
    z95 = 1.96

    print(f"\n  Total multi-pair trades available: {total_trades}")
    print(f"  Total years of data: {total_years}")
    print()

    for n, label in [
        (945, "Current 3yr IS"),
        (total_trades, f"All available ({total_trades})"),
    ]:
        if n <= 0:
            continue
        se = math.sqrt(wr * (1 - wr) / n)
        lo = (wr - z95 * se) * 100
        hi = (wr + z95 * se) * 100
        print(f"  {label}: 95% CI = {lo:.1f}% - {hi:.1f}% "
              f"(width {hi - lo:.1f}%)")


def main() -> None:
    """メイン実行。"""
    print("=" * 80)
    print("  AutoTraderV4 Critical Comments Analysis Report")
    print("  Generated from backtest results")
    print("=" * 80)

    analyze_deep_oos()
    analyze_spread_stress()
    analyze_correlation()
    analyze_w1()
    statistical_summary()

    print("\n" + "=" * 80)
    print("  END OF REPORT")
    print("=" * 80)


if __name__ == "__main__":
    main()
