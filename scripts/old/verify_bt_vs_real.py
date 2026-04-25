"""バックテスト vs リアルトレード 乖離検証スクリプト

リアルトレードDBから全決済済みトレードを抽出し、
同一期間・同一設定でバックテストを再実行して乖離を分析する。

使い方:
  uv run python scripts/verify_bt_vs_real.py --db /path/to/autotrader.db
  uv run python scripts/verify_bt_vs_real.py --db /mnt/d/Projects/AutoTraderV4/data/autotrader.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_real_trades(db_path: str) -> pd.DataFrame:
    """リアルトレードDBから決済済みトレードを読み込み"""
    conn = sqlite3.connect(db_path)
    query = """
        SELECT trade_id, symbol, signal_type, volume,
               entry_price, exit_price, stop_loss, take_profit,
               profit_loss, profit_loss_pips, exit_reason,
               entry_own_score, opened_at, closed_at
        FROM trades
        WHERE is_open = 0
        ORDER BY opened_at
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    df["opened_at"] = pd.to_datetime(df["opened_at"])
    df["closed_at"] = pd.to_datetime(df["closed_at"])

    logger.info(f"リアルトレード読み込み: {len(df)}件")
    if len(df) > 0:
        logger.info(
            f"  期間: {df['opened_at'].min()} → {df['opened_at'].max()}"
        )
        logger.info(f"  通貨: {df['symbol'].unique().tolist()}")
        pnl = df["profit_loss"].sum()
        wr = (df["profit_loss"] > 0).mean() * 100
        logger.info(f"  合計PnL: {pnl:+,.0f}, WR: {wr:.1f}%")
    return df


def replay_single_trade(
    trade: pd.Series,
    runner_class: type,
    bt_config_class: type,
    bot_config_class: type,
) -> dict:
    """1トレードを再生して乖離を分析

    Args:
        trade: リアルトレードの1行
        runner_class: BacktestRunner class
        bt_config_class: BacktestConfig class
        bot_config_class: UnifiedBotConfig class

    Returns:
        dict: 乖離分析結果
    """
    symbol = trade["symbol"]
    opened_at = trade["opened_at"]
    closed_at = trade["closed_at"]

    # トレード前後の期間でバックテスト
    # opened_atの月初から月末まで
    start_date = opened_at.replace(day=1, hour=0, minute=0, second=0)
    end_date = (start_date + timedelta(days=35)).replace(day=1)

    result = {
        "trade_id": trade["trade_id"],
        "symbol": symbol,
        "real_direction": trade["signal_type"],
        "real_entry_price": trade["entry_price"],
        "real_exit_price": trade["exit_price"],
        "real_sl": trade["stop_loss"],
        "real_tp": trade["take_profit"],
        "real_pnl": trade["profit_loss"],
        "real_pnl_pips": trade["profit_loss_pips"],
        "real_exit_reason": trade["exit_reason"],
        "real_score": trade["entry_own_score"],
        "real_opened_at": str(opened_at),
        "real_closed_at": str(closed_at),
        "divergence_type": "UNKNOWN",
        "details": "",
    }

    try:
        bt_config = bt_config_class(symbol=symbol, timeframe="H1")
        runner = runner_class(config=bt_config)
        bot_config = bot_config_class()

        bt_result = runner.run_unified_monthly(
            start_year=opened_at.year,
            end_year=opened_at.year,
            config=bot_config,
            use_m1=False,
            max_month_workers=1,
            sequential=True,
            period_start=start_date,
            period_end=end_date,
        )

        result["bt_trades_in_period"] = bt_result.trades
        result["bt_pnl_in_period"] = bt_result.net_profit

        # 月全体のBT結果とリアルを比較
        if bt_result.trades == 0:
            result["divergence_type"] = "BT_NO_SIGNAL"
            result["details"] = (
                "BTがこの月にシグナルを出さなかった"
            )
        else:
            result["divergence_type"] = "PERIOD_MATCH"
            result["details"] = (
                f"BT: {bt_result.trades}件, "
                f"PnL={bt_result.net_profit:+,.0f}"
            )

    except Exception as e:
        result["divergence_type"] = "ERROR"
        result["details"] = str(e)

    return result


def run_verification(db_path: str, output_dir: str) -> None:
    """検証を実行"""
    from autotrader.backtest.runner import BacktestRunner, BacktestConfig
    from autotrader.decision.unified.config import UnifiedBotConfig

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. リアルトレード読み込み
    trades = load_real_trades(db_path)
    if trades.empty:
        logger.error("リアルトレードが0件。DBパスを確認してください。")
        return

    # リアルトレードサマリーを保存
    trades.to_csv(out / "real_trades.csv", index=False)

    # 2. 月別にグルーピングしてBTと比較
    trades["month"] = trades["opened_at"].dt.to_period("M")
    monthly_real = (
        trades.groupby(["symbol", "month"])
        .agg(
            trades=("trade_id", "count"),
            pnl=("profit_loss", "sum"),
            wins=("profit_loss", lambda x: (x > 0).sum()),
            avg_pnl=("profit_loss", "mean"),
        )
        .reset_index()
    )

    logger.info(f"\n月別リアル実績: {len(monthly_real)}ヶ月×通貨")

    # 3. 各月のBTを実行して比較
    comparisons = []
    logging.disable(logging.WARNING)  # BT内部ログを抑制

    for _, row in monthly_real.iterrows():
        symbol = row["symbol"]
        period = row["month"]
        year = period.year
        month = period.month

        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)

        try:
            bt_config = BacktestConfig(symbol=symbol, timeframe="H1")
            runner = BacktestRunner(config=bt_config)
            bt_result = runner.run_unified_monthly(
                start_year=year,
                end_year=year,
                config=UnifiedBotConfig(),
                use_m1=False,
                max_month_workers=1,
                sequential=True,
                period_start=start,
                period_end=end,
            )

            comp = {
                "symbol": symbol,
                "month": str(period),
                "real_trades": int(row["trades"]),
                "real_pnl": float(row["pnl"]),
                "real_wins": int(row["wins"]),
                "bt_trades": bt_result.trades,
                "bt_pnl": bt_result.net_profit,
                "trade_diff": bt_result.trades - int(row["trades"]),
                "pnl_diff": bt_result.net_profit - float(row["pnl"]),
            }
        except Exception as e:
            comp = {
                "symbol": symbol,
                "month": str(period),
                "real_trades": int(row["trades"]),
                "real_pnl": float(row["pnl"]),
                "real_wins": int(row["wins"]),
                "bt_trades": -1,
                "bt_pnl": 0,
                "trade_diff": 0,
                "pnl_diff": 0,
                "error": str(e),
            }
        comparisons.append(comp)

    logging.disable(logging.NOTSET)

    # 4. 結果出力
    comp_df = pd.DataFrame(comparisons)
    comp_df.to_csv(out / "bt_vs_real_monthly.csv", index=False)

    print("\n" + "=" * 70)
    print("BT vs REAL MONTHLY COMPARISON")
    print("=" * 70)
    print(
        f"{'Month':<10} {'Symbol':<10} "
        f"{'R_Trades':>8} {'B_Trades':>8} {'Diff':>6} "
        f"{'R_PnL':>12} {'B_PnL':>12} {'PnL_Diff':>12}"
    )
    print("-" * 80)

    for _, c in comp_df.iterrows():
        print(
            f"{c['month']:<10} {c['symbol']:<10} "
            f"{c['real_trades']:>8} {c['bt_trades']:>8} "
            f"{c['trade_diff']:>+6} "
            f"{c['real_pnl']:>+12,.0f} {c['bt_pnl']:>+12,.0f} "
            f"{c['pnl_diff']:>+12,.0f}"
        )

    # サマリー
    total_real_pnl = comp_df["real_pnl"].sum()
    total_bt_pnl = comp_df["bt_pnl"].sum()
    total_real_trades = comp_df["real_trades"].sum()
    total_bt_trades = comp_df["bt_trades"].sum()

    print("-" * 80)
    print(
        f"{'TOTAL':<10} {'':10} "
        f"{total_real_trades:>8} {total_bt_trades:>8} "
        f"{total_bt_trades - total_real_trades:>+6} "
        f"{total_real_pnl:>+12,.0f} {total_bt_pnl:>+12,.0f} "
        f"{total_bt_pnl - total_real_pnl:>+12,.0f}"
    )

    print("\n" + "=" * 70)
    print("DIVERGENCE ANALYSIS")
    print("=" * 70)

    # 乖離パターン分析
    if len(comp_df) > 0:
        more_trades_bt = (comp_df["trade_diff"] > 0).sum()
        fewer_trades_bt = (comp_df["trade_diff"] < 0).sum()
        same_trades = (comp_df["trade_diff"] == 0).sum()

        print(f"  BT > Real (BTの方がトレード多い): {more_trades_bt}ヶ月")
        print(f"  BT < Real (リアルの方がトレード多い): {fewer_trades_bt}ヶ月")
        print(f"  BT == Real (一致): {same_trades}ヶ月")
        print()

        pnl_corr = comp_df[["real_pnl", "bt_pnl"]].corr().iloc[0, 1]
        print(f"  月次PnL相関: {pnl_corr:.3f}")

        avg_pnl_diff = comp_df["pnl_diff"].mean()
        print(f"  平均PnL乖離: {avg_pnl_diff:+,.0f}/月")

    # JSON出力
    with open(out / "summary.json", "w") as f:
        json.dump(
            {
                "total_real_trades": int(total_real_trades),
                "total_bt_trades": int(total_bt_trades),
                "total_real_pnl": float(total_real_pnl),
                "total_bt_pnl": float(total_bt_pnl),
                "monthly_comparisons": comparisons,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    logger.info(f"\n結果保存: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="バックテスト vs リアルトレード 乖離検証"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="リアルトレードDBのパス (autotrader.db)",
    )
    parser.add_argument(
        "--output",
        default="/tmp/bt_vs_real",
        help="結果出力先ディレクトリ",
    )
    args = parser.parse_args()
    run_verification(args.db, args.output)


if __name__ == "__main__":
    main()
