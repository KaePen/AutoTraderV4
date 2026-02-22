"""最大同時ポジション数比較バックテスト

max_positions の値を変えて同一期間のバックテストを実行し、
クールダウンを維持したまま同時エントリー緩和が収益性に与える影響を定量的に比較する。

前提:
- cooldown_minutes=5（デフォルト）を維持
- max_positions だけを変化させる
- 動的ロットサイジング（use_dynamic_lot=True）は維持
- max_total_exposure_lot で合計ロット上限を制御

使い方:
    python reports/max_positions_comparison.py
    python reports/max_positions_comparison.py --years 2023
    python reports/max_positions_comparison.py --years 2022-2024 --max-pos 1,2,3,5
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from autotrader.backtest.runner import BacktestConfig, BacktestResult, BacktestRunner
from autotrader.decision.unified import UnifiedBotConfig
from autotrader.decision.unified.position_manager import PositionManagerConfig

# ===== デフォルト設定 =====
def _find_data_dir() -> str:
    """git rootを基準にdataディレクトリを解決する。"""
    import subprocess as _sp
    try:
        _root = _sp.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path(__file__).parent),
            text=True,
        ).strip()
        _path = Path(_root) / "data"
        if _path.exists() and (_path / "USDJPY").exists():
            return str(_path)
    except Exception:
        pass
    _candidate = Path(__file__).resolve().parent
    for _ in range(6):
        _data = _candidate / "data"
        if _data.exists() and (_data / "USDJPY").exists():
            return str(_data)
        _candidate = _candidate.parent
    return "data"


DATA_DIR = _find_data_dir()
START_YEAR = 2022
END_YEAR = 2024
INITIAL_BALANCE = 1_000_000.0
SYMBOL = "USDJPY"
COOLDOWN_MINUTES = 5  # 変更しない

# 比較する最大ポジション数
MAX_POSITIONS_VARIANTS: list[int] = [1, 2, 3]


@dataclass
class CompareRow:
    """比較結果1行"""

    max_positions: int
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
    max_positions: int,
    start_year: int,
    end_year: int,
    use_m1: bool = True,
) -> CompareRow:
    """1条件でバックテストを実行して結果を返す

    Args:
        max_positions: 最大同時ポジション数
        start_year: 開始年
        end_year: 終了年
        use_m1: M1データを使用するか（Falseなら高速）

    Returns:
        CompareRow: 比較結果
    """
    label = f"max_positions={max_positions}"
    print(f"\n{'='*60}")
    print(f"実行中: {label}")
    print(f"{'='*60}")

    backtest_config = BacktestConfig(
        symbol=SYMBOL,
        initial_balance=INITIAL_BALANCE,
        max_positions=max_positions,
    )
    runner = BacktestRunner(
        data_dir=DATA_DIR,
        config=backtest_config,
        verbose=False,
    )

    # max_positionsに応じて合計ロット上限を比例拡張
    max_total_exposure = max(5.0, float(max_positions) * 2.0)

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

    print(f"  max_positions: {max_positions}")
    print(f"  max_total_exposure_lot: {max_total_exposure}")
    print(f"  cooldown_minutes: {COOLDOWN_MINUTES}（固定）")

    t0 = time.time()
    runner.load_data()
    result: BacktestResult = runner.run_unified(
        start_year=start_year,
        end_year=end_year,
        config=bot_config,
        use_m1=use_m1,
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

    # 年別表示
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
        max_positions=max_positions,
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


def print_comparison_table(
    rows: list[CompareRow], start_year: int, end_year: int
) -> None:
    """比較テーブルを出力

    Args:
        rows: 比較結果リスト
        start_year: 開始年
        end_year: 終了年
    """
    print(f"\n{'='*90}")
    print("最大同時ポジション数 比較結果")
    print(f"対象期間: {start_year}〜{end_year}年 / 通貨: {SYMBOL}")
    print(f"クールダウン: {COOLDOWN_MINUTES}分（全条件共通）")
    print(f"{'='*90}")

    header = (
        f"{'max_pos':>8} {'取引数':>7} {'勝率%':>7} {'非敗%':>7} {'PF':>7} "
        f"{'純利益':>12} {'MaxDD%':>7} {'シャープ':>9} {'年利%':>7}"
    )
    print(header)
    print("-" * 90)

    baseline = rows[0] if rows else None
    for row in rows:
        trade_diff = ""
        profit_diff = ""
        if baseline and row is not baseline:
            d_trades = row.trades - baseline.trades
            d_profit = row.net_profit - baseline.net_profit
            trade_diff = f"({d_trades:+d})"
            profit_diff = f"({d_profit:+,.0f})"

        print(
            f"{row.max_positions:>8} "
            f"{row.trades:>7}{trade_diff:<9}"
            f"{row.win_rate:>6.1f}% "
            f"{row.non_loss_rate:>6.1f}% "
            f"{row.profit_factor:>7.3f} "
            f"{row.net_profit:>12,.0f}{profit_diff:<14}"
            f"{row.max_drawdown:>6.2f}% "
            f"{row.sharpe_ratio:>9.3f} "
            f"{row.annual_return:>6.2f}%"
        )

    print("=" * 90)

    # 評価
    if len(rows) >= 2:
        base = rows[0]
        print("\n[機会緩和の評価]")
        for row in rows[1:]:
            d_trades = row.trades - base.trades
            d_profit = row.net_profit - base.net_profit
            d_pct = (
                d_profit / abs(base.net_profit) * 100
                if base.net_profit != 0
                else float("inf")
            )
            d_dd = row.max_drawdown - base.max_drawdown
            d_wr = row.win_rate - base.win_rate
            print(
                f"  max_pos={row.max_positions} vs 1: "
                f"取引{d_trades:+d}件 "
                f"利益{d_profit:+,.0f}円({d_pct:+.1f}%) "
                f"DD{d_dd:+.2f}% "
                f"勝率{d_wr:+.1f}%"
            )
            if d_pct > 10 and d_dd < 5:
                print(f"    -> [OK] 推奨: DD増加軽微で大きな利益向上")
            elif d_pct > 0 and d_dd < 10:
                print(f"    -> [?] 検討可: 利益改善あるがDD増加に注意")
            elif d_pct <= 0:
                print(f"    -> [NG] 非推奨: 利益改善なし")
            else:
                print(f"    -> [NG] 非推奨: DD増加が大きい")


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
    out_path = Path(__file__).parent / f"max_positions_comparison_{today}.md"

    lines = [
        "# 最大同時ポジション数 比較レポート",
        "",
        f"- 対象期間: {start_year}〜{end_year}年",
        f"- 通貨ペア: {SYMBOL}",
        f"- 初期残高: {INITIAL_BALANCE:,.0f}円",
        f"- クールダウン: {COOLDOWN_MINUTES}分（全条件共通）",
        "",
        "| max_pos | 取引数 | 勝率% | 非敗% | PF | 純利益 | MaxDD% | シャープ | 年利% |",
        "|---------|--------|-------|-------|-----|--------|--------|----------|-------|",
    ]

    for row in rows:
        lines.append(
            f"| {row.max_positions} | {row.trades} | {row.win_rate:.1f} "
            f"| {row.non_loss_rate:.1f} "
            f"| {row.profit_factor:.3f} | {row.net_profit:,.0f} "
            f"| {row.max_drawdown:.2f} | {row.sharpe_ratio:.3f} "
            f"| {row.annual_return:.2f} |"
        )

    if len(rows) >= 2:
        base = rows[0]
        lines += ["", "## 機会緩和の評価"]
        for row in rows[1:]:
            d_trades = row.trades - base.trades
            d_profit = row.net_profit - base.net_profit
            d_pct = (
                d_profit / abs(base.net_profit) * 100
                if base.net_profit != 0
                else float("inf")
            )
            d_dd = row.max_drawdown - base.max_drawdown
            lines.append(
                f"- max_pos={row.max_positions}: "
                f"追加{d_trades:+d}件, "
                f"利益変化{d_profit:+,.0f}円({d_pct:+.1f}%), "
                f"DD変化{d_dd:+.2f}%"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nレポートを保存しました: {out_path}")


def parse_args() -> argparse.Namespace:
    """引数をパース"""
    parser = argparse.ArgumentParser(
        description="最大同時ポジション数比較バックテスト"
    )
    parser.add_argument(
        "--years",
        type=str,
        default="2022-2024",
        help="バックテスト期間（例: 2022-2024 または 2023）",
    )
    parser.add_argument(
        "--max-pos",
        type=str,
        default="1,2,3",
        help="比較するmax_positionsのカンマ区切りリスト（例: 1,2,3,5）",
    )
    parser.add_argument(
        "--no-m1",
        action="store_true",
        default=False,
        help="M1データなし高速モード（SL/TPが若干不正確だが高速）",
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
    max_pos_list = [int(x.strip()) for x in args.max_pos.split(",")]

    print("最大同時ポジション数比較バックテスト開始")
    print(f"期間: {start_year}〜{end_year}年 / 通貨: {SYMBOL}")
    print(f"比較条件: max_positions = {max_pos_list}")
    print(f"クールダウン: {COOLDOWN_MINUTES}分（全条件で維持）")

    use_m1 = not args.no_m1
    print(f"M1モード: {'有効' if use_m1 else '無効（高速）'}")

    rows: list[CompareRow] = []
    for max_pos in max_pos_list:
        row = run_one(max_pos, start_year, end_year, use_m1=use_m1)
        rows.append(row)

    print_comparison_table(rows, start_year, end_year)
    save_report(rows, start_year, end_year)


if __name__ == "__main__":
    main()
