"""バックテストサービス

WebUIとスクリプトの両方から使用可能な共通バックテスト実行サービス。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from autotrader.backtest.runner import BacktestRunner, BacktestConfig, BacktestResult
from autotrader.backtest.events import BacktestEventEmitter
from autotrader.config import DEFAULT_TRADING_PARAMS
from autotrader.config.trading_params import get_preset


@dataclass
class BacktestServiceConfig:
    """バックテストサービス設定

    Attributes:
        start_year: 開始年
        end_year: 終了年
        initial_balance: 初期残高
        volume: 取引ボリューム
        data_dir: データディレクトリ
        symbol: 通貨ペア
        timeframe: 基準時間足（トレード判断の頻度）
        max_positions: 最大ポジション数
        spread_pips: スプレッド(pips)
        verbose: 詳細出力
        use_short_timeframe: 短い時間足（M1/M5）を基準として使用
    """

    start_year: int = 2020
    end_year: int = 2024
    initial_balance: float = 1_000_000.0
    volume: float = 1.0
    data_dir: str = "data"
    symbol: str = "USDJPY"
    timeframe: str = "M5"
    max_positions: int = 1
    spread_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.spread_pips
    )
    slippage_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.slippage_pips
    )
    verbose: bool = False
    use_short_timeframe: bool = True
    use_parallel_tf: bool = False
    enable_scalping: bool = False

    @classmethod
    def from_preset(
        cls,
        symbol: str,
        preset_path: Path | None = None,
        **overrides: Any,
    ) -> "BacktestServiceConfig":
        """シンボルプリセットから BacktestServiceConfig を生成.

        プリセット値をデフォルトとして使用し、
        overrides で任意フィールドを上書きできる。

        Args:
            symbol: 通貨ペア名
            preset_path: YAMLファイルパス（None時はデフォルトパス）
            **overrides: 上書きするフィールド

        Returns:
            BacktestServiceConfig: プリセット値で初期化した設定
        """
        preset = get_preset(symbol, preset_path)
        kwargs: dict[str, Any] = {
            "symbol": symbol,
            "spread_pips": preset.spread_pips,
            "slippage_pips": preset.slippage_pips,
        }
        kwargs.update(overrides)
        return cls(**kwargs)


def create_bot_config():
    """ボット設定を作成

    Returns:
        UnifiedBotConfig: ボット設定
    """
    from autotrader.decision.unified import UnifiedBotConfig

    return UnifiedBotConfig(
        timeframes=["M15", "H1", "H4", "D1"],
    )


def create_backtest_config(config: BacktestServiceConfig) -> BacktestConfig:
    """サービス設定からBacktestConfigを作成

    Args:
        config: サービス設定

    Returns:
        BacktestConfig: バックテスト設定
    """
    return BacktestConfig(
        symbol=config.symbol,
        timeframe=config.timeframe,
        initial_balance=config.initial_balance,
        volume=config.volume,
        max_positions=config.max_positions,
        spread_pips=config.spread_pips,
        slippage_pips=config.slippage_pips,
    )


class BacktestService:
    """バックテストサービス

    WebUIとスクリプトの両方から使用可能な統一インターフェース。
    """

    def __init__(
        self,
        config: BacktestServiceConfig,
        cancel_callback: Callable[[], bool] | None = None,
    ):
        """初期化

        Args:
            config: サービス設定
            cancel_callback: キャンセルコールバック
        """
        self._config = config
        self._cancel_callback = cancel_callback
        self._runner: BacktestRunner | None = None
        self._pending_listeners: list = []

    @property
    def emitter(self) -> BacktestEventEmitter | None:
        """イベントエミッター取得"""
        if self._runner:
            return self._runner._emitter
        return None

    def add_listener(self, listener) -> "BacktestService":
        """イベントリスナーを追加

        Args:
            listener: EventListenerインスタンス

        Returns:
            self: メソッドチェーン用
        """
        self._pending_listeners.append(listener)
        return self

    def create_runner(self) -> BacktestRunner:
        """ランナーを作成

        Returns:
            BacktestRunner: バックテストランナー
        """
        backtest_config = create_backtest_config(self._config)

        runner = BacktestRunner(
            data_dir=self._config.data_dir,
            config=backtest_config,
            verbose=self._config.verbose,
        )

        if self._cancel_callback:
            runner.set_cancel_callback(self._cancel_callback)

        # 保留中のリスナーを追加
        for listener in self._pending_listeners:
            runner._emitter.add_listener(listener)

        self._runner = runner
        return runner

    def run(self) -> BacktestResult:
        """バックテスト実行

        Returns:
            BacktestResult: バックテスト結果
        """
        runner = self.create_runner()

        bot_config = create_bot_config()

        return runner.run_unified(
            self._config.start_year,
            self._config.end_year,
            bot_config,
            use_m1=self._config.use_short_timeframe,
            use_parallel_tf=self._config.use_parallel_tf,
            enable_scalping=self._config.enable_scalping,
        )

    def run_with_strategy(
        self,
        strategy_name: str,
    ) -> BacktestResult:
        """指定戦略でバックテスト実行

        Args:
            strategy_name: 戦略名

        Returns:
            BacktestResult: バックテスト結果
        """
        runner = self.create_runner()

        return runner.run(
            strategy_name,
            self._config.start_year,
            self._config.end_year,
        )


def run_backtest(
    config: BacktestServiceConfig,
    cancel_callback: Callable[[], bool] | None = None,
) -> BacktestResult:
    """バックテスト実行（便利関数）

    Args:
        config: サービス設定
        cancel_callback: キャンセルコールバック

    Returns:
        BacktestResult: バックテスト結果
    """
    service = BacktestService(config, cancel_callback)
    return service.run()


