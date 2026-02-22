#!/usr/bin/env python3
"""3パターン比較バックテストスクリプト

A: ベースライン（現行デフォルト）
B: RANGE×DAY +0.5R → 20%部分利確 → 残りBE
C: SWING stagnation 120分→90分（MFE<0.15R維持）

条件:
  1: 2023-2025 (通常コスト)
  2: 2010-2022 (通常コスト)
  3: 2010-2025 (コストストレス: spread=2.5, slip=1.0)
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# プロジェクトルートをパスに追加
try:
    project_root = Path(__file__).parent.parent
except NameError:
    project_root = Path("D:/Projects/AutoTraderV4")
sys.path.insert(0, str(project_root))


def run_comparison() -> None:
    """3パターン×3条件の比較バックテスト実行"""
    from autotrader.backtest.runner import (
        BacktestConfig,
        BacktestRunner,
    )
    from autotrader.config import DEFAULT_TRADING_PARAMS
    from autotrader.decision.unified import UnifiedBotConfig
    from autotrader.decision.unified.position_manager import (
        PositionManagerConfig,
    )

    # パターン定義
    patterns: dict[str, dict] = {
        "A:ベースライン": {
            "pm_overrides": {},
        },
        "B:0.5R部分利確": {
            "pm_overrides": {
                "range_day_half_r_partial_enabled": True,
            },
        },
        "C:SWING 90分": {
            "pm_overrides": {
                "swing_stagnation_exit_minutes": 90.0,
            },
        },
    }

    # 条件定義
    conditions: dict[str, dict] = {
        "2023-2025": {
            "start_year": 2023,
            "end_year": 2025,
            "spread_pips": DEFAULT_TRADING_PARAMS.spread_pips,
            "slippage_pips": DEFAULT_TRADING_PARAMS.slippage_pips,
        },
        "2010-2022": {
            "start_year": 2010,
            "end_year": 2022,
            "spread_pips": DEFAULT_TRADING_PARAMS.spread_pips,
            "slippage_pips": DEFAULT_TRADING_PARAMS.slippage_pips,
        },
        "コストストレス": {
            "start_year": 2010,
            "end_year": 2025,
            "spread_pips": 2.5,
            "slippage_pips": 1.0,
        },
    }

    # データ読み込み（1回のみ）
    print("データ読み込み中...")
    t0 = time.time()
    base_config = BacktestConfig(
        symbol="USDJPY",
        timeframe="M15",
        initial_balance=1_000_000.0,
        volume=1.0,
        max_positions=1,
    )
    runner = BacktestRunner(
        data_dir="data/csv",
        config=base_config,
        verbose=False,
        log_to_file=False,
    )
    runner.load_data()
    print(f"データ読み込み完了 ({time.time()-t0:.1f}秒)")

    # 結果格納
    # results[条件名][パターン名] = BacktestResult
    results: dict[str, dict] = {}

    total_runs = len(patterns) * len(conditions)
    run_count = 0

    for cond_name, cond in conditions.items():
        results[cond_name] = {}

        for pat_name, pat in patterns.items():
            run_count += 1
            print(
                f"\n[{run_count}/{total_runs}] "
                f"{cond_name} × {pat_name}"
            )
            t1 = time.time()

            # BacktestConfig更新
            runner.config.spread_pips = cond["spread_pips"]
            runner.config.slippage_pips = cond["slippage_pips"]

            # BotConfig（デフォルト）
            bot_config = UnifiedBotConfig(
                timeframes=["M15", "H1", "H4", "D1"],
                use_dynamic_lot=False,
            )

            # PMConfig（パターン別オーバーライド）
            pm_config = PositionManagerConfig(
                **pat["pm_overrides"],
            )

            result = runner.run_unified(
                start_year=cond["start_year"],
                end_year=cond["end_year"],
                config=bot_config,
                use_m1=True,
                pm_config=pm_config,
            )

            results[cond_name][pat_name] = result
            elapsed = time.time() - t1
            print(
                f"  → 取引{result.trades}, "
                f"PF={result.profit_factor:.2f}, "
                f"DD={result.max_drawdown:.2f}% "
                f"({elapsed:.1f}秒)"
            )

    # レポート生成
    report = generate_report(results, conditions)
    print(report)

    # ファイル保存
    reports_dir = project_root / "reports"
    reports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"comparison_{timestamp}.txt"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nレポート保存: {report_path}")


def _extract_exit_breakdown(
    yearly_results: list,
) -> dict[str, dict]:
    """年別結果からexit_reason別の集計を抽出

    Args:
        yearly_results: 年別結果リスト

    Returns:
        dict: exit_reason → {count, net_profit}
    """
    totals: dict[str, dict] = {}
    for yr in yearly_results:
        bd = yr.get("breakdown", {})
        exit_bd = bd.get("exit_reason", {})
        for reason, data in exit_bd.items():
            if reason not in totals:
                totals[reason] = {
                    "count": 0,
                    "net_profit": 0.0,
                }
            totals[reason]["count"] += data.get("count", 0)
            totals[reason]["net_profit"] += data.get(
                "net_profit", 0.0
            )
    return totals


def generate_report(
    results: dict[str, dict],
    conditions: dict[str, dict],
) -> str:
    """比較レポートを生成

    Args:
        results: 結果データ
        conditions: 条件定義

    Returns:
        str: レポートテキスト
    """
    lines: list[str] = []
    lines.append("\n" + "=" * 100)
    lines.append("=== 3パターン比較結果 ===")
    lines.append("=" * 100)

    for cond_name in conditions:
        cond = conditions[cond_name]
        lines.append(
            f"\n【{cond_name}】"
            f" ({cond['start_year']}-{cond['end_year']}"
            f", sp={cond['spread_pips']}"
            f", slip={cond['slippage_pips']})"
        )

        # ヘッダー
        hdr = (
            f"{'パターン':<18} "
            f"{'取引':>6} "
            f"{'勝率':>7} "
            f"{'非敗率':>7} "
            f"{'PF':>6} "
            f"{'純利益':>14} "
            f"{'DD':>7} "
            f"{'Sharpe':>7} "
            f"{'年収率':>8} "
            f"{'SL損失':>14} "
            f"{'TRAIL+TP2':>14} "
            f"{'TP_EARLY':>14}"
        )
        lines.append(hdr)
        lines.append("-" * len(hdr))

        for pat_name in results[cond_name]:
            r = results[cond_name][pat_name]

            # exit_reason別集計
            exit_bd = _extract_exit_breakdown(
                r.yearly_results
            )
            sl_loss = exit_bd.get(
                "STOP_LOSS", {}
            ).get("net_profit", 0.0)
            trail_pnl = exit_bd.get(
                "TRAILING_STOP", {}
            ).get("net_profit", 0.0)
            tp2_pnl = exit_bd.get(
                "TAKE_PROFIT_2R", {}
            ).get("net_profit", 0.0)
            tp_early_pnl = exit_bd.get(
                "TAKE_PROFIT_EARLY", {}
            ).get("net_profit", 0.0)
            trail_tp2 = trail_pnl + tp2_pnl

            row = (
                f"{pat_name:<18} "
                f"{r.trades:>6} "
                f"{r.win_rate:>6.1f}% "
                f"{r.non_loss_rate:>6.1f}% "
                f"{r.profit_factor:>6.2f} "
                f"¥{r.net_profit:>+12,.0f} "
                f"{r.max_drawdown:>6.2f}% "
                f"{r.sharpe_ratio:>7.2f} "
                f"{r.annual_return:>7.1f}% "
                f"¥{sl_loss:>+12,.0f} "
                f"¥{trail_tp2:>+12,.0f} "
                f"¥{tp_early_pnl:>+12,.0f}"
            )
            lines.append(row)

        lines.append("")

    # exit_reason詳細（最初の条件のみ）
    first_cond = list(conditions.keys())[0]
    lines.append(f"\n--- Exit Reason 詳細 [{first_cond}] ---")
    for pat_name in results[first_cond]:
        r = results[first_cond][pat_name]
        exit_bd = _extract_exit_breakdown(
            r.yearly_results
        )
        lines.append(f"\n  {pat_name}:")
        for reason in sorted(exit_bd.keys()):
            d = exit_bd[reason]
            lines.append(
                f"    {reason:<25} "
                f"n={d['count']:>4} "
                f"PnL=¥{d['net_profit']:>+12,.0f}"
            )

    return "\n".join(lines)


if __name__ == "__main__":
    run_comparison()
