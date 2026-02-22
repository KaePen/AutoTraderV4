"""バックテスト評価指標計算

勝率、プロフィットファクター、ドローダウン、シャープレシオ等を計算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from autotrader.core.entities import Trade
from autotrader.core.enums import ExitReason, SignalType


@dataclass
class BacktestMetrics:
    """バックテスト評価指標

    Attributes:
        total_trades: 総トレード数
        winning_trades: 勝ちトレード数
        losing_trades: 負けトレード数
        win_rate: 勝率
        profit_factor: プロフィットファクター
        total_profit: 総利益
        total_loss: 総損失
        net_profit: 純利益
        max_drawdown: 最大ドローダウン金額
        max_drawdown_pct: 最大ドローダウン率
        sharpe_ratio: シャープレシオ
        sortino_ratio: ソルティノレシオ
        avg_trade_duration: 平均保有時間（分）
        avg_profit_per_trade: 平均利益/トレード
        avg_win: 平均利益（勝ち）
        avg_loss: 平均損失（負け）
        max_consecutive_wins: 最大連勝数
        max_consecutive_losses: 最大連敗数
        expectancy: 期待値
        risk_reward_ratio: リスクリワードレシオ
        recovery_factor: リカバリーファクター
        non_loss_rate: 非敗率（勝ち+BE / 全取引）
        daily_returns: 日次リターン
        equity_curve: エクイティカーブ
    """

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_profit: float = 0.0
    total_loss: float = 0.0
    net_profit: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    avg_trade_duration: float = 0.0
    avg_profit_per_trade: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    expectancy: float = 0.0
    risk_reward_ratio: float = 0.0
    recovery_factor: float = 0.0
    non_loss_rate: float = 0.0
    daily_returns: list[float] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換

        Returns:
            dict: 評価指標辞書
        """
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_profit": round(self.total_profit, 2),
            "total_loss": round(self.total_loss, 2),
            "net_profit": round(self.net_profit, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": (
                round(self.sharpe_ratio, 4) if self.sharpe_ratio else None
            ),
            "sortino_ratio": (
                round(self.sortino_ratio, 4) if self.sortino_ratio else None
            ),
            "avg_trade_duration": round(self.avg_trade_duration, 2),
            "avg_profit_per_trade": round(self.avg_profit_per_trade, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "max_consecutive_wins": self.max_consecutive_wins,
            "max_consecutive_losses": self.max_consecutive_losses,
            "expectancy": round(self.expectancy, 2),
            "risk_reward_ratio": round(self.risk_reward_ratio, 4),
            "recovery_factor": round(self.recovery_factor, 4),
            "non_loss_rate": round(self.non_loss_rate, 4),
        }


class MetricsCalculator:
    """バックテスト評価指標計算クラス

    トレード結果から各種評価指標を計算する。

    Attributes:
        initial_balance: 初期残高
        risk_free_rate: 無リスク金利（年率）
    """

    def __init__(
        self,
        initial_balance: float = 1_000_000.0,
        risk_free_rate: float = 0.0,
    ) -> None:
        """初期化

        Args:
            initial_balance: 初期残高
            risk_free_rate: 無リスク金利（年率）
        """
        self.initial_balance = initial_balance
        self.risk_free_rate = risk_free_rate

    def calculate(
        self,
        trades: list[Trade],
        equity_history: dict[str, float] | None = None,
    ) -> BacktestMetrics:
        """評価指標を計算

        Args:
            trades: トレードリスト
            equity_history: 日次エクイティ履歴 {日付: 評価額}

        Returns:
            BacktestMetrics: 評価指標
        """
        metrics = BacktestMetrics()

        if not trades and not equity_history:
            return metrics

        if not trades:
            # トレードがなくてもequity_historyがあればシャープレシオは計算
            if equity_history:
                metrics.daily_returns = self._calculate_daily_returns(
                    equity_history
                )
                metrics.sharpe_ratio = self._calculate_sharpe_ratio(
                    metrics.daily_returns
                )
                metrics.sortino_ratio = self._calculate_sortino_ratio(
                    metrics.daily_returns
                )
            return metrics

        # 基本統計
        metrics.total_trades = len(trades)

        wins = [t for t in trades if t.profit_loss and t.profit_loss > 0]
        losses = [t for t in trades if t.profit_loss and t.profit_loss < 0]

        metrics.winning_trades = len(wins)
        metrics.losing_trades = len(losses)

        # 勝率
        if metrics.total_trades > 0:
            metrics.win_rate = metrics.winning_trades / metrics.total_trades

        # 非敗率（勝ち + BE）
        be_trades = sum(
            1 for t in trades
            if t.exit_reason == ExitReason.BREAKEVEN
        )
        non_losses = metrics.winning_trades + be_trades
        if metrics.total_trades > 0:
            metrics.non_loss_rate = non_losses / metrics.total_trades

        # 損益計算
        metrics.total_profit = sum(
            t.profit_loss for t in wins if t.profit_loss
        )
        metrics.total_loss = abs(
            sum(t.profit_loss for t in losses if t.profit_loss)
        )
        metrics.net_profit = metrics.total_profit - metrics.total_loss

        # プロフィットファクター
        if metrics.total_loss > 0:
            metrics.profit_factor = metrics.total_profit / metrics.total_loss
        elif metrics.total_profit > 0:
            metrics.profit_factor = float("inf")

        # 平均値
        if metrics.winning_trades > 0:
            metrics.avg_win = metrics.total_profit / metrics.winning_trades
        if metrics.losing_trades > 0:
            metrics.avg_loss = metrics.total_loss / metrics.losing_trades
        if metrics.total_trades > 0:
            metrics.avg_profit_per_trade = (
                metrics.net_profit / metrics.total_trades
            )

        # リスクリワードレシオ
        if metrics.avg_loss > 0:
            metrics.risk_reward_ratio = metrics.avg_win / metrics.avg_loss

        # 期待値
        metrics.expectancy = self._calculate_expectancy(
            metrics.win_rate,
            metrics.avg_win,
            metrics.avg_loss,
        )

        # 連勝・連敗
        (
            metrics.max_consecutive_wins,
            metrics.max_consecutive_losses,
        ) = self._calculate_consecutive(trades)

        # 平均保有時間
        metrics.avg_trade_duration = self._calculate_avg_duration(trades)

        # ドローダウン
        (
            metrics.max_drawdown,
            metrics.max_drawdown_pct,
            metrics.equity_curve,
        ) = self._calculate_drawdown(trades)

        # リカバリーファクター
        if metrics.max_drawdown > 0:
            metrics.recovery_factor = metrics.net_profit / metrics.max_drawdown

        # シャープレシオ・ソルティノレシオ
        if equity_history:
            metrics.daily_returns = self._calculate_daily_returns(
                equity_history
            )
            metrics.sharpe_ratio = self._calculate_sharpe_ratio(
                metrics.daily_returns
            )
            metrics.sortino_ratio = self._calculate_sortino_ratio(
                metrics.daily_returns
            )

        return metrics

    def _calculate_expectancy(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """期待値を計算

        Args:
            win_rate: 勝率
            avg_win: 平均利益
            avg_loss: 平均損失

        Returns:
            float: 期待値
        """
        return win_rate * avg_win - (1 - win_rate) * avg_loss

    def _calculate_consecutive(
        self,
        trades: list[Trade],
    ) -> tuple[int, int]:
        """連勝・連敗を計算

        Args:
            trades: トレードリスト

        Returns:
            tuple: (最大連勝, 最大連敗)
        """
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0

        for trade in trades:
            if trade.profit_loss and trade.profit_loss > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif trade.profit_loss and trade.profit_loss < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0

        return max_wins, max_losses

    def _calculate_avg_duration(self, trades: list[Trade]) -> float:
        """平均保有時間を計算

        Args:
            trades: トレードリスト

        Returns:
            float: 平均保有時間（分）
        """
        durations = []
        for trade in trades:
            if trade.opened_at and trade.closed_at:
                duration = trade.closed_at - trade.opened_at
                durations.append(duration.total_seconds() / 60)

        if durations:
            return sum(durations) / len(durations)
        return 0.0

    def _calculate_drawdown(
        self,
        trades: list[Trade],
    ) -> tuple[float, float, list[dict[str, Any]]]:
        """ドローダウンを計算

        Args:
            trades: トレードリスト

        Returns:
            tuple: (最大DD金額, 最大DD率, エクイティカーブ)
        """
        equity = self.initial_balance
        peak = equity
        max_dd = 0.0
        max_dd_pct = 0.0
        equity_curve = []

        for trade in sorted(trades, key=lambda t: t.closed_at or t.opened_at):
            if trade.profit_loss:
                equity += trade.profit_loss

            if equity > peak:
                peak = equity

            dd = peak - equity
            dd_pct = dd / peak if peak > 0 else 0

            if dd > max_dd:
                max_dd = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

            equity_curve.append({
                "time": (
                    trade.closed_at.isoformat()
                    if trade.closed_at
                    else trade.opened_at.isoformat() if trade.opened_at else ""
                ),
                "equity": equity,
                "drawdown": dd,
                "drawdown_pct": dd_pct,
            })

        return max_dd, max_dd_pct, equity_curve

    def _calculate_daily_returns(
        self,
        equity_history: dict[str, float],
    ) -> list[float]:
        """日次リターンを計算

        Args:
            equity_history: 日次エクイティ履歴

        Returns:
            list[float]: 日次リターンリスト
        """
        dates = sorted(equity_history.keys())
        returns = []

        prev_equity = self.initial_balance
        for date in dates:
            current_equity = equity_history[date]
            if prev_equity > 0:
                daily_return = (current_equity - prev_equity) / prev_equity
                returns.append(daily_return)
            prev_equity = current_equity

        return returns

    def _calculate_sharpe_ratio(
        self,
        daily_returns: list[float],
        trading_days: int = 252,
    ) -> float | None:
        """シャープレシオを計算

        Args:
            daily_returns: 日次リターンリスト
            trading_days: 年間取引日数

        Returns:
            float | None: シャープレシオ
        """
        if len(daily_returns) < 2:
            return None

        returns_array = np.array(daily_returns)
        mean_return = np.mean(returns_array)
        std_return = np.std(returns_array, ddof=1)

        if std_return == 0:
            return None

        daily_risk_free = self.risk_free_rate / trading_days
        excess_return = mean_return - daily_risk_free

        sharpe = (excess_return / std_return) * np.sqrt(trading_days)
        return float(sharpe)

    def _calculate_sortino_ratio(
        self,
        daily_returns: list[float],
        trading_days: int = 252,
    ) -> float | None:
        """ソルティノレシオを計算

        Args:
            daily_returns: 日次リターンリスト
            trading_days: 年間取引日数

        Returns:
            float | None: ソルティノレシオ
        """
        if len(daily_returns) < 2:
            return None

        returns_array = np.array(daily_returns)
        mean_return = np.mean(returns_array)

        # 下方偏差（負のリターンのみ）
        negative_returns = returns_array[returns_array < 0]
        if len(negative_returns) == 0:
            return None

        downside_std = np.std(negative_returns, ddof=1)

        if downside_std == 0:
            return None

        daily_risk_free = self.risk_free_rate / trading_days
        excess_return = mean_return - daily_risk_free

        sortino = (excess_return / downside_std) * np.sqrt(trading_days)
        return float(sortino)

    def generate_summary(self, metrics: BacktestMetrics) -> str:
        """サマリーレポートを生成

        Args:
            metrics: 評価指標

        Returns:
            str: サマリーテキスト
        """
        lines = [
            "=" * 50,
            "バックテスト結果サマリー",
            "=" * 50,
            "",
            f"総トレード数: {metrics.total_trades}",
            f"勝ちトレード: {metrics.winning_trades}",
            f"負けトレード: {metrics.losing_trades}",
            f"勝率: {metrics.win_rate:.2%}",
            "",
            f"総利益: ¥{metrics.total_profit:,.0f}",
            f"総損失: ¥{metrics.total_loss:,.0f}",
            f"純利益: ¥{metrics.net_profit:,.0f}",
            "",
            f"プロフィットファクター: {metrics.profit_factor:.2f}",
            f"期待値: ¥{metrics.expectancy:,.0f}",
            f"リスクリワード比: {metrics.risk_reward_ratio:.2f}",
            "",
            f"最大ドローダウン: ¥{metrics.max_drawdown:,.0f}",
            f"最大ドローダウン率: {metrics.max_drawdown_pct:.2%}",
            f"リカバリーファクター: {metrics.recovery_factor:.2f}",
            "",
            f"最大連勝: {metrics.max_consecutive_wins}",
            f"最大連敗: {metrics.max_consecutive_losses}",
            f"平均保有時間: {metrics.avg_trade_duration:.0f}分",
            "",
        ]

        if metrics.sharpe_ratio is not None:
            lines.append(f"シャープレシオ: {metrics.sharpe_ratio:.2f}")
        if metrics.sortino_ratio is not None:
            lines.append(f"ソルティノレシオ: {metrics.sortino_ratio:.2f}")

        lines.append("")
        lines.append("=" * 50)

        return "\n".join(lines)

    def generate_breakdown(
        self,
        trades: list[Trade],
    ) -> dict[str, dict[str, dict[str, float]]]:
        """regime別/mode別/exit_reason別のブレークダウンを生成

        Args:
            trades: トレードリスト

        Returns:
            dict: {分類キー: {カテゴリ: {指標: 値}}}
        """
        result: dict[str, dict[str, dict[str, float]]] = {}

        for group_name, key_fn in [
            ("regime", lambda t: t.regime or "UNKNOWN"),
            ("mode", lambda t: t.mode or "UNKNOWN"),
            ("exit_reason", lambda t: (
                t.exit_reason.value if t.exit_reason else "UNKNOWN"
            )),
        ]:
            groups: dict[str, list[Trade]] = {}
            for t in trades:
                k = key_fn(t)
                if k not in groups:
                    groups[k] = []
                groups[k].append(t)

            breakdown: dict[str, dict[str, float]] = {}
            for k, group_trades in sorted(groups.items()):
                n = len(group_trades)
                wins = sum(
                    1 for t in group_trades
                    if t.profit_loss and t.profit_loss > 0
                )
                total_profit = sum(
                    t.profit_loss for t in group_trades
                    if t.profit_loss and t.profit_loss > 0
                )
                total_loss = abs(sum(
                    t.profit_loss for t in group_trades
                    if t.profit_loss and t.profit_loss < 0
                ))
                net = total_profit - total_loss
                pf = (
                    total_profit / total_loss
                    if total_loss > 0 else float("inf")
                )
                wr = wins / n if n > 0 else 0.0

                breakdown[k] = {
                    "trades": float(n),
                    "win_rate": round(wr, 4),
                    "profit_factor": round(pf, 4),
                    "net_profit": round(net, 2),
                    "avg_profit": round(net / n, 2) if n > 0 else 0.0,
                }

            result[group_name] = breakdown

        return result

    def format_breakdown(
        self,
        breakdown: dict[str, dict[str, dict[str, float]]],
    ) -> str:
        """ブレークダウンをフォーマットして文字列で返す

        Args:
            breakdown: generate_breakdownの結果

        Returns:
            str: フォーマット済みテキスト
        """
        lines: list[str] = []

        for group_name, categories in breakdown.items():
            lines.append(f"\n{'=' * 50}")
            lines.append(f"  {group_name.upper()} 別ブレークダウン")
            lines.append(f"{'=' * 50}")
            header = (
                f"{'カテゴリ':<15} {'取引数':>6} {'勝率':>7} "
                f"{'PF':>7} {'純利益':>12} {'平均':>10}"
            )
            lines.append(header)
            lines.append("-" * 60)

            for category, stats in categories.items():
                line = (
                    f"{category:<15} "
                    f"{int(stats['trades']):>6} "
                    f"{stats['win_rate']:>6.1%} "
                    f"{stats['profit_factor']:>7.2f} "
                    f"¥{stats['net_profit']:>11,.0f} "
                    f"¥{stats['avg_profit']:>9,.0f}"
                )
                lines.append(line)

        return "\n".join(lines)
