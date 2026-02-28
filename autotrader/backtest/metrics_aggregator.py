"""メトリクス集計モジュール

年別結果から全体メトリクスを集計し、トレードログの品質検証を行う。
"""

from __future__ import annotations

import logging
from typing import Any


def aggregate_results(
    yearly_results: list[dict[str, Any]],
    monthly_results: list[dict[str, Any]],
    initial_balance: float,
) -> "BacktestResult":
    """結果を集計（後方互換用）

    Args:
        yearly_results: 年別結果
        monthly_results: 月別結果
        initial_balance: 初期残高

    Returns:
        BacktestResult: 集計結果
    """
    from autotrader.backtest.runner import BacktestResult

    result = aggregate_results_from_yearly(yearly_results, initial_balance)
    # 外部から monthly_results が渡された場合は上書き
    if monthly_results:
        result.monthly_results = monthly_results
    return result


def aggregate_results_from_yearly(
    yearly_results: list[dict[str, Any]],
    initial_balance: float,
) -> "BacktestResult":
    """年別結果から集計結果を生成

    各年の結果から月別結果を抽出して集計する。

    Args:
        yearly_results: 年別結果（monthly_results フィールドを含む）
        initial_balance: 初期残高

    Returns:
        BacktestResult: 集計結果
    """
    from autotrader.backtest.runner import BacktestResult

    if not yearly_results:
        return BacktestResult()

    total_trades = sum(r["trades"] for r in yearly_results)
    total_profit = sum(r["net_profit"] for r in yearly_results)
    avg_win_rate = sum(r["win_rate"] for r in yearly_results) / len(
        yearly_results
    )
    avg_non_loss_rate = sum(
        r.get("non_loss_rate", 0) for r in yearly_results
    ) / len(yearly_results)
    avg_pf = sum(r["profit_factor"] for r in yearly_results) / len(
        yearly_results
    )
    max_dd = max(r["max_drawdown"] for r in yearly_results)
    avg_sharpe = sum(r["sharpe"] for r in yearly_results) / len(
        yearly_results
    )

    years = len(yearly_results)
    annual_return = (
        total_profit / initial_balance * 100 / years
    )

    # 各年の月別結果をマージして時系列順にソート
    monthly_results: list[dict[str, Any]] = []
    for yr in yearly_results:
        monthly_results.extend(yr.get("monthly_results", []))
    monthly_results.sort(
        key=lambda r: (r.get("year", 0), r.get("month", 0))
    )

    return BacktestResult(
        trades=total_trades,
        win_rate=avg_win_rate,
        non_loss_rate=avg_non_loss_rate,
        profit_factor=avg_pf,
        net_profit=total_profit,
        max_drawdown=max_dd,
        sharpe_ratio=avg_sharpe,
        annual_return=annual_return,
        monthly_results=monthly_results,
        yearly_results=yearly_results,
    )


def validate_trade_log(
    trades: list,
    year: int,
) -> None:
    """トレードログの品質チェック

    regime/mode/scoreが欠落していないか検証。
    部分決済(parent_trade_id付き)は親情報を継承するため
    score=0でもエラーとしない。

    Args:
        trades: トレードリスト
        year: 対象年
    """
    _log = logging.getLogger(__name__)
    errors: list[str] = []
    for t in trades:
        tid = t.trade_id[:8] if t.trade_id else "?"
        if not t.regime or t.regime == "UNKNOWN":
            errors.append(
                f"regime欠落: {tid}"
            )
        if not t.mode or t.mode == "UNKNOWN":
            errors.append(
                f"mode欠落: {tid}"
            )
        if (
            (t.consensus_score or 0) == 0
            and not t.parent_trade_id
        ):
            errors.append(
                f"score=0: {tid}"
            )
    if errors:
        msg = (
            f"{year}年ログ品質警告"
            f"({len(errors)}件):\n"
            + "\n".join(errors[:10])
        )
        _log.warning(msg)
