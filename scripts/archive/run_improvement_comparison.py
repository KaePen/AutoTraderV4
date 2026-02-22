#!/usr/bin/env python3
"""改善比較バックテストスクリプト

9パターンの改善組み合わせを自動実行し、結果を比較テーブルで出力する。

使用例:
    uv run python scripts/run_improvement_comparison.py
    uv run python scripts/run_improvement_comparison.py --years 2020-2024
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

# プロジェクトルートをパスに追加
try:
    project_root = Path(__file__).parent.parent
except NameError:
    project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(project_root ))


@dataclass
class TestPattern:
    """テストパターン定義

    Attributes:
        name: パターン名
    """

    name: str


# 比較パターン定義（現在は単一設定のみ）
PATTERNS = [
    TestPattern("デフォルト"),
]


def parse_years(years_str: str) -> tuple[int, int]:
    """年範囲をパース

    Args:
        years_str: 年範囲文字列

    Returns:
        tuple[int, int]: (開始年, 終了年)
    """
    if "-" in years_str:
        parts = years_str.split("-")
        return int(parts[0]), int(parts[1])
    year = int(years_str)
    return year, year


def run_pattern(
    pattern: TestPattern,
    start_year: int,
    end_year: int,
    data_dir: str = "data/csv",
) -> dict[str, any]:
    """1パターンのバックテスト実行

    Args:
        pattern: テストパターン
        start_year: 開始年
        end_year: 終了年
        data_dir: データディレクトリ

    Returns:
        dict: 結果辞書
    """
    from autotrader.backtest.service import (
        BacktestService,
        BacktestServiceConfig,
        create_bot_config,
    )

    config = BacktestServiceConfig(
        start_year=start_year,
        end_year=end_year,
        initial_balance=1_000_000.0,
        volume=1.0,
        data_dir=data_dir,
        verbose=False,
        use_short_timeframe=True,
    )

    service = BacktestService(config)
    runner = service.create_runner()
    runner.load_data()

    bot_config = create_bot_config()

    result = runner.run_unified(
        start_year,
        end_year,
        bot_config,
        use_m1=True,
    )

    return {
        "name": pattern.name,
        "trades": result.trades,
        "win_rate": result.win_rate,
        "pf": result.profit_factor,
        "net_profit": result.net_profit,
        "max_dd": result.max_drawdown,
        "sharpe": result.sharpe_ratio,
        "annual_return": result.annual_return,
        "yearly": result.yearly_results,
        "monthly": result.monthly_results,
    }


def print_comparison(results: list[dict]) -> str:
    """比較テーブルを出力

    Args:
        results: 結果リスト

    Returns:
        str: マークダウンテーブル文字列
    """
    lines = []
    lines.append("# 改善比較結果\n")
    lines.append(
        "| # | 改善 | 取引数 | 勝率 | PF | 純利益 "
        "| DD | Sharpe | 年収益 |"
    )
    lines.append(
        "|---|------|--------|------|-----|--------"
        "|-----|--------|--------|"
    )

    base = results[0] if results else None
    for i, r in enumerate(results):
        # ベースラインとの差分表示
        pf_diff = ""
        wr_diff = ""
        if base and i > 0:
            pf_delta = r["pf"] - base["pf"]
            wr_delta = r["win_rate"] - base["win_rate"]
            pf_diff = f" ({pf_delta:+.2f})"
            wr_diff = f" ({wr_delta:+.1f})"

        line = (
            f"| {i} | {r['name']} "
            f"| {r['trades']} "
            f"| {r['win_rate']:.1f}%{wr_diff} "
            f"| {r['pf']:.2f}{pf_diff} "
            f"| {r['net_profit']:+,.0f} "
            f"| {r['max_dd']:.2f}% "
            f"| {r['sharpe']:.2f} "
            f"| {r['annual_return']:.1f}% |"
        )
        lines.append(line)

    # 年別詳細
    lines.append("\n## 年別詳細\n")
    for r in results:
        lines.append(f"### {r['name']}\n")
        lines.append(
            "| 年 | 取引 | 勝率 | PF | 利益 | DD |"
        )
        lines.append(
            "|----|------|------|-----|-------|-----|"
        )
        for yr in r.get("yearly", []):
            lines.append(
                f"| {yr['year']} "
                f"| {yr['trades']} "
                f"| {yr['win_rate']:.1f}% "
                f"| {yr['profit_factor']:.2f} "
                f"| {yr['net_profit']:+,.0f} "
                f"| {yr['max_drawdown']:.2f}% |"
            )
        lines.append("")

    # 月間プラス率
    lines.append("\n## 月間プラス率\n")
    lines.append("| 改善 | プラス月 | 全月 | 率 |")
    lines.append("|------|----------|------|-----|")
    for r in results:
        monthly = r.get("monthly", [])
        if monthly:
            positive = sum(
                1 for m in monthly if m["return_pct"] > 0
            )
            total = len(monthly)
            rate = positive / total * 100 if total else 0
            lines.append(
                f"| {r['name']} | {positive} "
                f"| {total} | {rate:.1f}% |"
            )

    return "\n".join(lines)


def main():
    """メイン関数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="改善比較バックテスト",
    )
    parser.add_argument(
        "--years",
        default="2020-2024",
        help="バックテスト期間",
    )
    parser.add_argument(
        "--data-dir",
        default="data/csv",
        help="データディレクトリ",
    )
    args = parser.parse_args()

    start_year, end_year = parse_years(args.years)

    print("=" * 60)
    print("改善比較バックテスト")
    print(f"期間: {start_year}-{end_year}")
    print(f"パターン数: {len(PATTERNS)}")
    print("=" * 60)

    results = []
    total_start = time.time()

    for i, pattern in enumerate(PATTERNS):
        print(f"\n[{i + 1}/{len(PATTERNS)}] {pattern.name}...")
        t0 = time.time()
        result = run_pattern(
            pattern, start_year, end_year, args.data_dir,
        )
        elapsed = time.time() - t0
        print(
            f"  完了: {elapsed:.1f}秒 "
            f"取引={result['trades']} "
            f"PF={result['pf']:.2f} "
            f"勝率={result['win_rate']:.1f}%"
        )
        results.append(result)

    total_elapsed = time.time() - total_start
    print(f"\n合計: {total_elapsed:.1f}秒")

    # 結果出力
    report = print_comparison(results)
    print("\n" + report)

    # ファイル出力
    report_dir = project_root / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "improvement_comparison.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nレポート出力: {report_path}")


if __name__ == "__main__":
    main()
