"""品質ベース動的ポジション枠 比較バックテスト

通常時のポジション数と、高品質シグナル時のみ追加枠を解放する戦略を比較する。

比較シナリオ:
  A. max=1, bonus=0     : ベースライン（現状）
  B. max=2, bonus=0     : 単純2ポジション
  C. max=1, bonus=1, t=6.5 : 通常1、score>=6.5で2枠
  D. max=1, bonus=1, t=7.0 : 通常1、score>=7.0で2枠
  E. max=1, bonus=1, t=7.5 : 通常1、score>=7.5で2枠
  F. max=2, bonus=1, t=7.0 : 通常2、score>=7.0で3枠

使い方:
    python reports/quality_based_positions_comparison.py
    python reports/quality_based_positions_comparison.py --years 2023
    python reports/quality_based_positions_comparison.py --years 2022-2024
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autotrader.backtest.runner import (
    BacktestConfig,
    BacktestResult,
    BacktestRunner,
)
from autotrader.decision.unified import UnifiedBotConfig
from autotrader.decision.unified.position_manager import PositionManagerConfig

# ===== デフォルト設定 =====
# スクリプトの場所から相対的にプロジェクトルートを特定
DATA_DIR = str(Path(__file__).parent.parent / "data")
START_YEAR = 2022
END_YEAR = 2024
INITIAL_BALANCE = 1_000_000.0
SYMBOL = "USDJPY"


@dataclass
class Scenario:
    """比較シナリオ定義"""

    label: str
    max_positions: int
    bonus_max_positions: int
    bonus_score_threshold: float


# 比較シナリオ
SCENARIOS: list[Scenario] = [
    Scenario("A: 基本(max=1)", 1, 0, 7.0),
    Scenario("B: 単純2枠(max=2)", 2, 0, 7.0),
    Scenario("C: 品質>=6.5で+1枠", 1, 1, 6.5),
    Scenario("D: 品質>=7.0で+1枠", 1, 1, 7.0),
    Scenario("E: 品質>=7.5で+1枠", 1, 1, 7.5),
    Scenario("F: 基本2+品質>=7.0で+1枠", 2, 1, 7.0),
]


@dataclass
class CompareRow:
    """比較結果1行"""

    scenario: Scenario
    trades: int
    win_rate: float
    non_loss_rate: float
    profit_factor: float
    net_profit: float
    max_drawdown: float
    sharpe_ratio: float
    annual_return: float
    elapsed_sec: float


def run_one(
    scenario: Scenario, start_year: int, end_year: int
) -> CompareRow:
    """1シナリオでバックテストを実行して結果を返す

    Args:
        scenario: 実行するシナリオ
        start_year: 開始年
        end_year: 終了年

    Returns:
        CompareRow: 比較結果
    """
    print(f"\n{'='*65}")
    print(f"実行中: {scenario.label}")
    print(f"  max_positions={scenario.max_positions}, "
          f"bonus={scenario.bonus_max_positions}, "
          f"threshold={scenario.bonus_score_threshold}")
    print(f"{'='*65}")

    backtest_config = BacktestConfig(
        symbol=SYMBOL,
        initial_balance=INITIAL_BALANCE,
        max_positions=scenario.max_positions,
        bonus_max_positions=scenario.bonus_max_positions,
        bonus_score_threshold=scenario.bonus_score_threshold,
    )
    runner = BacktestRunner(
        data_dir=DATA_DIR,
        config=backtest_config,
        verbose=False,
    )

    # max_positionsに応じて合計ロット上限を比例拡張
    effective_max = scenario.max_positions + scenario.bonus_max_positions
    max_total_exposure = max(5.0, float(effective_max) * 2.0)

    bot_config = UnifiedBotConfig(
        use_dynamic_lot=True,
        max_lot_per_trade=2.0,
        max_total_exposure_lot=max_total_exposure,
        base_risk_pct=0.02,
        max_risk_pct_absolute=0.03,
        equity_floor_pct=0.30,
        equity_caution_pct=0.50,
        consensus_threshold=5.5,
    )

    pm_config = PositionManagerConfig(
        stagnation_exit_minutes=120.0,
        stagnation_min_mfe_r=0.15,
        range_day_be_disabled=True,
        range_day_early_be_r=0.3,
        range_day_fast_be_enabled=True,
        range_day_fast_be_minutes=90.0,
        range_day_insurance_enabled=True,
        range_day_insurance_max_minutes=30.0,
        range_day_insurance_sl_offset_r=-0.1,
        range_day_insurance_partial_ratio=0.20,
        insurance_trigger_r=1.0,
        insurance_block_high_mfe_r=0.8,
        insurance_min_holding_minutes=15.0,
        range_day_half_r_partial_enabled=True,
        disable_tp_after_partial=True,
    )

    t0 = time.time()
    runner.load_data()
    result: BacktestResult = runner.run_unified(
        start_year=start_year,
        end_year=end_year,
        config=bot_config,
        use_m1=True,
        pm_config=pm_config,
    )
    elapsed = time.time() - t0

    nlr = result.non_loss_rate if hasattr(result, "non_loss_rate") else 0.0

    print(f"  トレード数: {result.trades}")
    print(f"  勝率:       {result.win_rate:.1f}%")
    print(f"  非敗率:     {nlr:.1f}%")
    print(f"  PF:         {result.profit_factor:.3f}")
    print(f"  純利益:     {result.net_profit:,.0f}円")
    print(f"  最大DD:     {result.max_drawdown:.2f}%")
    print(f"  シャープ:   {result.sharpe_ratio:.3f}")
    print(f"  年間収益率: {result.annual_return:.2f}%")
    print(f"  実行時間:   {elapsed:.1f}秒")

    if result.yearly_results:
        print("  [年別]")
        for yr in result.yearly_results:
            yr_nlr = yr.get("non_loss_rate", 0.0)
            profit_sign = "+" if yr["net_profit"] >= 0 else ""
            print(
                f"    {yr['year']}: "
                f"取引={yr['trades']} "
                f"勝率={yr['win_rate']:.1f}% "
                f"非敗={yr_nlr:.1f}% "
                f"PF={yr['profit_factor']:.2f} "
                f"損益={profit_sign}{yr['net_profit']:,.0f}円 "
                f"DD={yr['max_drawdown']:.2f}%"
            )

    return CompareRow(
        scenario=scenario,
        trades=result.trades,
        win_rate=result.win_rate,
        non_loss_rate=nlr,
        profit_factor=result.profit_factor,
        net_profit=result.net_profit,
        max_drawdown=result.max_drawdown,
        sharpe_ratio=result.sharpe_ratio,
        annual_return=result.annual_return,
        elapsed_sec=elapsed,
    )


def _verdict(d_pct: float, d_dd: float) -> str:
    """評価判定を返す

    Args:
        d_pct: 利益変化率（%）
        d_dd: DD変化（%pt）

    Returns:
        str: 評価文字列
    """
    if d_pct > 10 and d_dd < 5:
        return "[OK] 推奨: DD増加軽微で大きな利益向上"
    if d_pct > 5 and d_dd < 8:
        return "[?]  検討可: 利益改善あるがDD増加に注意"
    if d_pct > 0 and d_dd < 12:
        return "[?]  要検討: 利益改善小さくDD増加大"
    if d_pct <= 0:
        return "[NG] 非推奨: 利益改善なし"
    return "[NG] 非推奨: DD増加が許容範囲外"


def print_comparison_table(
    rows: list[CompareRow], start_year: int, end_year: int
) -> None:
    """比較テーブルを出力

    Args:
        rows: 比較結果リスト
        start_year: 開始年
        end_year: 終了年
    """
    print(f"\n{'='*100}")
    print("品質ベース動的ポジション枠 比較結果")
    print(f"対象期間: {start_year}〜{end_year}年 / 通貨: {SYMBOL}")
    print(f"{'='*100}")

    header = (
        f"{'シナリオ':<28} {'取引数':>6} {'勝率%':>6} {'非敗%':>6} "
        f"{'PF':>6} {'純利益':>12} {'MaxDD%':>7} {'シャープ':>8} {'年利%':>7}"
    )
    print(header)
    print("-" * 100)

    baseline = rows[0] if rows else None
    for row in rows:
        print(
            f"{row.scenario.label:<28} "
            f"{row.trades:>6} "
            f"{row.win_rate:>5.1f}% "
            f"{row.non_loss_rate:>5.1f}% "
            f"{row.profit_factor:>6.3f} "
            f"{row.net_profit:>12,.0f} "
            f"{row.max_drawdown:>6.2f}% "
            f"{row.sharpe_ratio:>8.3f} "
            f"{row.annual_return:>6.2f}%"
        )

    print("=" * 100)

    if len(rows) >= 2 and baseline:
        print("\n[ベースライン(A)比の評価]")
        for row in rows[1:]:
            d_trades = row.trades - baseline.trades
            d_profit = row.net_profit - baseline.net_profit
            d_pct = (
                d_profit / abs(baseline.net_profit) * 100
                if baseline.net_profit != 0
                else float("inf")
            )
            d_dd = row.max_drawdown - baseline.max_drawdown
            d_wr = row.win_rate - baseline.win_rate
            verdict = _verdict(d_pct, d_dd)
            print(
                f"  {row.scenario.label}: "
                f"取引{d_trades:+d}件 "
                f"利益{d_profit:+,.0f}円({d_pct:+.1f}%) "
                f"DD{d_dd:+.2f}%pt "
                f"勝率{d_wr:+.1f}%pt"
            )
            print(f"    → {verdict}")


def save_report(
    rows: list[CompareRow], start_year: int, end_year: int
) -> None:
    """レポートファイルに保存

    Args:
        rows: 比較結果リスト
        start_year: 開始年
        end_year: 終了年
    """
    from datetime import date

    today = date.today().strftime("%Y%m%d")
    out_path = (
        Path(__file__).parent.parent
        / "reports"
        / f"quality_based_positions_{today}.md"
    )

    lines = [
        "# 品質ベース動的ポジション枠 比較レポート",
        "",
        f"- 対象期間: {start_year}〜{end_year}年",
        f"- 通貨ペア: {SYMBOL}",
        f"- 初期残高: {INITIAL_BALANCE:,.0f}円",
        "",
        "## シナリオ定義",
        "",
        "| シナリオ | max_positions | bonus枠 | score閾値 |",
        "|---------|--------------|---------|----------|",
    ]
    for row in rows:
        s = row.scenario
        threshold_str = (
            f"{s.bonus_score_threshold}"
            if s.bonus_max_positions > 0
            else "N/A"
        )
        lines.append(
            f"| {s.label} | {s.max_positions} "
            f"| {s.bonus_max_positions} | {threshold_str} |"
        )

    lines += [
        "",
        "## 比較結果",
        "",
        "| シナリオ | 取引数 | 勝率% | 非敗% | PF | 純利益 | MaxDD% "
        "| シャープ | 年利% |",
        "|---------|--------|-------|-------|-----|--------|--------|"
        "----------|-------|",
    ]

    for row in rows:
        lines.append(
            f"| {row.scenario.label} | {row.trades} "
            f"| {row.win_rate:.1f} | {row.non_loss_rate:.1f} "
            f"| {row.profit_factor:.3f} | {row.net_profit:,.0f} "
            f"| {row.max_drawdown:.2f} | {row.sharpe_ratio:.3f} "
            f"| {row.annual_return:.2f} |"
        )

    if len(rows) >= 2:
        baseline = rows[0]
        lines += ["", "## ベースライン(A)比の評価"]
        for row in rows[1:]:
            d_trades = row.trades - baseline.trades
            d_profit = row.net_profit - baseline.net_profit
            d_pct = (
                d_profit / abs(baseline.net_profit) * 100
                if baseline.net_profit != 0
                else float("inf")
            )
            d_dd = row.max_drawdown - baseline.max_drawdown
            verdict = _verdict(d_pct, d_dd)
            lines.append(
                f"- {row.scenario.label}: "
                f"取引{d_trades:+d}件, "
                f"利益{d_profit:+,.0f}円({d_pct:+.1f}%), "
                f"DD{d_dd:+.2f}%pt → {verdict}"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nレポートを保存しました: {out_path}")


def parse_args() -> argparse.Namespace:
    """引数をパース"""
    parser = argparse.ArgumentParser(
        description="品質ベース動的ポジション枠 比較バックテスト"
    )
    parser.add_argument(
        "--years",
        type=str,
        default="2022-2024",
        help="バックテスト期間（例: 2022-2024 または 2023）",
    )
    return parser.parse_args()


def parse_years(years_str: str) -> tuple[int, int]:
    """年範囲をパース"""
    if "-" in years_str:
        parts = years_str.split("-")
        return int(parts[0]), int(parts[1])
    year = int(years_str)
    return year, year


def main() -> None:
    """メイン処理"""
    args = parse_args()
    start_year, end_year = parse_years(args.years)

    print("品質ベース動的ポジション枠 比較バックテスト開始")
    print(f"期間: {start_year}〜{end_year}年 / 通貨: {SYMBOL}")
    print(f"シナリオ数: {len(SCENARIOS)}")

    rows: list[CompareRow] = []
    for scenario in SCENARIOS:
        row = run_one(scenario, start_year, end_year)
        rows.append(row)

    print_comparison_table(rows, start_year, end_year)
    save_report(rows, start_year, end_year)


if __name__ == "__main__":
    main()
