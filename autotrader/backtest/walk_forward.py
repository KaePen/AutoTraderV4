"""Walk-Forward検証インフラ

過学習を検出し、堅牢なパラメータ最適化を実現する。
In-Sample (IS) と Out-of-Sample (OOS) の分割検証を提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Any, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class WalkForwardConfig:
    """Walk-Forward検証設定

    Attributes:
        is_ratio: In-Sampleの比率（デフォルト0.7=70%）
        oos_ratio: Out-of-Sampleの比率（デフォルト0.3=30%）
        window_months: ウィンドウ期間（月数）
        step_months: スライド幅（月数）
        min_trades_per_period: 期間あたり最小トレード数
        overfitting_winrate_diff: 過学習警告の勝率差閾値
        overfitting_pf_degradation: 過学習警告のPF劣化閾値
    """

    is_ratio: float = 0.7
    oos_ratio: float = 0.3
    window_months: int = 12
    step_months: int = 3
    min_trades_per_period: int = 30
    overfitting_winrate_diff: float = 0.10  # 10%
    overfitting_pf_degradation: float = 0.30  # 30%

    @classmethod
    def default(cls) -> WalkForwardConfig:
        """デフォルト設定"""
        return cls()

    @classmethod
    def conservative(cls) -> WalkForwardConfig:
        """保守的設定（短いOOS期間）"""
        return cls(
            is_ratio=0.6,
            oos_ratio=0.4,
            window_months=18,
            step_months=6,
        )


@dataclass
class PeriodMetrics:
    """期間別パフォーマンス指標

    Attributes:
        start_date: 開始日
        end_date: 終了日
        total_trades: トレード数
        win_rate: 勝率
        profit_factor: プロフィットファクター
        total_profit: 総利益
        max_drawdown: 最大ドローダウン
        avg_profit_per_trade: 1トレードあたり平均利益
    """

    start_date: datetime
    end_date: datetime
    total_trades: int
    win_rate: float
    profit_factor: float
    total_profit: float
    max_drawdown: float
    avg_profit_per_trade: float = 0.0


@dataclass
class WalkForwardPeriod:
    """Walk-Forwardの1期間

    Attributes:
        period_id: 期間ID
        is_start: In-Sample開始日
        is_end: In-Sample終了日
        oos_start: Out-of-Sample開始日
        oos_end: Out-of-Sample終了日
        is_metrics: In-Sample指標
        oos_metrics: Out-of-Sample指標
        optimized_params: 最適化されたパラメータ
    """

    period_id: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    is_metrics: PeriodMetrics | None = None
    oos_metrics: PeriodMetrics | None = None
    optimized_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OverfittingWarning:
    """過学習警告

    Attributes:
        period_id: 対象期間ID
        warning_type: 警告種別
        is_value: In-Sample値
        oos_value: Out-of-Sample値
        difference: 差分/劣化率
        message: 警告メッセージ
    """

    period_id: int
    warning_type: str
    is_value: float
    oos_value: float
    difference: float
    message: str


@dataclass
class WalkForwardResult:
    """Walk-Forward検証結果

    Attributes:
        config: 検証設定
        periods: 各期間の結果
        aggregate_is_metrics: 集計IS指標
        aggregate_oos_metrics: 集計OOS指標
        overfitting_warnings: 過学習警告リスト
        is_robust: ロバスト性判定（過学習警告なし）
    """

    config: WalkForwardConfig
    periods: list[WalkForwardPeriod]
    aggregate_is_metrics: PeriodMetrics | None = None
    aggregate_oos_metrics: PeriodMetrics | None = None
    overfitting_warnings: list[OverfittingWarning] = field(
        default_factory=list
    )
    is_robust: bool = True


class WalkForwardValidator:
    """Walk-Forward検証器

    過学習を検出するためのIS/OOS分割検証を実行。

    Args:
        config: 検証設定
    """

    def __init__(
        self, config: WalkForwardConfig | None = None
    ) -> None:
        self.config = config or WalkForwardConfig.default()
        self._periods: list[WalkForwardPeriod] = []

    def generate_periods(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[WalkForwardPeriod]:
        """検証期間を生成

        Args:
            start_date: データ開始日
            end_date: データ終了日

        Returns:
            list[WalkForwardPeriod]: 期間リスト
        """
        periods: list[WalkForwardPeriod] = []
        period_id = 0
        current_start = start_date

        window_days = self.config.window_months * 30
        step_days = self.config.step_months * 30
        is_days = int(window_days * self.config.is_ratio)

        while current_start + timedelta(days=window_days) <= end_date:
            is_start = current_start
            is_end = current_start + timedelta(days=is_days)
            oos_start = is_end
            oos_end = current_start + timedelta(days=window_days)

            period = WalkForwardPeriod(
                period_id=period_id,
                is_start=is_start,
                is_end=is_end,
                oos_start=oos_start,
                oos_end=oos_end,
            )
            periods.append(period)

            current_start += timedelta(days=step_days)
            period_id += 1

        self._periods = periods
        return periods

    def calculate_metrics(
        self, trades: list[dict[str, Any]]
    ) -> PeriodMetrics | None:
        """トレードリストから指標を計算

        Args:
            trades: トレードリスト（dict形式）

        Returns:
            PeriodMetrics | None: 計算された指標
        """
        if not trades:
            return None

        wins = [t for t in trades if t.get("profit_loss", 0) > 0]
        losses = [t for t in trades if t.get("profit_loss", 0) <= 0]

        total_trades = len(trades)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0

        # プロフィットファクター
        gross_profit = sum(t.get("profit_loss", 0) for t in wins)
        gross_loss = abs(sum(t.get("profit_loss", 0) for t in losses))
        profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )

        total_profit = sum(t.get("profit_loss", 0) for t in trades)
        avg_profit = total_profit / total_trades if total_trades > 0 else 0.0

        # 簡易ドローダウン計算
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(trades, key=lambda x: x.get("closed_at", "")):
            equity += t.get("profit_loss", 0)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # 日付を取得
        dates = [t.get("closed_at") for t in trades if t.get("closed_at")]
        start = min(dates) if dates else datetime.now()
        end = max(dates) if dates else datetime.now()

        return PeriodMetrics(
            start_date=start,
            end_date=end,
            total_trades=total_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_profit=total_profit,
            max_drawdown=max_dd,
            avg_profit_per_trade=avg_profit,
        )

    def check_overfitting(
        self,
        is_metrics: PeriodMetrics,
        oos_metrics: PeriodMetrics,
        period_id: int,
    ) -> list[OverfittingWarning]:
        """過学習をチェック

        Args:
            is_metrics: In-Sample指標
            oos_metrics: Out-of-Sample指標
            period_id: 期間ID

        Returns:
            list[OverfittingWarning]: 警告リスト
        """
        warnings: list[OverfittingWarning] = []

        # 勝率差チェック
        winrate_diff = is_metrics.win_rate - oos_metrics.win_rate
        if winrate_diff > self.config.overfitting_winrate_diff:
            warnings.append(
                OverfittingWarning(
                    period_id=period_id,
                    warning_type="winrate_degradation",
                    is_value=is_metrics.win_rate,
                    oos_value=oos_metrics.win_rate,
                    difference=winrate_diff,
                    message=(
                        f"勝率劣化: IS {is_metrics.win_rate:.1%} → "
                        f"OOS {oos_metrics.win_rate:.1%} "
                        f"（差 {winrate_diff:.1%}）"
                    ),
                )
            )

        # PF劣化チェック
        if is_metrics.profit_factor > 0:
            pf_degradation = 1 - (
                oos_metrics.profit_factor / is_metrics.profit_factor
            )
            if pf_degradation > self.config.overfitting_pf_degradation:
                warnings.append(
                    OverfittingWarning(
                        period_id=period_id,
                        warning_type="pf_degradation",
                        is_value=is_metrics.profit_factor,
                        oos_value=oos_metrics.profit_factor,
                        difference=pf_degradation,
                        message=(
                            f"PF劣化: IS {is_metrics.profit_factor:.2f} → "
                            f"OOS {oos_metrics.profit_factor:.2f} "
                            f"（劣化 {pf_degradation:.0%}）"
                        ),
                    )
                )

        return warnings

    def validate(
        self,
        data: pd.DataFrame,
        backtest_func: Callable[
            [pd.DataFrame, dict[str, Any]], list[dict[str, Any]]
        ],
        optimize_func: Callable[
            [pd.DataFrame], dict[str, Any]
        ] | None = None,
        base_params: dict[str, Any] | None = None,
    ) -> WalkForwardResult:
        """Walk-Forward検証を実行

        Args:
            data: 価格データ（time列またはDatetimeIndex必須）
            backtest_func: バックテスト関数（data, params -> trades）
            optimize_func: 最適化関数（data -> params）、任意
            base_params: ベースパラメータ

        Returns:
            WalkForwardResult: 検証結果
        """
        # 日付範囲を取得
        if "time" in data.columns:
            start_date = data["time"].min()
            end_date = data["time"].max()
        else:
            start_date = data.index.min()
            end_date = data.index.max()

        # 期間を生成
        periods = self.generate_periods(start_date, end_date)

        all_warnings: list[OverfittingWarning] = []
        all_is_trades: list[dict[str, Any]] = []
        all_oos_trades: list[dict[str, Any]] = []

        for period in periods:
            # データ分割
            if "time" in data.columns:
                is_data = data[
                    (data["time"] >= period.is_start)
                    & (data["time"] < period.is_end)
                ]
                oos_data = data[
                    (data["time"] >= period.oos_start)
                    & (data["time"] < period.oos_end)
                ]
            else:
                is_data = data[
                    (data.index >= period.is_start)
                    & (data.index < period.is_end)
                ]
                oos_data = data[
                    (data.index >= period.oos_start)
                    & (data.index < period.oos_end)
                ]

            # パラメータ最適化（IS期間）
            if optimize_func is not None:
                params = optimize_func(is_data)
            else:
                params = base_params or {}

            period.optimized_params = params

            # ISバックテスト
            is_trades = backtest_func(is_data, params)
            period.is_metrics = self.calculate_metrics(is_trades)
            all_is_trades.extend(is_trades)

            # OOSバックテスト
            oos_trades = backtest_func(oos_data, params)
            period.oos_metrics = self.calculate_metrics(oos_trades)
            all_oos_trades.extend(oos_trades)

            # 過学習チェック
            if period.is_metrics and period.oos_metrics:
                warnings = self.check_overfitting(
                    period.is_metrics,
                    period.oos_metrics,
                    period.period_id,
                )
                all_warnings.extend(warnings)

        # 集計指標
        aggregate_is = self.calculate_metrics(all_is_trades)
        aggregate_oos = self.calculate_metrics(all_oos_trades)

        # ロバスト性判定
        is_robust = len(all_warnings) == 0

        return WalkForwardResult(
            config=self.config,
            periods=periods,
            aggregate_is_metrics=aggregate_is,
            aggregate_oos_metrics=aggregate_oos,
            overfitting_warnings=all_warnings,
            is_robust=is_robust,
        )

    def print_report(self, result: WalkForwardResult) -> str:
        """検証結果レポートを生成

        Args:
            result: 検証結果

        Returns:
            str: レポート文字列
        """
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("Walk-Forward検証レポート")
        lines.append("=" * 60)

        # 設定
        lines.append(f"\n設定:")
        lines.append(f"  IS比率: {result.config.is_ratio:.0%}")
        lines.append(f"  OOS比率: {result.config.oos_ratio:.0%}")
        lines.append(f"  ウィンドウ: {result.config.window_months}ヶ月")
        lines.append(f"  ステップ: {result.config.step_months}ヶ月")
        lines.append(f"  期間数: {len(result.periods)}")

        # 集計結果
        lines.append(f"\n集計結果:")
        if result.aggregate_is_metrics:
            m = result.aggregate_is_metrics
            lines.append(f"  In-Sample:")
            lines.append(f"    トレード数: {m.total_trades}")
            lines.append(f"    勝率: {m.win_rate:.1%}")
            lines.append(f"    PF: {m.profit_factor:.2f}")
            lines.append(f"    総利益: ¥{m.total_profit:,.0f}")

        if result.aggregate_oos_metrics:
            m = result.aggregate_oos_metrics
            lines.append(f"  Out-of-Sample:")
            lines.append(f"    トレード数: {m.total_trades}")
            lines.append(f"    勝率: {m.win_rate:.1%}")
            lines.append(f"    PF: {m.profit_factor:.2f}")
            lines.append(f"    総利益: ¥{m.total_profit:,.0f}")

        # 過学習警告
        if result.overfitting_warnings:
            lines.append(f"\n⚠️ 過学習警告 ({len(result.overfitting_warnings)}件):")
            for w in result.overfitting_warnings:
                lines.append(f"  期間{w.period_id}: {w.message}")
        else:
            lines.append(f"\n✅ 過学習警告なし")

        # ロバスト性判定
        status = "✅ ロバスト" if result.is_robust else "⚠️ 要確認"
        lines.append(f"\n判定: {status}")
        lines.append("=" * 60)

        return "\n".join(lines)


def create_walk_forward_periods(
    start_year: int,
    end_year: int,
    is_years: int = 3,
    oos_years: int = 1,
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """年単位のWalk-Forward期間を生成

    Args:
        start_year: 開始年
        end_year: 終了年
        is_years: IS期間（年）
        oos_years: OOS期間（年）

    Returns:
        list: ((is_start, is_end), (oos_start, oos_end))のリスト
    """
    periods = []
    window = is_years + oos_years
    current = start_year

    while current + window <= end_year + 1:
        is_start = current
        is_end = current + is_years
        oos_start = is_end
        oos_end = current + window

        periods.append(
            ((is_start, is_end), (oos_start, oos_end))
        )

        current += oos_years

    return periods
