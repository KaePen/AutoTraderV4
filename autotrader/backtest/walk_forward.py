"""Walk-Forward検証インフラ

過学習を検出し、堅牢なパラメータ最適化を実現する。
In-Sample (IS) と Out-of-Sample (OOS) の分割検証を提供。

年単位ローリングウォークフォワード検証:
- RollingWalkForwardValidator: 年単位IS/OOSローリング検証
- ParameterStabilityTest: 最適値近傍での安定性検証
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


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


# ============================================================
# 年単位ローリングウォークフォワード検証
# ============================================================


@dataclass(frozen=True)
class WalkForwardWindow:
    """ウォークフォワードのIS/OOS期間（年単位）

    Attributes:
        is_start_year: IS開始年
        is_end_year: IS終了年
        oos_year: OOS年
    """

    is_start_year: int
    is_end_year: int
    oos_year: int

    @property
    def label(self) -> str:
        """ウィンドウラベル"""
        return (
            f"IS:{self.is_start_year}-{self.is_end_year}"
            f"_OOS:{self.oos_year}"
        )


@dataclass
class RollingWFResult:
    """1ウィンドウの検証結果

    Attributes:
        window: ウィンドウ定義
        is_metrics: IS期間のメトリクス
        oos_metrics: OOS期間のメトリクス
    """

    window: WalkForwardWindow
    is_metrics: dict[str, float]
    oos_metrics: dict[str, float]

    @property
    def oos_profit(self) -> float:
        """OOS期間の総利益"""
        return self.oos_metrics.get("total_profit", 0.0)

    @property
    def oos_sharpe(self) -> float:
        """OOS期間のシャープレシオ"""
        return self.oos_metrics.get("sharpe_ratio", 0.0)

    @property
    def degradation_pct(self) -> float:
        """IS→OOS のパフォーマンス劣化率（%）"""
        is_profit = self.is_metrics.get("total_profit", 0.0)
        if is_profit <= 0:
            return 0.0
        return (
            (is_profit - self.oos_profit) / is_profit * 100
        )


@dataclass
class RollingWFReport:
    """全ウィンドウの集計結果

    Attributes:
        results: 各ウィンドウの結果リスト
    """

    results: list[RollingWFResult] = field(
        default_factory=list
    )

    @property
    def avg_oos_profit(self) -> float:
        """OOS利益の平均"""
        if not self.results:
            return 0.0
        return sum(
            r.oos_profit for r in self.results
        ) / len(self.results)

    @property
    def avg_degradation(self) -> float:
        """平均劣化率（%）"""
        if not self.results:
            return 0.0
        return sum(
            r.degradation_pct for r in self.results
        ) / len(self.results)

    @property
    def all_oos_profitable(self) -> bool:
        """全OOS期間が黒字か"""
        return all(
            r.oos_profit > 0 for r in self.results
        )

    def summary(self) -> str:
        """サマリーレポートを文字列で返す

        Returns:
            str: レポート文字列
        """
        lines = ["=== Walk-Forward Validation Report ==="]
        for r in self.results:
            is_profit = r.is_metrics.get(
                "total_profit", 0
            )
            lines.append(
                f"{r.window.label}: "
                f"IS={is_profit:+,.0f} "
                f"OOS={r.oos_profit:+,.0f} "
                f"Degradation={r.degradation_pct:.1f}%"
            )
        lines.append("")
        lines.append(
            f"Avg OOS Profit: {self.avg_oos_profit:+,.0f}"
        )
        lines.append(
            f"Avg Degradation: {self.avg_degradation:.1f}%"
        )
        lines.append(
            f"All OOS Profitable: "
            f"{self.all_oos_profitable}"
        )
        return "\n".join(lines)


class RollingWalkForwardValidator:
    """年単位ローリングウォークフォワード検証

    IS期間（複数年）で学習し、OOS期間（1年）で検証する
    ローリング方式のウォークフォワード分析を実行。

    Args:
        symbol: 通貨ペア名
        is_years: IS期間の年数（デフォルト3）
        oos_years: OOS期間の年数（デフォルト1）
        start_year: データ開始年（デフォルト2020）
        end_year: データ終了年（デフォルト2025）
    """

    def __init__(
        self,
        symbol: str,
        is_years: int = 3,
        oos_years: int = 1,
        start_year: int = 2020,
        end_year: int = 2025,
    ) -> None:
        self.symbol = symbol
        self.is_years = is_years
        self.oos_years = oos_years
        self.start_year = start_year
        self.end_year = end_year

    def generate_windows(self) -> list[WalkForwardWindow]:
        """ローリングウィンドウを生成

        Returns:
            list[WalkForwardWindow]: ウィンドウリスト
        """
        windows: list[WalkForwardWindow] = []
        is_start = self.start_year
        while (
            is_start + self.is_years + self.oos_years - 1
            <= self.end_year
        ):
            is_end = is_start + self.is_years - 1
            oos_year = is_end + 1
            windows.append(
                WalkForwardWindow(
                    is_start, is_end, oos_year
                )
            )
            # 1年ずつスライド
            is_start += self.oos_years
        return windows

    def run(
        self,
        backtest_fn: Callable[
            [str, int, int], dict[str, float]
        ],
    ) -> RollingWFReport:
        """検証実行

        Args:
            backtest_fn: バックテスト実行関数
                signature: (symbol, start_year, end_year)
                    -> dict[str, float]
                返すdict例:
                    {"total_profit": float,
                     "win_rate": float,
                     "profit_factor": float,
                     "sharpe_ratio": float, ...}

        Returns:
            RollingWFReport: 全ウィンドウの集計結果
        """
        report = RollingWFReport()
        windows = self.generate_windows()

        for window in windows:
            logger.info(
                "ウォークフォワード実行: %s",
                window.label,
            )

            # IS期間のバックテスト
            is_metrics = backtest_fn(
                self.symbol,
                window.is_start_year,
                window.is_end_year,
            )

            # OOS期間のバックテスト（同じ設定）
            oos_metrics = backtest_fn(
                self.symbol,
                window.oos_year,
                window.oos_year,
            )

            result = RollingWFResult(
                window, is_metrics, oos_metrics
            )
            report.results.append(result)
            logger.info(
                "%s: IS=%+.0f, OOS=%+.0f, "
                "Degradation=%.1f%%",
                window.label,
                is_metrics.get("total_profit", 0),
                result.oos_profit,
                result.degradation_pct,
            )

        return report


# ============================================================
# パラメータ安定性テスト
# ============================================================


@dataclass
class StabilityResult:
    """安定性テスト1バリエーションの結果

    Attributes:
        multiplier: ベース値に対する倍率
        actual_value: 実際のパラメータ値
        metrics: バックテスト結果メトリクス
    """

    multiplier: float
    actual_value: float
    metrics: dict[str, float]


@dataclass
class StabilityReport:
    """パラメータ安定性テストの集計結果

    Attributes:
        param_name: テスト対象パラメータ名
        base_value: ベース値
        results: 各バリエーションの結果
    """

    param_name: str
    base_value: float
    results: list[StabilityResult] = field(
        default_factory=list
    )

    @property
    def profit_range(self) -> float:
        """利益の最大-最小幅"""
        if not self.results:
            return 0.0
        profits = [
            r.metrics.get("total_profit", 0.0)
            for r in self.results
        ]
        return max(profits) - min(profits)

    @property
    def is_stable(self) -> bool:
        """全バリエーションが黒字かどうか"""
        return all(
            r.metrics.get("total_profit", 0.0) > 0
            for r in self.results
        )

    def summary(self) -> str:
        """サマリーレポートを文字列で返す

        Returns:
            str: レポート文字列
        """
        lines = [
            f"=== Parameter Stability: "
            f"{self.param_name} "
            f"(base={self.base_value}) ==="
        ]
        for r in self.results:
            profit = r.metrics.get("total_profit", 0)
            lines.append(
                f"  x{r.multiplier:.2f} "
                f"(value={r.actual_value:.4f}): "
                f"Profit={profit:+,.0f}"
            )
        lines.append(
            f"  Profit Range: {self.profit_range:,.0f}"
        )
        lines.append(f"  All Profitable: {self.is_stable}")
        return "\n".join(lines)


class ParameterStabilityTest:
    """最適値近傍でのパラメータ安定性テスト

    最適パラメータの値を一定範囲で変動させ、
    性能への影響を検証する。

    Args:
        base_config: ベースとなる設定辞書
        param_name: テスト対象のパラメータ名
        variations: 倍率リスト
            （例: [0.9, 0.95, 1.0, 1.05, 1.1]）
    """

    # 倍率±10%のデフォルトバリエーション
    DEFAULT_VARIATIONS = [0.90, 0.95, 1.0, 1.05, 1.10]

    def __init__(
        self,
        base_config: dict[str, Any],
        param_name: str,
        variations: list[float] | None = None,
    ) -> None:
        self.base_config = base_config
        self.param_name = param_name
        self.variations = (
            variations or self.DEFAULT_VARIATIONS
        )

    def run(
        self,
        backtest_fn: Callable[
            [dict[str, Any]], dict[str, float]
        ],
    ) -> StabilityReport:
        """安定性テスト実行

        Args:
            backtest_fn: バックテスト実行関数
                signature: (config_dict) -> dict[str, float]
                返すdict例:
                    {"total_profit": float, ...}

        Returns:
            StabilityReport: 安定性テスト結果
        """
        base_value = self.base_config[self.param_name]
        report = StabilityReport(
            param_name=self.param_name,
            base_value=base_value,
        )

        for mult in self.variations:
            varied_value = base_value * mult
            varied_config = {
                **self.base_config,
                self.param_name: varied_value,
            }

            logger.info(
                "安定性テスト %s: x%.2f (%.4f)",
                self.param_name,
                mult,
                varied_value,
            )

            metrics = backtest_fn(varied_config)
            report.results.append(
                StabilityResult(
                    multiplier=mult,
                    actual_value=varied_value,
                    metrics=metrics,
                )
            )

        return report
