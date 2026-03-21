"""スプレッドp95再計算スクリプト.

2023-2025年のM1データからスプレッドのp95を再計算し、
現行閾値との差分を分析する。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

# 対象8ペア
SYMBOLS = [
    "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
    "CADJPY", "CHFJPY", "EURUSD", "GBPUSD",
]

# 現行閾値（symbol_presets.yaml から）
CURRENT_THRESHOLDS: dict[str, float] = {
    "USDJPY": 3.0,
    "EURJPY": 4.5,
    "GBPJPY": 5.5,
    "AUDJPY": 4.5,
    "CADJPY": 5.5,
    "CHFJPY": 6.0,
    "EURUSD": 2.0,
    "GBPUSD": 3.0,
}

# データディレクトリ
DATA_DIR = Path("D:/Projects/AutoTraderV4_data/data")


def _find_m1_csv(symbol: str) -> Path:
    """M1 CSVファイルを検索."""
    csv_dir = DATA_DIR / symbol / "chart" / "csv"
    matches = list(csv_dir.glob(f"{symbol}_M1_*.csv"))
    if not matches:
        raise FileNotFoundError(f"{symbol} M1 CSV not found in {csv_dir}")
    return matches[0]


def _ceil_half_pip(value: float) -> float:
    """0.5pips刻みに切り上げ."""
    return math.ceil(value * 2) / 2


def analyze_spread(symbol: str) -> dict[str, any]:
    """1ペアのスプレッド分析を実行.

    Args:
        symbol: 通貨ペア名

    Returns:
        dict: 期間別の統計情報
    """
    csv_path = _find_m1_csv(symbol)

    # TSV形式で読み込み（DATE, TIME, OHLC, TICKVOL, VOL, SPREAD）
    df = pd.read_csv(
        csv_path,
        sep="\t",
        usecols=["<DATE>", "<SPREAD>"],
        parse_dates={"datetime": ["<DATE>"]},
    )

    # spread_points → spread_pips
    df["spread_pips"] = df["<SPREAD>"] / 10.0

    # 年カラム追加
    df["year"] = df["datetime"].dt.year

    results: dict[str, any] = {"symbol": symbol}

    # 期間定義
    periods = {
        "2023-2024": (2023, 2024),
        "2025": (2025, 2025),
        "2023-2025": (2023, 2025),
    }

    for period_name, (y_start, y_end) in periods.items():
        mask = (df["year"] >= y_start) & (df["year"] <= y_end)
        subset = df.loc[mask, "spread_pips"]

        if subset.empty:
            results[period_name] = None
            continue

        stats = {
            "count": len(subset),
            "mean": round(subset.mean(), 2),
            "median": round(subset.median(), 2),
            "p75": round(subset.quantile(0.75), 2),
            "p90": round(subset.quantile(0.90), 2),
            "p95": round(subset.quantile(0.95), 2),
            "p99": round(subset.quantile(0.99), 2),
            "max": round(subset.max(), 2),
        }
        results[period_name] = stats

    return results


def generate_report(
    all_results: list[dict[str, any]],
) -> str:
    """Markdownレポートを生成.

    Args:
        all_results: 全ペアの分析結果

    Returns:
        str: Markdownテキスト
    """
    lines: list[str] = []
    lines.append("# スプレッドp95再計算レポート")
    lines.append("")
    lines.append("## 概要")
    lines.append("")
    lines.append(
        "M1データのSPREAD列（points）をpipsに変換し、"
        "期間別にパーセンタイルを計算。"
    )
    lines.append(
        "採用値: `ceil(p95 * 2) / 2`（0.5pips刻みに切り上げ）"
    )
    lines.append("")

    # サマリーテーブル
    lines.append("## 閾値比較サマリー")
    lines.append("")
    lines.append(
        "| ペア | 現行閾値 | p95(23-24) | p95(2025) "
        "| p95(23-25) | 新閾値候補 | 差分 | 更新 |"
    )
    lines.append(
        "|------|----------|------------|-----------|"
        "------------|------------|------|------|"
    )

    updates: list[dict[str, any]] = []

    for r in all_results:
        symbol = r["symbol"]
        current = CURRENT_THRESHOLDS[symbol]

        p95_old = r["2023-2024"]["p95"] if r.get("2023-2024") else "-"
        p95_new_only = r["2025"]["p95"] if r.get("2025") else "-"
        p95_combined = (
            r["2023-2025"]["p95"] if r.get("2023-2025") else "-"
        )

        if isinstance(p95_combined, (int, float)):
            new_threshold = _ceil_half_pip(p95_combined)
            diff = new_threshold - current
            should_update = abs(diff) >= 0.5
            diff_str = f"{diff:+.1f}"
            update_str = "YES" if should_update else "no"
            if should_update:
                updates.append({
                    "symbol": symbol,
                    "current": current,
                    "new": new_threshold,
                    "diff": diff,
                    "p95": p95_combined,
                })
        else:
            new_threshold = "-"
            diff_str = "-"
            update_str = "-"

        lines.append(
            f"| {symbol} | {current} | {p95_old} | {p95_new_only} "
            f"| {p95_combined} | {new_threshold} | {diff_str} | {update_str} |"
        )

    lines.append("")

    # 詳細テーブル
    lines.append("## 期間別詳細統計")
    lines.append("")

    for r in all_results:
        symbol = r["symbol"]
        lines.append(f"### {symbol}")
        lines.append("")
        lines.append(
            "| 期間 | count | mean | median | p75 "
            "| p90 | p95 | p99 | max |"
        )
        lines.append(
            "|------|-------|------|--------|-----"
            "|-----|-----|-----|-----|"
        )

        for period in ["2023-2024", "2025", "2023-2025"]:
            stats = r.get(period)
            if stats is None:
                lines.append(f"| {period} | - | - | - | - | - | - | - | - |")
                continue
            lines.append(
                f"| {period} | {stats['count']:,} "
                f"| {stats['mean']} | {stats['median']} "
                f"| {stats['p75']} | {stats['p90']} "
                f"| {stats['p95']} | {stats['p99']} "
                f"| {stats['max']} |"
            )

        lines.append("")

    # 更新推奨
    lines.append("## 更新推奨")
    lines.append("")
    if updates:
        for u in updates:
            direction = "引き上げ" if u["diff"] > 0 else "引き下げ"
            lines.append(
                f"- **{u['symbol']}**: {u['current']} → {u['new']} "
                f"({direction} {abs(u['diff']):.1f}pips, "
                f"p95={u['p95']})"
            )
    else:
        lines.append(
            "全ペアの差分が0.5pips未満のため、更新不要。"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """メイン実行."""
    all_results: list[dict[str, any]] = []

    for symbol in SYMBOLS:
        sys.stdout.write(f"  {symbol} ... ")
        sys.stdout.flush()
        result = analyze_spread(symbol)
        all_results.append(result)

        # 即時サマリー表示
        p95 = result["2023-2025"]["p95"]
        current = CURRENT_THRESHOLDS[symbol]
        new_th = _ceil_half_pip(p95)
        diff = new_th - current
        mark = " *" if abs(diff) >= 0.5 else ""
        sys.stdout.write(
            f"p95={p95:.2f} → {new_th:.1f} "
            f"(現行{current:.1f}, diff={diff:+.1f}){mark}\n"
        )

    # レポート生成
    report = generate_report(all_results)

    report_dir = Path(__file__).resolve().parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "spread_p95_analysis.md"
    report_path.write_text(report, encoding="utf-8")

    sys.stdout.write(f"\nレポート出力: {report_path}\n")


if __name__ == "__main__":
    main()
