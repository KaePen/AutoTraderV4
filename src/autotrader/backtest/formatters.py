"""結果フォーマッタモジュール

CLI/WebUI向け出力フォーマットを提供。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from autotrader.backtest.config import (
    UnifiedBacktestConfig,
    UnifiedBacktestResult,
    BacktestMetrics,
    MonthlyResult,
    YearlyResult,
)


class ResultFormatter(ABC):
    """結果フォーマッタ基底クラス

    バックテスト結果の出力フォーマットを抽象化。
    """

    @abstractmethod
    def format_header(self, config: UnifiedBacktestConfig) -> str:
        """ヘッダーをフォーマット

        Args:
            config: バックテスト設定

        Returns:
            str: フォーマット済みヘッダー
        """
        pass

    @abstractmethod
    def format_metrics(self, metrics: BacktestMetrics) -> str:
        """評価指標をフォーマット

        Args:
            metrics: 評価指標

        Returns:
            str: フォーマット済み指標
        """
        pass

    @abstractmethod
    def format_monthly(self, results: list[MonthlyResult]) -> str:
        """月別結果をフォーマット

        Args:
            results: 月別結果リスト

        Returns:
            str: フォーマット済み月別結果
        """
        pass

    @abstractmethod
    def format_yearly(self, results: list[YearlyResult]) -> str:
        """年別結果をフォーマット

        Args:
            results: 年別結果リスト

        Returns:
            str: フォーマット済み年別結果
        """
        pass

    def format_full_result(self, result: UnifiedBacktestResult) -> str:
        """完全な結果をフォーマット

        Args:
            result: バックテスト結果

        Returns:
            str: フォーマット済み結果
        """
        parts = [
            self.format_header(result.config),
            self.format_metrics(result.metrics),
        ]

        if result.yearly_results:
            parts.append(self.format_yearly(result.yearly_results))

        if result.monthly_results:
            parts.append(self.format_monthly(result.monthly_results))

        return "\n".join(parts)


class CLIFormatter(ResultFormatter):
    """CLI用フォーマッタ

    ターミナル出力用のプレーンテキストフォーマット。
    """

    def __init__(self, width: int = 80):
        """初期化

        Args:
            width: 出力幅
        """
        self._width = width

    def format_header(self, config: UnifiedBacktestConfig) -> str:
        """ヘッダーをフォーマット"""
        lines = [
            "=" * self._width,
            "AutoTraderV4 バックテスト",
            "=" * self._width,
            f"シンボル: {config.symbol}",
            f"期間: {config.start_year}-{config.end_year}",
            f"初期残高: ¥{config.initial_balance:,.0f}",
            f"プリセット: {config.preset.value}",
            f"コンセンサス: {config.consensus.value}",
            "=" * self._width,
        ]
        return "\n".join(lines)

    def format_metrics(self, metrics: BacktestMetrics) -> str:
        """評価指標をフォーマット"""
        lines = [
            "",
            "-" * self._width,
            "バックテスト結果",
            "-" * self._width,
            f"総取引数: {metrics.total_trades}",
            f"勝率: {metrics.win_rate * 100:.1f}%",
            f"プロフィットファクター: {metrics.profit_factor:.2f}",
            f"純利益: ¥{metrics.net_profit:+,.0f}",
            f"最大ドローダウン: {metrics.max_drawdown_pct * 100:.2f}%",
        ]

        if metrics.sharpe_ratio is not None:
            lines.append(f"シャープレシオ: {metrics.sharpe_ratio:.2f}")

        if metrics.sortino_ratio is not None:
            lines.append(f"ソルティノレシオ: {metrics.sortino_ratio:.2f}")

        if metrics.annual_return:
            lines.append(f"年間平均収益率: {metrics.annual_return * 100:.1f}%")

        return "\n".join(lines)

    def format_monthly(self, results: list[MonthlyResult]) -> str:
        """月別結果をフォーマット"""
        if not results:
            return ""

        lines = [
            "",
            "=" * self._width,
            "月別サマリー",
            "=" * self._width,
        ]

        # 統計計算
        total_months = len(results)
        positive_months = sum(1 for r in results if r.profit > 0)
        target_months = sum(1 for r in results if r.profit_pct >= 0.05)
        avg_return = sum(r.profit_pct for r in results) / total_months

        lines.extend([
            f"月間プラス率: {positive_months}/{total_months} "
            f"({100*positive_months/total_months:.1f}%)",
            f"月間+5%達成: {target_months}/{total_months} "
            f"({100*target_months/total_months:.1f}%)",
            f"月間平均収益率: {avg_return*100:.2f}%",
        ])

        # 直近12ヶ月
        if len(results) >= 12:
            recent = results[-12:]
            recent_target = sum(1 for r in recent if r.profit_pct >= 0.05)
            recent_avg = sum(r.profit_pct for r in recent) / 12
            lines.extend([
                "",
                "直近12ヶ月:",
                f"  +5%達成: {recent_target}/12",
                f"  平均収益率: {recent_avg*100:.2f}%",
            ])

        return "\n".join(lines)

    def format_yearly(self, results: list[YearlyResult]) -> str:
        """年別結果をフォーマット"""
        if not results:
            return ""

        lines = [
            "",
            "=" * self._width,
            "年別詳細",
            "=" * self._width,
            f"{'年':<6} {'取引数':>8} {'勝率':>8} {'PF':>8} "
            f"{'利益':>14} {'DD':>8}",
            "-" * self._width,
        ]

        for r in results:
            lines.append(
                f"{r.year:<6} "
                f"{r.trades:>8} "
                f"{r.win_rate:>7.1f}% "
                f"{r.profit_pct if r.profit_pct else 0:>7.2f} "
                f"¥{r.profit:>+12,.0f} "
                f"{r.max_drawdown_pct*100:>7.2f}%"
            )

        lines.append("-" * self._width)
        profitable = sum(1 for r in results if r.profit > 0)
        lines.append(f"黒字年: {profitable}/{len(results)}")

        return "\n".join(lines)

    def format_comparison(self, results: list[dict[str, Any]]) -> str:
        """コンセンサスルール比較をフォーマット

        Args:
            results: 比較結果リスト

        Returns:
            str: フォーマット済み比較結果
        """
        lines = [
            "",
            "=" * self._width,
            "コンセンサスルール比較サマリー",
            "=" * self._width,
            f"{'ルール':<12} {'取引数':>8} {'勝率':>8} "
            f"{'PF':>8} {'年間収益率':>10}",
            "-" * 60,
        ]

        for r in results:
            lines.append(
                f"{r['consensus']:<12} {r['trades']:>8} "
                f"{r['win_rate']:>7.1f}% {r['profit_factor']:>8.2f} "
                f"{r['annual_return']:>9.1f}%"
            )

        return "\n".join(lines)

    def format_walk_forward(self, results: list[dict[str, Any]]) -> str:
        """ウォークフォワード結果をフォーマット

        Args:
            results: ウォークフォワード結果リスト

        Returns:
            str: フォーマット済み結果
        """
        lines = [
            "",
            "=" * self._width,
            "ウォークフォワード検証結果",
            "=" * self._width,
            f"{'訓練期間':<12} {'検証期間':<12} "
            f"{'訓練収益':>10} {'検証収益':>10} {'取引数':>8}",
            "-" * self._width,
        ]

        overfit_count = 0
        for r in results:
            lines.append(
                f"{r['train_period']:<12} "
                f"{r['valid_period']:<12} "
                f"{r['train_return']:>9.1f}% "
                f"{r['valid_return']:>9.1f}% "
                f"{r['valid_trades']:>8}"
            )

            # オーバーフィット判定
            if r["train_return"] > 0 and r["valid_return"] < 0:
                overfit_count += 1
            elif (
                r["train_return"] > 0
                and r["valid_return"] < r["train_return"] * 0.5
            ):
                overfit_count += 1

        lines.append("-" * self._width)

        if overfit_count > len(results) * 0.5:
            lines.append("警告: オーバーフィットの可能性があります")
        else:
            lines.append("検証期間でも安定したパフォーマンス")

        return "\n".join(lines)


class JSONFormatter(ResultFormatter):
    """WebUI用JSONフォーマッタ

    APIレスポンス用のJSON形式。
    """

    def format_header(self, config: UnifiedBacktestConfig) -> str:
        """ヘッダーをJSON形式で返す（実際は辞書を返す）"""
        return ""

    def format_metrics(self, metrics: BacktestMetrics) -> str:
        """評価指標をJSON形式で返す（実際は辞書を返す）"""
        return ""

    def format_monthly(self, results: list[MonthlyResult]) -> str:
        """月別結果をJSON形式で返す（実際は辞書を返す）"""
        return ""

    def format_yearly(self, results: list[YearlyResult]) -> str:
        """年別結果をJSON形式で返す（実際は辞書を返す）"""
        return ""

    def to_dict(self, result: UnifiedBacktestResult) -> dict[str, Any]:
        """結果を辞書形式に変換

        Args:
            result: バックテスト結果

        Returns:
            dict: JSON互換辞書
        """
        return result.to_dict()

    def format_full_result(self, result: UnifiedBacktestResult) -> str:
        """完全な結果をJSON文字列で返す

        Args:
            result: バックテスト結果

        Returns:
            str: JSON文字列
        """
        import json
        return json.dumps(self.to_dict(result), ensure_ascii=False, indent=2)


class CompactFormatter(ResultFormatter):
    """コンパクトフォーマッタ

    ログ出力や簡易表示用の1行フォーマット。
    """

    def format_header(self, config: UnifiedBacktestConfig) -> str:
        """ヘッダーをフォーマット"""
        return (
            f"[{config.symbol}] {config.start_year}-{config.end_year} "
            f"preset={config.preset.value}"
        )

    def format_metrics(self, metrics: BacktestMetrics) -> str:
        """評価指標をフォーマット"""
        return (
            f"trades={metrics.total_trades} "
            f"WR={metrics.win_rate*100:.1f}% "
            f"PF={metrics.profit_factor:.2f} "
            f"profit=¥{metrics.net_profit:+,.0f} "
            f"DD={metrics.max_drawdown_pct*100:.1f}%"
        )

    def format_monthly(self, results: list[MonthlyResult]) -> str:
        """月別結果をフォーマット"""
        if not results:
            return ""
        positive = sum(1 for r in results if r.profit > 0)
        return f"monthly_positive={positive}/{len(results)}"

    def format_yearly(self, results: list[YearlyResult]) -> str:
        """年別結果をフォーマット"""
        if not results:
            return ""
        profitable = sum(1 for r in results if r.profit > 0)
        return f"profitable_years={profitable}/{len(results)}"

    def format_full_result(self, result: UnifiedBacktestResult) -> str:
        """完全な結果をフォーマット"""
        parts = [
            self.format_header(result.config),
            self.format_metrics(result.metrics),
        ]

        if result.yearly_results:
            parts.append(self.format_yearly(result.yearly_results))

        if result.monthly_results:
            parts.append(self.format_monthly(result.monthly_results))

        return " | ".join(parts)
