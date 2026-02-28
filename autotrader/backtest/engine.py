"""バックテストエンジン

データ → 計算機 → 制約機 → 判定機 → シミュレーター → 評価
の一連の流れを統括する。

Protocol基盤のシグナル生成インターフェースを提供し、
レガシージェネレーターとUnifiedTradeBotの両方に対応。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol, runtime_checkable
import logging
import time

import pandas as pd

from autotrader.config import DEFAULT_TRADING_PARAMS
from autotrader.core.entities import Candle, Signal, Trade
from autotrader.core.enums import Timeframe, SignalType, ExitReason
from autotrader.backtest.data_loader import DataLoader
from autotrader.backtest.simulator import TradeSimulator, SimulatorConfig
from autotrader.backtest.metrics import MetricsCalculator, BacktestMetrics
from autotrader.backtest.events import BacktestEventEmitter
from autotrader.calculator.precompute import PrecomputeEngine
from autotrader.constraint.hard_guard import HardGuard, HardGuardConfig
from autotrader.constraint.soft_guard import SoftGuard, SoftGuardConfig
from autotrader.decision.signal_generator import SignalGenerator


logger = logging.getLogger(__name__)


@runtime_checkable
class SignalGeneratorProtocol(Protocol):
    """シグナル生成プロトコル

    バックテストエンジンが使用するシグナル生成インターフェース。
    レガシーSignalGeneratorとUnifiedTradeBotの両方に対応。
    """

    def generate_signal(
        self,
        current_time: datetime | pd.Timestamp,
        candle: Candle,
        data_dict: dict[str, pd.DataFrame] | None = None,
    ) -> Signal | None:
        """シグナルを生成

        Args:
            current_time: 現在時刻
            candle: 足データ
            data_dict: 時間足別データ（オプション）

        Returns:
            Signal | None: シグナル（HOLDの場合None）
        """
        ...


@dataclass
class BacktestConfig:
    """バックテスト設定

    Attributes:
        symbol: シンボル
        timeframe: 時間足
        start_date: 開始日時
        end_date: 終了日時
        initial_balance: 初期残高
        spread_pips: スプレッド
        pip_value: pip価値
        max_positions: 最大ポジション数
        default_volume: デフォルトロット
        data_dir: データディレクトリ
        min_confidence: 最小確度
        stop_loss_pips: デフォルトSL（pips）
        take_profit_pips: デフォルトTP（pips）
    """

    symbol: str = "USDJPY"
    timeframe: Timeframe = Timeframe.M15
    start_date: datetime = field(default_factory=lambda: datetime(2023, 1, 1))
    end_date: datetime = field(default_factory=lambda: datetime(2023, 12, 31))
    initial_balance: float = 1_000_000.0
    spread_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.spread_pips
    )
    pip_value: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.pip_value
    )
    max_positions: int = 1
    default_volume: float = 0.1
    data_dir: str = "data/raw"
    min_confidence: float = 0.6
    stop_loss_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.default_sl_pips
    )
    take_profit_pips: float = field(
        default_factory=lambda: DEFAULT_TRADING_PARAMS.default_tp_pips
    )


@dataclass
class BacktestResult:
    """バックテスト結果

    Attributes:
        config: バックテスト設定
        metrics: 評価指標
        trades: トレードリスト
        equity_curve: エクイティカーブ
        signals_generated: 生成シグナル数
        signals_filtered: フィルターされたシグナル数
        execution_time: 実行時間（秒）
    """

    config: BacktestConfig
    metrics: BacktestMetrics
    trades: list[Trade]
    equity_curve: list[dict[str, Any]]
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
                "timeframe": self.config.timeframe.value,
                "start_date": self.config.start_date.isoformat(),
                "end_date": self.config.end_date.isoformat(),
                "initial_balance": self.config.initial_balance,
            },
            "metrics": self.metrics.to_dict(),
            "trade_count": len(self.trades),
            "signals_generated": self.signals_generated,
            "signals_filtered": self.signals_filtered,
            "execution_time": self.execution_time,
        }


class BacktestEngine:
    """バックテストエンジン

    データ読み込みからメトリクス計算まで一連の処理を実行。

    Attributes:
        config: バックテスト設定
        data_loader: データローダー
        precompute: 事前計算エンジン
        hard_guard: ハードガード
        soft_guard: ソフトガード
        signal_generator: シグナル生成器
        simulator: トレードシミュレーター
        metrics_calc: メトリクス計算器
    """

    def __init__(
        self,
        config: BacktestConfig,
        hard_guard_config: HardGuardConfig | None = None,
        soft_guard_config: SoftGuardConfig | None = None,
    ) -> None:
        """初期化

        Args:
            config: バックテスト設定
            hard_guard_config: ハードガード設定
            soft_guard_config: ソフトガード設定
        """
        self.config = config

        # コンポーネント初期化
        self.data_loader = DataLoader(
            data_dir=config.data_dir,
        )

        self.precompute = PrecomputeEngine()

        self.hard_guard = HardGuard(
            config=hard_guard_config or HardGuardConfig()
        )

        self.soft_guard = SoftGuard(
            config=soft_guard_config or SoftGuardConfig()
        )

        # signal_generator は外部から注入（Protocol対応）
        self.signal_generator: SignalGeneratorProtocol | None = None

        simulator_config = SimulatorConfig(
            initial_balance=config.initial_balance,
            spread_pips=config.spread_pips,
            pip_value=config.pip_value,
            max_positions=config.max_positions,
            default_volume=config.default_volume,
        )
        self.simulator = TradeSimulator(config=simulator_config)

        self.metrics_calc = MetricsCalculator(
            initial_balance=config.initial_balance,
        )

        # 統計
        self._signals_generated = 0
        self._signals_filtered = 0

    def run(
        self,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> BacktestResult:
        """バックテストを実行

        Args:
            progress_callback: 進捗コールバック(current, total)

        Returns:
            BacktestResult: バックテスト結果
        """
        import time

        start_time = time.time()

        logger.info(
            f"バックテスト開始: {self.config.symbol} "
            f"{self.config.start_date} - {self.config.end_date}"
        )

        # データ読み込み
        logger.info("データ読み込み中...")
        df = self.data_loader._load_raw_data(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
        )

        if df.empty:
            logger.warning("データが空です")
            return self._create_empty_result(0.0)

        logger.info(f"データ件数: {len(df)}")

        # 事前計算
        logger.info("テクニカル指標計算中...")
        df_with_indicators = self.precompute.precompute(
            df=df,
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            use_cache=False,
        )

        # シミュレーション実行
        logger.info("シミュレーション実行中...")
        self.simulator.reset()
        self._signals_generated = 0
        self._signals_filtered = 0

        total_rows = len(df_with_indicators)
        last_candle = None

        # numpy配列ベースのループ
        from autotrader.backtest.candle_arrays import CandleArrays
        arrays = CandleArrays.from_dataframe(df_with_indicators)
        for i in range(arrays.n_rows):
            # 進捗通知
            if progress_callback and i % 1000 == 0:
                progress_callback(i, total_rows)

            # Candleに変換
            candle = arrays.get_candle(
                i, self.config.symbol, self.config.timeframe
            )
            last_candle = candle

            # シグナル生成（指標データはrowから取得が必要）
            row = df_with_indicators.iloc[i]
            # Protocol の generate_signal を呼び出し
            signal = None
            if self.signal_generator:
                signal = self.signal_generator.generate_signal(
                    current_time=candle.time,
                    candle=candle,
                    data_dict=None,
                )

            # シミュレーター処理
            self.simulator.process_candle(candle, signal)

        # 最終決済
        if last_candle:
            self.simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

        # メトリクス計算
        logger.info("評価指標計算中...")
        trades = self.simulator.get_closed_trades()
        equity_history = self.simulator.state.daily_pnl

        metrics = self.metrics_calc.calculate(
            trades=trades,
            equity_history=equity_history,
        )

        execution_time = time.time() - start_time
        logger.info(f"バックテスト完了: {execution_time:.2f}秒")

        return BacktestResult(
            config=self.config,
            metrics=metrics,
            trades=trades,
            equity_curve=metrics.equity_curve,
            signals_generated=self._signals_generated,
            signals_filtered=self._signals_filtered,
            execution_time=execution_time,
        )


    def _row_to_candle(self, row: pd.Series) -> Candle:
        """データ行をCandleに変換

        Args:
            row: データ行

        Returns:
            Candle: 足データ
        """
        return Candle(
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            time=pd.to_datetime(row["time"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", 0)),
        )

    def _extract_indicators(self, row: pd.Series) -> dict[str, Any]:
        """指標データを抽出

        Args:
            row: データ行

        Returns:
            dict: 指標辞書
        """
        indicators = {}

        # 利用可能な指標列を抽出
        indicator_cols = [
            "sma_20", "sma_50", "sma_200",
            "ema_12", "ema_26",
            "rsi_14",
            "macd", "macd_signal", "macd_hist",
            "bb_upper", "bb_middle", "bb_lower", "bb_width",
            "atr_14",
            "stoch_k", "stoch_d",
            "adx",
        ]

        for col in indicator_cols:
            if col in row.index and pd.notna(row[col]):
                indicators[col] = float(row[col])

        return indicators

    def _build_context(
        self,
        candle: Candle,
        indicators: dict[str, Any],
    ) -> dict[str, Any]:
        """ガードチェック用コンテキストを構築

        Args:
            candle: 足データ
            indicators: 指標辞書

        Returns:
            dict: コンテキスト
        """
        state = self.simulator.get_state_snapshot()

        return {
            "balance": state["balance"],
            "equity": state["equity"],
            "margin_used": 0.0,  # バックテストでは簡略化
            "margin_free": state["balance"],
            "daily_loss": 0.0,  # 日次損失は別途計算が必要
            "open_positions_count": state["open_positions_count"],
            "current_hour": candle.time.hour,
            "spread_pips": self.config.spread_pips,
            "volatility": indicators.get("atr_14", 0.0),
            "trend_strength": indicators.get("adx", 0.0),
            "current_drawdown": state["current_drawdown"],
        }

    def _calculate_stop_loss(
        self,
        signal_type: SignalType,
        current_price: float,
    ) -> float:
        """ストップロスを計算

        Args:
            signal_type: シグナル種別
            current_price: 現在価格

        Returns:
            float: ストップロス価格
        """
        sl_distance = self.config.stop_loss_pips * 0.01

        if signal_type == SignalType.BUY:
            return current_price - sl_distance
        else:
            return current_price + sl_distance

    def _calculate_take_profit(
        self,
        signal_type: SignalType,
        current_price: float,
    ) -> float:
        """テイクプロフィットを計算

        Args:
            signal_type: シグナル種別
            current_price: 現在価格

        Returns:
            float: テイクプロフィット価格
        """
        tp_distance = self.config.take_profit_pips * 0.01

        if signal_type == SignalType.BUY:
            return current_price + tp_distance
        else:
            return current_price - tp_distance

    def _create_empty_result(
        self,
        execution_time: float,
    ) -> BacktestResult:
        """空の結果を作成

        Args:
            execution_time: 実行時間

        Returns:
            BacktestResult: 空の結果
        """
        return BacktestResult(
            config=self.config,
            metrics=BacktestMetrics(),
            trades=[],
            equity_curve=[],
            signals_generated=0,
            signals_filtered=0,
            execution_time=execution_time,
        )

    def save_result_to_db(
        self,
        result: BacktestResult,
        session: Any,
        name: str,
        description: str | None = None,
    ) -> int:
        """結果をDBに保存

        Args:
            result: バックテスト結果
            session: SQLAlchemyセッション
            name: バックテスト名
            description: 説明

        Returns:
            int: バックテストID
        """
        from autotrader.adapters.database.repositories import (
            BacktestRepository,
            TradeRepository,
        )

        backtest_repo = BacktestRepository(session)
        trade_repo = TradeRepository(session)

        # バックテスト結果を保存
        backtest_record = backtest_repo.create(
            name=name,
            symbol=result.config.symbol,
            timeframe=result.config.timeframe.value,
            start_date=result.config.start_date,
            end_date=result.config.end_date,
            config={
                "initial_balance": result.config.initial_balance,
                "spread_pips": result.config.spread_pips,
                "min_confidence": result.config.min_confidence,
            },
            description=description,
        )

        # メトリクスを更新
        backtest_repo.update_metrics(
            backtest=backtest_record,
            total_trades=result.metrics.total_trades,
            winning_trades=result.metrics.winning_trades,
            losing_trades=result.metrics.losing_trades,
            total_profit=result.metrics.total_profit,
            total_loss=result.metrics.total_loss,
            max_drawdown=result.metrics.max_drawdown,
            max_drawdown_pct=result.metrics.max_drawdown_pct,
            sharpe_ratio=result.metrics.sharpe_ratio,
            avg_trade_duration=result.metrics.avg_trade_duration,
            daily_stats=result.metrics.to_dict(),
            equity_curve=result.equity_curve,
        )

        # トレードを保存
        for trade in result.trades:
            trade_repo.create(
                symbol=trade.symbol,
                signal_type=trade.signal_type.value,
                volume=trade.volume,
                entry_price=trade.entry_price,
                opened_at=trade.opened_at,
                backtest_id=backtest_record.id,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
            )

            # 決済情報を更新
            if trade.exit_price and trade.closed_at:
                trade_record = trade_repo.get_by_id(trade.trade_id)
                if trade_record:
                    trade_repo.close(
                        trade=trade_record,
                        exit_price=trade.exit_price,
                        closed_at=trade.closed_at,
                        exit_reason=trade.exit_reason or "unknown",
                        profit_loss=trade.profit_loss or 0.0,
                        profit_loss_pips=trade.profit_loss_pips,
                    )

        return backtest_record.id


class LegacyGeneratorAdapter:
    """既存SignalGeneratorをProtocolに適合するアダプター

    レガシーのSignalGeneratorをSignalGeneratorProtocolに適合させる。
    """

    def __init__(
        self,
        generator: Any,
        symbol: str,
        timeframe: Timeframe,
        min_confidence: float = 0.6,
    ):
        """初期化

        Args:
            generator: レガシーシグナルジェネレーター
            symbol: 通貨ペア
            timeframe: 時間足
            min_confidence: 最小確度
        """
        self._generator = generator
        self._symbol = symbol
        self._timeframe = timeframe
        self._min_confidence = min_confidence

    def generate_signal(
        self,
        current_time: datetime | pd.Timestamp,
        candle: Candle,
        data_dict: dict[str, pd.DataFrame] | None = None,
    ) -> Signal | None:
        """シグナルを生成

        Args:
            current_time: 現在時刻
            candle: 足データ
            data_dict: 時間足別データ（未使用）

        Returns:
            Signal | None: シグナル
        """
        from uuid import uuid4

        # レガシージェネレーターを呼び出し
        if hasattr(self._generator, "generate"):
            result = self._generator.generate(candle)

            if result is None:
                return None

            if hasattr(result, "signal_type"):
                if result.signal_type == SignalType.HOLD:
                    return None

                confidence = max(
                    result.strength.buy_strength,
                    result.strength.sell_strength,
                )

                if confidence < self._min_confidence:
                    return None

                return Signal(
                    signal_id=str(uuid4()),
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    signal_type=result.signal_type,
                    confidence=confidence,
                    reasoning=result.reasoning if hasattr(result, "reasoning") else "",
                    created_at=current_time,
                )

        return None


class UnifiedBotAdapter:
    """UnifiedTradeBotをProtocolに適合するアダプター

    UnifiedTradeBotをSignalGeneratorProtocolに適合させる。
    """

    def __init__(
        self,
        bot: Any,
        symbol: str,
        min_confidence: float = 0.5,
    ):
        """初期化

        Args:
            bot: UnifiedTradeBot
            symbol: 通貨ペア
            min_confidence: 最小確度
        """
        self._bot = bot
        self._symbol = symbol
        self._min_confidence = min_confidence

    def generate_signal(
        self,
        current_time: datetime | pd.Timestamp,
        candle: Candle,
        data_dict: dict[str, pd.DataFrame] | None = None,
    ) -> Signal | None:
        """シグナルを生成

        Args:
            current_time: 現在時刻
            candle: 足データ
            data_dict: 時間足別データ（オプション）

        Returns:
            Signal | None: シグナル
        """
        # UnifiedTradeBotのgenerate_signalを呼び出し
        consolidated = self._bot.generate_signal(current_time, candle)

        if consolidated.direction == SignalType.HOLD:
            return None

        if consolidated.confidence < self._min_confidence:
            return None

        # SL/TPを価格に変換
        sl_price = None
        tp_price = None
        if consolidated.sl_pips > 0:
            if consolidated.direction == SignalType.BUY:
                sl_price = candle.close - consolidated.sl_pips / 100
                tp_price = candle.close + consolidated.tp_pips / 100
            else:
                sl_price = candle.close + consolidated.sl_pips / 100
                tp_price = candle.close - consolidated.tp_pips / 100

        return Signal(
            symbol=self._symbol,
            timeframe=candle.timeframe,
            signal_type=consolidated.direction,
            confidence=min(consolidated.confidence, 1.0),
            stop_loss=sl_price,
            take_profit=tp_price,
            reasoning=consolidated.rationale,
        )


@dataclass
class UnifiedEngineConfig:
    """統一エンジン設定

    Attributes:
        symbol: 通貨ペア
        initial_balance: 初期残高
        spread_pips: スプレッド（pips）
        pip_value: pip価値
        max_positions: 最大ポジション数
        default_volume: デフォルトボリューム
        min_confidence: 最小確度
        progress_interval: 進捗報告間隔（行数）
    """

    symbol: str = "USDJPY"
    initial_balance: float = 1_000_000.0
    spread_pips: float = 1.5
    pip_value: float = 100.0
    max_positions: int = 1
    default_volume: float = 1.0
    min_confidence: float = 0.5
    progress_interval: int = 100


@dataclass
class UnifiedEngineResult:
    """統一エンジン結果

    Attributes:
        trades: 決済済みトレード
        monthly_results: 月別結果
        metrics: 評価指標
        final_balance: 最終残高
        execution_time: 実行時間（秒）
        cancelled: キャンセルされたか
    """

    trades: list[Trade] = field(default_factory=list)
    monthly_results: list[dict[str, Any]] = field(default_factory=list)
    metrics: BacktestMetrics | None = None
    final_balance: float = 0.0
    execution_time: float = 0.0
    cancelled: bool = False


class UnifiedBacktestEngine:
    """統一バックテストエンジン

    Protocol基盤のシグナル生成を使用し、
    レガシーとUnifiedTradeBotの両方に対応。

    runner.pyの_run_yearと_run_unified_yearを統合。
    """

    def __init__(
        self,
        config: UnifiedEngineConfig,
        signal_generator: SignalGeneratorProtocol,
        event_emitter: BacktestEventEmitter | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ):
        """初期化

        Args:
            config: エンジン設定
            signal_generator: シグナルジェネレーター（Protocol準拠）
            event_emitter: イベントエミッター（オプション）
            cancel_callback: キャンセルコールバック（オプション）
        """
        self._config = config
        self._signal_generator = signal_generator
        self._emitter = event_emitter
        self._cancel_callback = cancel_callback

        # シミュレーター設定
        self._sim_config = SimulatorConfig(
            initial_balance=config.initial_balance,
            spread_pips=config.spread_pips,
            pip_value=config.pip_value,
            max_positions=config.max_positions,
            default_volume=config.default_volume,
        )

    def run_year(
        self,
        df: pd.DataFrame,
        year: int,
        timeframe: Timeframe,
        data_dict: dict[str, pd.DataFrame] | None = None,
    ) -> UnifiedEngineResult:
        """1年分のバックテストを実行

        Args:
            df: 基準タイムフレームのデータ
            year: 対象年
            timeframe: 時間足
            data_dict: 時間足別データ（オプション）

        Returns:
            UnifiedEngineResult: 実行結果
        """
        start_time = time.time()
        start_date = datetime(year, 1, 1)
        end_date = datetime(year + 1, 1, 1)

        # 期間フィルタリング
        period_df = df[
            (df["time"] >= start_date) & (df["time"] < end_date)
        ].reset_index(drop=True)

        if period_df.empty:
            return UnifiedEngineResult(cancelled=False)

        # シミュレーター初期化
        simulator = TradeSimulator(config=self._sim_config)

        # 月別トラッキング
        monthly_results: list[dict[str, Any]] = []
        current_month = None
        month_start_balance = self._sim_config.initial_balance
        month_trades = 0

        # メトリクス追跡
        winning_trades = 0
        losing_trades = 0
        total_rows = len(period_df)

        last_candle = None

        # numpy配列ベースのループ
        from autotrader.backtest.candle_arrays import CandleArrays
        arrays = CandleArrays.from_dataframe(period_df)
        for idx in range(arrays.n_rows):
            candle = arrays.get_candle(
                idx, self._config.symbol, timeframe
            )
            last_candle = candle
            candle_time = arrays.get_time(idx)

            # 月変わり検出
            candle_month = (candle_time.year, candle_time.month)
            if current_month is None:
                current_month = candle_month
                month_start_balance = simulator.state.balance
            elif candle_month != current_month:
                # 月末処理
                month_pnl = simulator.state.balance - month_start_balance
                month_return = month_pnl / month_start_balance * 100
                month_result = {
                    "year": current_month[0],
                    "month": current_month[1],
                    "trades": month_trades,
                    "pnl": month_pnl,
                    "return_pct": month_return,
                }
                monthly_results.append(month_result)

                if self._emitter:
                    self._emitter.emit_month_end(month_result)

                current_month = candle_month
                month_start_balance = simulator.state.balance
                month_trades = 0

            # シグナル生成
            signal = self._signal_generator.generate_signal(
                current_time=pd.Timestamp(candle_time),
                candle=candle,
                data_dict=data_dict,
            )

            # シグナルイベント発行
            if signal and self._emitter:
                self._emitter.emit_signal(
                    signal_type=signal.signal_type.value,
                    symbol=self._config.symbol,
                    timeframe=timeframe.value,
                    confidence=signal.confidence,
                    sl_pips=0,
                    tp_pips=0,
                    rationale=signal.reasoning or "",
                    candle_time=candle_time,
                )

            # シミュレーター処理
            prev_positions = simulator.get_open_positions()
            prev_trade_count = len(simulator.get_closed_trades())
            simulator.process_candle(candle, signal)

            # 新規ポジション検出
            if self._emitter:
                current_positions = simulator.get_open_positions()
                for pos in current_positions:
                    if pos not in prev_positions:
                        self._emitter.emit_trade_opened(
                            trade_id=pos.position_id,
                            symbol=pos.symbol,
                            direction=pos.signal_type.value,
                            entry_price=pos.entry_price,
                            volume=pos.volume,
                            candle_time=candle_time,
                        )

            # 決済検出
            closed_trades = simulator.get_closed_trades()
            if len(closed_trades) > prev_trade_count:
                month_trades += 1
                new_trade = closed_trades[-1]
                pnl = new_trade.profit_loss or 0

                if pnl > 0:
                    winning_trades += 1
                else:
                    losing_trades += 1

                if self._emitter:
                    self._emitter.emit_trade_closed(
                        trade_id=new_trade.trade_id,
                        symbol=new_trade.symbol,
                        direction=new_trade.signal_type.value,
                        entry_price=new_trade.entry_price,
                        exit_price=new_trade.exit_price or 0,
                        volume=new_trade.volume,
                        profit_loss=pnl,
                        exit_reason=(
                            new_trade.exit_reason.value
                            if new_trade.exit_reason
                            else "UNKNOWN"
                        ),
                        candle_time=candle_time,
                        opened_at=new_trade.opened_at,
                        position_id=(
                            new_trade.position_id or ""
                        ),
                    )

                    # メトリクス発行
                    total_trades = len(closed_trades)
                    self._emitter.emit_metrics(
                        balance=simulator.state.balance,
                        equity=simulator.state.balance,
                        total_trades=total_trades,
                        winning_trades=winning_trades,
                        losing_trades=losing_trades,
                        max_drawdown=simulator.state.max_drawdown * 100,
                    )

            # 進捗イベント
            if idx % self._config.progress_interval == 0:
                elapsed = time.time() - start_time

                if self._emitter:
                    self._emitter.emit_progress(
                        current=row_idx,
                        total=total_rows,
                        elapsed=elapsed,
                        message=f"{year}年処理中 ({timeframe.value})",
                    )

                # キャンセルチェック
                if self._cancel_callback and self._cancel_callback():
                    if self._emitter:
                        self._emitter.emit_backtest_end({"cancelled": True})
                    return UnifiedEngineResult(cancelled=True)

        # 強制決済
        if last_candle:
            simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

        # 最終月の結果
        if current_month:
            month_pnl = simulator.state.balance - month_start_balance
            month_return = month_pnl / month_start_balance * 100
            month_result = {
                "year": current_month[0],
                "month": current_month[1],
                "trades": month_trades,
                "pnl": month_pnl,
                "return_pct": month_return,
            }
            monthly_results.append(month_result)

            if self._emitter:
                self._emitter.emit_month_end(month_result)

        # メトリクス計算
        trades = simulator.get_closed_trades()
        calculator = MetricsCalculator(
            initial_balance=self._sim_config.initial_balance
        )
        metrics = calculator.calculate(trades, simulator.state.daily_pnl)

        execution_time = time.time() - start_time

        return UnifiedEngineResult(
            trades=trades,
            monthly_results=monthly_results,
            metrics=metrics,
            final_balance=simulator.state.balance,
            execution_time=execution_time,
            cancelled=False,
        )



@dataclass
class ParallelEngineConfig:
    """並列エンジン設定

    Attributes:
        symbol: 通貨ペア
        initial_balance: 初期残高
        spread_pips: スプレッド
        pip_value: pip価値
        max_positions: 最大ポジション数
        default_volume: デフォルトボリューム
        min_confidence: 最小確度
        stop_loss_pips: デフォルトSL
        take_profit_pips: デフォルトTP
        progress_interval: 進捗通知間隔
        enable_parallel: 並列評価を有効化
        max_tf_workers: TFワーカー数
        timeframes: 使用するタイムフレーム
        use_mode_aware_consensus: モード対応コンセンサスを使用
        enable_scalping: スキャルピングモード有効化
    """

    symbol: str = "USDJPY"
    initial_balance: float = 1_000_000.0
    spread_pips: float = 1.5
    pip_value: float = 100.0
    max_positions: int = 1
    default_volume: float = 0.1
    min_confidence: float = 0.6
    stop_loss_pips: float = 30.0
    take_profit_pips: float = 60.0
    progress_interval: int = 1000
    enable_parallel: bool = True
    max_tf_workers: int = 6
    timeframes: list[str] = field(
        default_factory=lambda: ["M5", "M15", "H1", "H4", "D1"]
    )
    use_mode_aware_consensus: bool = True
    enable_scalping: bool = False


class ParallelMultiTFBacktestEngine:
    """並列マルチタイムフレームバックテストエンジン

    全タイムフレームでエントリー可能なイベント駆動バックテスト。
    同時刻に複数TFが確定した場合は並列評価を行う。
    """

    def __init__(
        self,
        config: ParallelEngineConfig,
        event_emitter: BacktestEventEmitter | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ):
        """初期化

        Args:
            config: エンジン設定
            event_emitter: イベントエミッター
            cancel_callback: キャンセルコールバック
        """
        self._config = config
        self._emitter = event_emitter
        self._cancel_callback = cancel_callback

        # シミュレーター設定
        self._sim_config = SimulatorConfig(
            initial_balance=config.initial_balance,
            spread_pips=config.spread_pips,
            pip_value=config.pip_value,
            max_positions=config.max_positions,
            default_volume=config.default_volume,
        )

        # 並列評価器（モード対応または従来型）
        if config.use_mode_aware_consensus:
            from .parallel import ModeAwareParallelEvaluator
            self._mode_aware_evaluator = ModeAwareParallelEvaluator(
                max_workers=config.max_tf_workers
            )
            self._evaluator = None
        else:
            from .parallel import ParallelSignalEvaluator
            self._evaluator = ParallelSignalEvaluator(
                max_workers=config.max_tf_workers
            )
            self._mode_aware_evaluator = None

    def run(
        self,
        market_data: dict[str, pd.DataFrame],
        start_date: datetime,
        end_date: datetime,
    ) -> UnifiedEngineResult:
        """バックテストを実行

        Args:
            market_data: タイムフレーム別データ
            start_date: 開始日時
            end_date: 終了日時

        Returns:
            UnifiedEngineResult: 実行結果
        """
        from .events import TimelineEventQueue

        start_time = time.time()

        # 期間でフィルタリングしたデータを用意
        filtered_data = {}
        for tf, df in market_data.items():
            if df is None or df.empty:
                continue
            mask = (df["time"] >= start_date) & (df["time"] < end_date)
            filtered_df = df[mask].reset_index(drop=True)
            if not filtered_df.empty:
                filtered_data[tf] = filtered_df

        if not filtered_data:
            return UnifiedEngineResult(cancelled=False)

        # タイムラインイベントキューを構築
        event_queue = TimelineEventQueue(
            market_data=filtered_data,
            symbol=self._config.symbol,
        )

        # シミュレーター初期化
        simulator = TradeSimulator(config=self._sim_config)

        # 月別トラッキング
        monthly_results: list[dict[str, Any]] = []
        current_month: tuple[int, int] | None = None
        month_start_balance = self._sim_config.initial_balance
        month_trades = 0

        # メトリクス追跡
        winning_trades = 0
        losing_trades = 0
        event_count = 0
        total_events = len(event_queue)

        last_candle: Candle | None = None

        for event_batch in event_queue:
            event_count += len(event_batch)

            # モード対応評価または従来評価
            mode_aware_result = None
            eval_results = {}

            if self._mode_aware_evaluator is not None:
                # モード対応評価
                h1_row_data = self._extract_h1_row_data(event_batch)
                mode_aware_result = self._mode_aware_evaluator.evaluate_with_mode(
                    events=event_batch,
                    h1_row_data=h1_row_data,
                )
                eval_results = mode_aware_result.tf_results
            else:
                # 従来評価
                if self._config.enable_parallel:
                    eval_results = self._evaluator.evaluate_batch(event_batch)
                else:
                    eval_results = self._evaluator.evaluate_sequential(
                        event_batch
                    )

            # 各イベントを処理
            for event in event_batch:
                # キャンドルを構築
                candle = Candle(
                    symbol=self._config.symbol,
                    timeframe=Timeframe(event.timeframe),
                    time=event.timestamp,
                    open=event.candle_data["open"],
                    high=event.candle_data["high"],
                    low=event.candle_data["low"],
                    close=event.candle_data["close"],
                    volume=event.candle_data.get("volume", 0),
                )
                last_candle = candle

                # 月変わり検出
                candle_month = (event.timestamp.year, event.timestamp.month)
                if current_month is None:
                    current_month = candle_month
                    month_start_balance = simulator.state.balance
                elif candle_month != current_month:
                    # 月末処理
                    month_pnl = simulator.state.balance - month_start_balance
                    month_return = month_pnl / month_start_balance * 100
                    month_result = {
                        "year": current_month[0],
                        "month": current_month[1],
                        "trades": month_trades,
                        "pnl": month_pnl,
                        "return_pct": month_return,
                    }
                    monthly_results.append(month_result)

                    if self._emitter:
                        self._emitter.emit_month_end(month_result)

                    current_month = candle_month
                    month_start_balance = simulator.state.balance
                    month_trades = 0

                # 評価結果からシグナルを構築
                signal = None

                if mode_aware_result is not None:
                    # 優先度ベース評価: best_entry_tf足確定時のみエントリー
                    if (
                        mode_aware_result.should_enter
                        and event.timeframe == mode_aware_result.best_entry_tf
                    ):
                        signal = self._build_signal_from_mode_aware(
                            mode_aware_result, candle, event
                        )
                else:
                    # 従来評価
                    eval_result = eval_results.get(event.timeframe)
                    if eval_result and eval_result.direction != "HOLD":
                        if eval_result.confidence >= self._config.min_confidence:
                            signal = self._build_signal_from_eval_result(
                                eval_result, candle, event
                            )

                # シミュレーター処理
                prev_positions = simulator.get_open_positions()
                prev_trade_count = len(simulator.get_closed_trades())
                simulator.process_candle(candle, signal)

                # 新規ポジション検出
                if self._emitter:
                    current_positions = simulator.get_open_positions()
                    for pos in current_positions:
                        if pos not in prev_positions:
                            self._emitter.emit_trade_opened(
                                trade_id=pos.position_id,
                                symbol=pos.symbol,
                                direction=pos.signal_type.value,
                                entry_price=pos.entry_price,
                                volume=pos.volume,
                                candle_time=event.timestamp,
                            )

                # 決済検出
                closed_trades = simulator.get_closed_trades()
                if len(closed_trades) > prev_trade_count:
                    month_trades += 1
                    new_trade = closed_trades[-1]
                    pnl = new_trade.profit_loss or 0

                    if pnl > 0:
                        winning_trades += 1
                    else:
                        losing_trades += 1

                    if self._emitter:
                        self._emitter.emit_trade_closed(
                            trade_id=new_trade.trade_id,
                            symbol=new_trade.symbol,
                            direction=new_trade.signal_type.value,
                            entry_price=new_trade.entry_price,
                            exit_price=new_trade.exit_price or 0,
                            volume=new_trade.volume,
                            profit_loss=pnl,
                            exit_reason=(
                                new_trade.exit_reason.value
                                if new_trade.exit_reason
                                else "UNKNOWN"
                            ),
                            candle_time=event.timestamp,
                            opened_at=new_trade.opened_at,
                            position_id=(
                                new_trade.position_id or ""
                            ),
                        )

                        # メトリクス発行
                        total_trades = len(closed_trades)
                        self._emitter.emit_metrics(
                            balance=simulator.state.balance,
                            equity=simulator.state.balance,
                            total_trades=total_trades,
                            winning_trades=winning_trades,
                            losing_trades=losing_trades,
                            max_drawdown=simulator.state.max_drawdown * 100,
                        )

            # 進捗イベント
            if event_count % self._config.progress_interval == 0:
                elapsed = time.time() - start_time

                if self._emitter:
                    self._emitter.emit_progress(
                        current=event_count,
                        total=total_events,
                        elapsed=elapsed,
                        message="マルチTFバックテスト処理中",
                    )

                # キャンセルチェック
                if self._cancel_callback and self._cancel_callback():
                    if self._emitter:
                        self._emitter.emit_backtest_end({"cancelled": True})
                    return UnifiedEngineResult(cancelled=True)

        # 強制決済
        if last_candle:
            simulator.force_close_all(last_candle, ExitReason.FORCE_CLOSE)

        # 最終月の結果
        if current_month:
            month_pnl = simulator.state.balance - month_start_balance
            month_return = month_pnl / month_start_balance * 100
            month_result = {
                "year": current_month[0],
                "month": current_month[1],
                "trades": month_trades,
                "pnl": month_pnl,
                "return_pct": month_return,
            }
            monthly_results.append(month_result)

            if self._emitter:
                self._emitter.emit_month_end(month_result)

        # メトリクス計算
        trades = simulator.get_closed_trades()
        calculator = MetricsCalculator(
            initial_balance=self._sim_config.initial_balance
        )
        metrics = calculator.calculate(trades, simulator.state.daily_pnl)

        execution_time = time.time() - start_time

        return UnifiedEngineResult(
            trades=trades,
            monthly_results=monthly_results,
            metrics=metrics,
            final_balance=simulator.state.balance,
            execution_time=execution_time,
            cancelled=False,
        )

    def _extract_h1_row_data(
        self,
        event_batch: list,
    ) -> dict[str, float] | None:
        """イベントバッチからH1データを抽出

        Args:
            event_batch: イベントバッチ

        Returns:
            dict[str, float] | None: H1行データ
        """
        for event in event_batch:
            if event.timeframe == "H1":
                return event.row_data
        return None

    def _build_signal_from_mode_aware(
        self,
        result: any,
        candle: Candle,
        event: any,
    ) -> Signal | None:
        """優先度ベース評価結果からシグナルを構築

        Args:
            result: PriorityEvaluationResult
            candle: キャンドル
            event: CandleEvent

        Returns:
            Signal | None: シグナル
        """
        if result.consensus_direction == "HOLD":
            return None

        signal_type = SignalType(result.consensus_direction)

        # 価格からSL/TPを計算
        close_price = candle.close
        pip_unit = 0.01 if "JPY" in self._config.symbol else 0.0001

        if signal_type == SignalType.BUY:
            sl_price = close_price - result.sl_pips * pip_unit
            tp_price = close_price + result.tp_pips * pip_unit
        else:
            sl_price = close_price + result.sl_pips * pip_unit
            tp_price = close_price - result.tp_pips * pip_unit

        # weighted_scoreを確度に変換（10.0でスケール）
        confidence = min(result.weighted_score / 10.0, 1.0)

        signal = Signal(
            symbol=self._config.symbol,
            timeframe=Timeframe(event.timeframe),
            signal_type=signal_type,
            confidence=confidence,
            stop_loss=sl_price,
            take_profit=tp_price,
            reasoning=result.reasoning,
        )

        # シグナルイベント発行
        if self._emitter:
            aligned_tfs = list(result.tf_results.keys())
            self._emitter.emit_signal(
                signal_type=signal_type.value,
                symbol=self._config.symbol,
                timeframe=event.timeframe,
                confidence=confidence,
                sl_pips=result.sl_pips,
                tp_pips=result.tp_pips,
                rationale=result.reasoning,
                aligned_timeframes=aligned_tfs,
                candle_time=event.timestamp,
            )

        return signal

    def _build_signal_from_eval_result(
        self,
        eval_result: any,
        candle: Candle,
        event: any,
    ) -> Signal | None:
        """従来評価結果からシグナルを構築

        Args:
            eval_result: EvaluationResult
            candle: キャンドル
            event: CandleEvent

        Returns:
            Signal | None: シグナル
        """
        signal_type = SignalType(eval_result.direction)

        # 価格からSL/TPを計算
        close_price = candle.close
        pip_unit = 0.01 if "JPY" in self._config.symbol else 0.0001

        if signal_type == SignalType.BUY:
            sl_price = close_price - eval_result.sl_pips * pip_unit
            tp_price = close_price + eval_result.tp_pips * pip_unit
        else:
            sl_price = close_price + eval_result.sl_pips * pip_unit
            tp_price = close_price - eval_result.tp_pips * pip_unit

        signal = Signal(
            symbol=self._config.symbol,
            timeframe=Timeframe(event.timeframe),
            signal_type=signal_type,
            confidence=min(eval_result.confidence, 1.0),
            stop_loss=sl_price,
            take_profit=tp_price,
            reasoning=eval_result.reason,
        )

        # シグナルイベント発行
        if self._emitter:
            self._emitter.emit_signal(
                signal_type=signal_type.value,
                symbol=self._config.symbol,
                timeframe=event.timeframe,
                confidence=eval_result.confidence,
                sl_pips=eval_result.sl_pips,
                tp_pips=eval_result.tp_pips,
                rationale=eval_result.reason,
                aligned_timeframes=[event.timeframe],
                candle_time=event.timestamp,
            )

        return signal
