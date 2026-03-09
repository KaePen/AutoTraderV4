"""統一バックテスト実行エンジン

CLIとWebUIの両方から使用可能な統一インターフェースを提供。
並列処理オプションとマルチモードトレードをサポート。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from autotrader.config import DEFAULT_TRADING_PARAMS

if TYPE_CHECKING:
    from autotrader.backtest.events import BacktestEventEmitter
    from autotrader.backtest.runner import BacktestResult


@dataclass
class ExecutorConfig:
    """統一実行設定

    CLIとWebUIの両方から同一の設定形式で使用可能。

    Attributes:
        start_year: 開始年
        end_year: 終了年
        start_date: 開始日時（年指定より優先）
        end_date: 終了日時（年指定より優先）
        initial_balance: 初期残高
        volume: 取引ボリューム
        symbol: 通貨ペア
        data_dir: データディレクトリ
        use_short_timeframe: 短い時間足（M5）を基準として使用
        parallel_years: 年並列処理有効化
        max_workers: 並列ワーカー数（Noneで自動）
        max_positions: 最大ポジション数
        spread_pips: スプレッド（pips）
        pip_value: pip価値
        verbose: 詳細出力
    """

    start_year: int = 2020
    end_year: int = 2024
    start_date: datetime | None = None
    end_date: datetime | None = None
    initial_balance: float = 1_000_000.0
    volume: float = 1.0
    symbol: str = "USDJPY"
    data_dir: str = "data/csv"
    use_short_timeframe: bool = True
    parallel_years: bool = True
    max_workers: int | None = None
    max_positions: int = 1
    spread_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.spread_pips
    )
    pip_value: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.pip_value
    )
    verbose: bool = False
    # ファンダメンタル統合（デフォルトOFF）
    fundamental_csv: str | None = None  # CSVパス（Noneで無効）
    fundamental_guard_minutes: int = 30  # 指標前停止分数

    def get_years(self) -> list[int]:
        """対象年リストを取得

        Returns:
            list[int]: 対象年のリスト
        """
        return list(range(self.start_year, self.end_year + 1))


@dataclass
class ExecutorResult:
    """統一実行結果

    全実行モードで共通の結果フォーマット。

    Attributes:
        trades: 総取引数
        win_rate: 勝率（%）
        profit_factor: プロフィットファクター
        net_profit: 純利益
        max_drawdown: 最大ドローダウン（%）
        sharpe_ratio: シャープレシオ
        annual_return: 年間平均収益率（%）
        monthly_results: 月別結果
        yearly_results: 年別結果
        mode_results: モード別結果（マルチモード時）
        execution_time: 実行時間（秒）
        cancelled: キャンセルされたか
    """

    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_profit: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    annual_return: float = 0.0
    monthly_results: list[dict] = field(default_factory=list)
    yearly_results: list[dict] = field(default_factory=list)
    mode_results: dict[str, dict] = field(default_factory=dict)
    execution_time: float = 0.0
    cancelled: bool = False

    def to_dict(self) -> dict:
        """辞書形式に変換

        Returns:
            dict: 結果辞書
        """
        return {
            "trades": self.trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "net_profit": self.net_profit,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "annual_return": self.annual_return,
            "monthly_results": self.monthly_results,
            "yearly_results": self.yearly_results,
            "mode_results": self.mode_results,
            "execution_time": self.execution_time,
            "cancelled": self.cancelled,
        }


class BacktestExecutor:
    """統一バックテスト実行エンジン

    CLIとWebUIの両方から使用可能な統一実行エンジン。
    同期・非同期・並列実行をサポート。
    """

    def __init__(
        self,
        config: ExecutorConfig,
        cancel_callback: Callable[[], bool] | None = None,
        emitter: "BacktestEventEmitter | None" = None,
    ):
        """初期化

        Args:
            config: 実行設定
            cancel_callback: キャンセルコールバック
            emitter: イベントエミッター
        """
        self._config = config
        self._cancel_callback = cancel_callback
        self._emitter = emitter
        self._runner = None

    @property
    def config(self) -> ExecutorConfig:
        """設定を取得"""
        return self._config

    @property
    def emitter(self) -> "BacktestEventEmitter | None":
        """イベントエミッターを取得"""
        return self._emitter

    def set_emitter(self, emitter: "BacktestEventEmitter") -> None:
        """イベントエミッターを設定

        Args:
            emitter: イベントエミッター
        """
        self._emitter = emitter

    def _create_runner(self):
        """ランナーを作成

        Returns:
            BacktestRunner: ランナーインスタンス
        """
        from autotrader.backtest.runner import BacktestConfig, BacktestRunner

        backtest_config = BacktestConfig(
            symbol=self._config.symbol,
            timeframe="M5" if self._config.use_short_timeframe else "M15",
            initial_balance=self._config.initial_balance,
            volume=self._config.volume,
            max_positions=self._config.max_positions,
            spread_pips=self._config.spread_pips,
            pip_value=self._config.pip_value,
        )

        runner = BacktestRunner(
            data_dir=self._config.data_dir,
            config=backtest_config,
            verbose=self._config.verbose,
        )

        if self._cancel_callback:
            runner.set_cancel_callback(self._cancel_callback)

        # イベントエミッターをセット
        if self._emitter:
            runner._emitter = self._emitter

        self._runner = runner
        return runner

    def _create_bot_config(self):
        """ボット設定を作成

        Returns:
            UnifiedBotConfig: ボット設定
        """
        from autotrader.decision.unified import UnifiedBotConfig

        # UNIVERSALモード固定: M1〜D1全TFを評価
        return UnifiedBotConfig()

    def run(self) -> ExecutorResult:
        """バックテスト実行（同期）

        Returns:
            ExecutorResult: 実行結果
        """
        import time

        start_time = time.time()

        if self._config.parallel_years:
            result = self._run_parallel()
        else:
            result = self._run_sequential()

        result.execution_time = time.time() - start_time
        return result

    def _run_sequential(self) -> ExecutorResult:
        """逐次実行

        Returns:
            ExecutorResult: 実行結果
        """
        runner = self._create_runner()
        bot_config = self._create_bot_config()

        backtest_result = runner.run_unified(
            self._config.start_year,
            self._config.end_year,
            bot_config,
            use_m1=self._config.use_short_timeframe,
            fundamental_csv=self._config.fundamental_csv,
            fundamental_guard_minutes=(
                self._config.fundamental_guard_minutes
            ),
        )

        return self._convert_result(backtest_result)

    def _run_parallel(self) -> ExecutorResult:
        """並列実行

        Returns:
            ExecutorResult: 実行結果
        """
        from autotrader.backtest.parallel import ParallelYearExecutor

        executor = ParallelYearExecutor(
            max_workers=self._config.max_workers
        )

        year_results = executor.execute(
            years=self._config.get_years(),
            config=self._config,
            cancel_callback=self._cancel_callback,
        )

        return self._aggregate_parallel_results(year_results)

    def _aggregate_parallel_results(
        self,
        year_results: list[dict],
    ) -> ExecutorResult:
        """並列実行結果を集計

        Args:
            year_results: 年別結果リスト

        Returns:
            ExecutorResult: 集計結果
        """
        if not year_results:
            return ExecutorResult(cancelled=True)

        # キャンセルチェック
        if any(r.get("cancelled", False) for r in year_results):
            return ExecutorResult(
                cancelled=True,
                yearly_results=[
                    r for r in year_results if not r.get("cancelled", False)
                ],
            )

        # 集計
        total_trades = sum(r.get("trades", 0) for r in year_results)
        total_profit = sum(r.get("net_profit", 0) for r in year_results)

        if year_results:
            avg_win_rate = sum(
                r.get("win_rate", 0) for r in year_results
            ) / len(year_results)
            avg_pf = sum(
                r.get("profit_factor", 0) for r in year_results
            ) / len(year_results)
            max_dd = max(r.get("max_drawdown", 0) for r in year_results)
            avg_sharpe = sum(
                r.get("sharpe", 0) for r in year_results
            ) / len(year_results)
        else:
            avg_win_rate = 0
            avg_pf = 0
            max_dd = 0
            avg_sharpe = 0

        years = len(year_results)
        annual_return = (
            total_profit / self._config.initial_balance * 100 / years
            if years > 0 else 0
        )

        # 月別結果を集約
        monthly_results = []
        for yr in year_results:
            monthly_results.extend(yr.get("monthly_results", []))

        return ExecutorResult(
            trades=total_trades,
            win_rate=avg_win_rate,
            profit_factor=avg_pf,
            net_profit=total_profit,
            max_drawdown=max_dd,
            sharpe_ratio=avg_sharpe,
            annual_return=annual_return,
            monthly_results=monthly_results,
            yearly_results=year_results,
        )

    def _convert_result(
        self,
        backtest_result: "BacktestResult",
    ) -> ExecutorResult:
        """BacktestResult を ExecutorResult に変換

        Args:
            backtest_result: バックテスト結果

        Returns:
            ExecutorResult: 変換結果
        """
        return ExecutorResult(
            trades=backtest_result.trades,
            win_rate=backtest_result.win_rate,
            profit_factor=backtest_result.profit_factor,
            net_profit=backtest_result.net_profit,
            max_drawdown=backtest_result.max_drawdown,
            sharpe_ratio=backtest_result.sharpe_ratio,
            annual_return=backtest_result.annual_return,
            monthly_results=backtest_result.monthly_results,
            yearly_results=backtest_result.yearly_results,
        )

    async def run_async(self) -> ExecutorResult:
        """バックテスト実行（非同期）

        WebUIからの呼び出し用。別スレッドで同期実行をラップ。

        Returns:
            ExecutorResult: 実行結果
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run)


def run_backtest(config: ExecutorConfig) -> ExecutorResult:
    """バックテスト実行（便利関数）

    Args:
        config: 実行設定

    Returns:
        ExecutorResult: 実行結果
    """
    executor = BacktestExecutor(config)
    return executor.run()
