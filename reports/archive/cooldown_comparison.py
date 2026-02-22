"""クールダウン設定比較バックテスト

cooldown_minutes の値を変えて同一期間のバックテストを実行し、
機会損失の有無と規模を定量的に比較する。

使い方:
    python reports/cooldown_comparison.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autotrader.backtest.runner import BacktestConfig, BacktestResult, BacktestRunner
from autotrader.decision.unified import UnifiedBotConfig
from autotrader.decision.unified.config import RiskConfig

# ===== 設定 =====
DATA_DIR = "data/csv"
START_YEAR = 2022
END_YEAR = 2024
INITIAL_BALANCE = 1_000_000.0
VOLUME = 1.0
SYMBOL = "USDJPY"

# 比較するクールダウン設定（分）
COOLDOWN_VARIANTS: list[tuple[str, int]] = [
    ("cooldown=5min（デフォルト）", 5),
    ("cooldown=2min（短縮）", 2),
    ("cooldown=0min（無効化）", 0),
]


@dataclass
class CompareRow:
    """比較結果1行"""

    label: str
    cooldown_minutes: int
    trades: int
    win_rate: float
    profit_factor: float
    net_profit: float
    max_drawdown: float
    sharpe_ratio: float
    annual_return: float
    elapsed_sec: float


def run_one(label: str, cooldown_minutes: int) -> CompareRow:
    """1条件でバックテストを実行して結果を返す

    Args:
        label: 条件ラベル
        cooldown_minutes: クールダウン時間（分）

    Returns:
        CompareRow: 比較結果
    """
    print(f"\n{'='*60}")
    print(f"実行中: {label}")
    print(f"{'='*60}")

    backtest_config = BacktestConfig(
        symbol=SYMBOL,
        timeframe="M5",
        initial_balance=INITIAL_BALANCE,
        volume=VOLUME,
        max_positions=1,
    )
    runner = BacktestRunner(
        data_dir=DATA_DIR,
        config=backtest_config,
        verbose=False,
    )

    bot_config = UnifiedBotConfig(
        timeframes=["M15", "H1", "H4", "D1"],
        risk=RiskConfig(cooldown_minutes=cooldown_minutes),
    )

    t0 = time.time()
    result: BacktestResult = runner.run_unified(
        start_year=START_YEAR,
        end_year=END_YEAR,
        config=bot_config,
    )
    elapsed = time.time() - t0

    print(f"  トレード数: {result.trades}")
    print(f"  勝率:       {result.win_rate:.1f}%")
    print(f"  PF:         {result.profit_factor:.3f}")
    print(f"  純利益:     {result.net_profit:,.0f}円")
    print(f"  最大DD:     {result.max_drawdown:.2f}%")
    print(f"  シャープ:   {result.sharpe_ratio:.3f}")
    print(f"  年間収益率: {result.annual_return:.2f}%")
    print(f"  実行時間:   {elapsed:.1f}秒")

    return CompareRow(
        label=label,
        cooldown_minutes=cooldown_minutes,
        trades=result.trades,
        win_rate=result.win_rate,
        profit_factor=result.profit_factor,
        net_profit=result.net_profit,
        max_drawdown=result.max_drawdown,
        sharpe_ratio=result.sharpe_ratio,
        annual_return=result.annual_return,
        elapsed_sec=elapsed,
    )


def print_comparison_table(rows: list[CompareRow]) -> None:
    """比較テーブルを出力

    Args:
        rows: 比較結果リスト
    """
    print(f"\n{'='*80}")
    print("クールダウン比較結果")
    print(f"対象期間: {START_YEAR}〜{END_YEAR}年 / 通貨: {SYMBOL}")
    print(f"{'='*80}")

    # ヘッダー
    header = (
        f"{'条件':<30} {'取引数':>6} {'勝率%':>7} {'PF':>7} "
        f"{'純利益':>10} {'MaxDD%':>7} {'シャープ':>8} {'年利%':>7}"
    )
    print(header)
    print("-" * 80)

    # データ行
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
            f"{row.label:<30} {row.trades:>6}{trade_diff:<8}"
            f"{row.win_rate:>6.1f}% {row.profit_factor:>7.3f} "
            f"{row.net_profit:>10,.0f}{profit_diff:<12}"
            f"{row.max_drawdown:>6.2f}% {row.sharpe_ratio:>8.3f} "
            f"{row.annual_return:>6.2f}%"
        )

    print("=" * 80)

    # 機会損失の判定
    if len(rows) >= 2:
        base = rows[0]
        no_cd = rows[-1]  # cooldown=0
        trade_increase = no_cd.trades - base.trades
        profit_increase = no_cd.net_profit - base.net_profit
        profit_increase_pct = (
            profit_increase / abs(base.net_profit) * 100
            if base.net_profit != 0 else float("inf")
        )
        print("\n[機会損失の評価]")
        print(
            f"  クールダウン無効化による追加トレード数: {trade_increase:+d}件"
        )
        print(
            f"  クールダウン無効化による純利益の変化: "
            f"{profit_increase:+,.0f}円 ({profit_increase_pct:+.1f}%)"
        )
        if profit_increase_pct > 5:
            print("  → 機会損失大: クールダウン短縮を検討")
        elif profit_increase_pct > 1:
            print("  → 機会損失中程度: 状況次第で調整を検討")
        else:
            print("  → 機会損失小: クールダウンは収益への影響が軽微")


def save_report(rows: list[CompareRow]) -> None:
    """レポートファイルに保存

    Args:
        rows: 比較結果リスト
    """
    from datetime import date

    today = date.today().strftime("%Y%m%d")
    out_path = Path("reports") / f"cooldown_comparison_{today}.md"

    lines = [
        "# クールダウン比較レポート",
        "",
        f"- 対象期間: {START_YEAR}〜{END_YEAR}年",
        f"- 通貨ペア: {SYMBOL}",
        f"- 初期残高: {INITIAL_BALANCE:,.0f}円",
        "",
        "| 条件 | 取引数 | 勝率% | PF | 純利益 | MaxDD% | シャープ | 年利% |",
        "|------|--------|-------|-----|--------|--------|----------|-------|",
    ]
    for row in rows:
        lines.append(
            f"| {row.label} | {row.trades} | {row.win_rate:.1f} "
            f"| {row.profit_factor:.3f} | {row.net_profit:,.0f} "
            f"| {row.max_drawdown:.2f} | {row.sharpe_ratio:.3f} "
            f"| {row.annual_return:.2f} |"
        )

    if len(rows) >= 2:
        base = rows[0]
        no_cd = rows[-1]
        profit_increase = no_cd.net_profit - base.net_profit
        profit_increase_pct = (
            profit_increase / abs(base.net_profit) * 100
            if base.net_profit != 0 else float("inf")
        )
        lines += [
            "",
            "## 機会損失の評価",
            f"- 追加トレード数（CD無効化）: {no_cd.trades - base.trades:+d}件",
            f"- 純利益の変化（CD無効化）: {profit_increase:+,.0f}円 "
            f"({profit_increase_pct:+.1f}%)",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nレポートを保存しました: {out_path}")


def main() -> None:
    """メイン処理"""
    print(f"クールダウン比較バックテスト開始")
    print(f"期間: {START_YEAR}〜{END_YEAR}年 / 通貨: {SYMBOL}")

    rows: list[CompareRow] = []
    for label, cooldown_min in COOLDOWN_VARIANTS:
        row = run_one(label, cooldown_min)
        rows.append(row)

    print_comparison_table(rows)
    save_report(rows)


if __name__ == "__main__":
    main()
