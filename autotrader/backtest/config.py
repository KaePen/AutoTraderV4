"""統一バックテスト設定

全バックテストコンポーネントで使用する共通設定・結果クラス。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autotrader.config import DEFAULT_TRADING_PARAMS
from autotrader.config.trading_params import get_preset
from autotrader.core.enums import Timeframe


@dataclass
class UnifiedBacktestConfig:
    """統一バックテスト設定

    全バックテストコンポーネントで共有する設定。
    WebUI、CLI、エンジンすべてで同一設定を使用。

    Attributes:
        symbol: 通貨ペア
        start_year: 開始年
        end_year: 終了年
        start_date: 開始日時（年指定より優先）
        end_date: 終了日時（年指定より優先）
        initial_balance: 初期残高
        leverage: レバレッジ
        position_size_pct: ポジションサイズ（残高比率）
        volume: 取引ボリューム（固定ロット、Noneでposition_size_pct使用）
        use_short_timeframe: 短い時間足（M5）を基準として使用
        base_timeframe: 基準時間足
        max_positions: 最大ポジション数
        spread_pips: スプレッド（pips）
        pip_value: pip価値
        stop_loss_pips: デフォルトSL（pips）
        take_profit_pips: デフォルトTP（pips）
        min_confidence: 最小確度
        data_dir: データディレクトリ
        verbose: 詳細出力
    """

    symbol: str = "USDJPY"
    start_year: int = 2020
    end_year: int = 2024
    start_date: datetime | None = None
    end_date: datetime | None = None
    initial_balance: float = 1_000_000.0
    leverage: float = 25.0
    position_size_pct: float = 0.02
    volume: float | None = None
    use_short_timeframe: bool = True
    base_timeframe: str = "M5"
    max_positions: int = 1
    spread_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.spread_pips
    )
    pip_value: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.pip_value
    )
    stop_loss_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.default_sl_pips
    )
    take_profit_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.default_tp_pips
    )
    min_confidence: float = 0.6
    data_dir: str = "data/csv"
    verbose: bool = False

    @classmethod
    def from_preset(
        cls,
        symbol: str,
        *,
        start_year: int = 2020,
        end_year: int = 2024,
        initial_balance: float = 1_000_000.0,
        data_dir: str = "data/csv",
    ) -> UnifiedBacktestConfig:
        """SymbolPreset からバックテスト設定を生成.

        Args:
            symbol: 通貨ペア名（例: "USDJPY"）
            start_year: 開始年
            end_year: 終了年
            initial_balance: 初期残高
            data_dir: データディレクトリ

        Returns:
            UnifiedBacktestConfig: プリセットベースの設定
        """
        p = get_preset(symbol)
        return cls(
            symbol=symbol,
            start_year=start_year,
            end_year=end_year,
            initial_balance=initial_balance,
            spread_pips=p.spread_pips,
            pip_value=p.pip_value,
            stop_loss_pips=p.default_sl_pips,
            take_profit_pips=p.default_tp_pips,
            max_positions=p.max_positions,
            data_dir=data_dir,
        )

    def get_effective_timeframe(self) -> str:
        """実効時間足を取得

        Returns:
            str: 使用する基準時間足
        """
        if self.use_short_timeframe:
            return self.base_timeframe
        return "H1"

    def get_start_datetime(self) -> datetime:
        """開始日時を取得

        Returns:
            datetime: 開始日時
        """
        if self.start_date:
            return self.start_date
        return datetime(self.start_year, 1, 1)

    def get_end_datetime(self) -> datetime:
        """終了日時を取得

        Returns:
            datetime: 終了日時
        """
        if self.end_date:
            return self.end_date
        return datetime(self.end_year, 12, 31, 23, 59, 59)

    def to_legacy_runner_config(self) -> dict[str, Any]:
        """レガシーrunner.py BacktestConfig形式に変換

        後方互換性のため。

        Returns:
            dict: レガシー設定辞書
        """
        return {
            "symbol": self.symbol,
            "timeframe": self.get_effective_timeframe(),
            "initial_balance": self.initial_balance,
            "volume": self.volume,
            "max_positions": self.max_positions,
            "spread_pips": self.spread_pips,
            "pip_value": self.pip_value,
        }

    def to_legacy_engine_config(self) -> dict[str, Any]:
        """レガシーengine.py BacktestConfig形式に変換

        後方互換性のため。

        Returns:
            dict: レガシー設定辞書
        """
        return {
            "symbol": self.symbol,
            "timeframe": Timeframe(self.get_effective_timeframe()),
            "start_date": self.get_start_datetime(),
            "end_date": self.get_end_datetime(),
            "initial_balance": self.initial_balance,
            "spread_pips": self.spread_pips,
            "pip_value": self.pip_value,
            "max_positions": self.max_positions,
            "default_volume": self.volume or 0.1,
            "min_confidence": self.min_confidence,
            "stop_loss_pips": self.stop_loss_pips,
            "take_profit_pips": self.take_profit_pips,
        }


@dataclass
class ParallelBacktestConfig:
    """並列バックテスト設定

    マルチタイムフレーム並列バックテスト用の追加設定。

    Attributes:
        enable_parallel_tf: 並列TF評価を有効化
        max_tf_workers: TF並列ワーカー数
        use_sequential: デバッグ用シーケンシャルモード
        timeframes: 使用するタイムフレームリスト
        htf_priority: 上位足優先モード（同時刻イベントで長期足を先に処理）
    """

    enable_parallel_tf: bool = True
    max_tf_workers: int = 6
    use_sequential: bool = False
    timeframes: list[str] = field(
        default_factory=lambda: ["M5", "M15", "H1", "H4", "D1"]
    )
    htf_priority: bool = True


@dataclass
class MonthlyResult:
    """月別バックテスト結果

    Attributes:
        year: 年
        month: 月
        trades: トレード数
        wins: 勝ちトレード数
        losses: 負けトレード数
        win_rate: 勝率（%）
        profit: 純利益
        profit_pct: 利益率（%）
        cumulative_profit: 累積利益
        cumulative_profit_pct: 累積利益率（%）
        max_drawdown_pct: 最大ドローダウン（%）
    """

    year: int
    month: int
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit: float = 0.0
    profit_pct: float = 0.0
    cumulative_profit: float = 0.0
    cumulative_profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換

        Returns:
            dict: 結果辞書
        """
        return {
            "year": self.year,
            "month": self.month,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 2),
            "profit": round(self.profit, 2),
            "profit_pct": round(self.profit_pct, 4),
            "cumulative_profit": round(self.cumulative_profit, 2),
            "cumulative_profit_pct": round(self.cumulative_profit_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
        }


@dataclass
class YearlyResult:
    """年別バックテスト結果

    Attributes:
        year: 年
        trades: トレード数
        wins: 勝ちトレード数
        losses: 負けトレード数
        win_rate: 勝率（%）
        profit: 純利益
        profit_pct: 利益率（%）
        max_drawdown_pct: 最大ドローダウン（%）
    """

    year: int
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit: float = 0.0
    profit_pct: float = 0.0
    max_drawdown_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換

        Returns:
            dict: 結果辞書
        """
        return {
            "year": self.year,
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 2),
            "profit": round(self.profit, 2),
            "profit_pct": round(self.profit_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
        }


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
        annual_return: 年間収益率（%）
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
    annual_return: float = 0.0
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
            "annual_return": round(self.annual_return, 4),
        }


@dataclass
class UnifiedBacktestResult:
    """統一バックテスト結果

    全バックテストコンポーネントの結果を統一フォーマットで保持。

    Attributes:
        config: バックテスト設定
        metrics: 評価指標
        trades: トレードリスト
        monthly_results: 月別結果
        yearly_results: 年別結果
        signals_generated: 生成シグナル数
        signals_filtered: フィルターされたシグナル数
        execution_time: 実行時間（秒）
    """

    config: UnifiedBacktestConfig
    metrics: BacktestMetrics
    trades: list[Any] = field(default_factory=list)
    monthly_results: list[MonthlyResult] = field(default_factory=list)
    yearly_results: list[YearlyResult] = field(default_factory=list)
    signals_generated: int = 0
    signals_filtered: int = 0
    execution_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """辞書に変換

        Returns:
            dict: 結果辞書
        """
        return {
            "config": {
                "symbol": self.config.symbol,
                "start_year": self.config.start_year,
                "end_year": self.config.end_year,
                "initial_balance": self.config.initial_balance,
            },
            "metrics": self.metrics.to_dict(),
            "trade_count": len(self.trades),
            "monthly_results": [m.to_dict() for m in self.monthly_results],
            "yearly_results": [y.to_dict() for y in self.yearly_results],
            "signals_generated": self.signals_generated,
            "signals_filtered": self.signals_filtered,
            "execution_time": round(self.execution_time, 2),
        }

    def get_win_rate(self) -> float:
        """勝率を取得

        Returns:
            float: 勝率（%）
        """
        return self.metrics.win_rate * 100

    def get_profit_factor(self) -> float:
        """プロフィットファクターを取得

        Returns:
            float: プロフィットファクター
        """
        return self.metrics.profit_factor

    def get_net_profit(self) -> float:
        """純利益を取得

        Returns:
            float: 純利益
        """
        return self.metrics.net_profit

    def get_max_drawdown_pct(self) -> float:
        """最大ドローダウン率を取得

        Returns:
            float: 最大ドローダウン（%）
        """
        return self.metrics.max_drawdown_pct * 100
