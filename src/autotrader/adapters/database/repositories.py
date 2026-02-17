"""リポジトリパターン実装"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import (
    SignalRecord,
    TradeRecord,
    BacktestResult,
    AuditLog,
)


class SignalRepository:
    """シグナルリポジトリ"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        symbol: str,
        timeframe: str,
        signal_type: str,
        confidence: float,
        target_price: float | None = None,
        stop_loss_price: float | None = None,
        reasoning: str | None = None,
        indicators_snapshot: dict | None = None,
        constraint_result: dict | None = None,
    ) -> SignalRecord:
        """シグナルを作成

        Args:
            symbol: シンボル
            timeframe: 時間足
            signal_type: シグナル種別
            confidence: 確度
            target_price: 目標価格
            stop_loss_price: 損切価格
            reasoning: 理由
            indicators_snapshot: 指標スナップショット
            constraint_result: 制約結果

        Returns:
            SignalRecord: 作成されたシグナル
        """
        signal = SignalRecord(
            signal_id=str(uuid4()),
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            confidence=confidence,
            target_price=target_price,
            stop_loss_price=stop_loss_price,
            reasoning=reasoning,
            indicators_snapshot=indicators_snapshot,
            constraint_result=constraint_result,
        )
        self.session.add(signal)
        self.session.flush()
        return signal

    def get_by_id(self, signal_id: str) -> SignalRecord | None:
        """IDでシグナルを取得

        Args:
            signal_id: シグナルID

        Returns:
            SignalRecord | None: シグナル
        """
        return (
            self.session.query(SignalRecord)
            .filter(SignalRecord.signal_id == signal_id)
            .first()
        )

    def get_recent(
        self, symbol: str, limit: int = 100
    ) -> list[SignalRecord]:
        """最近のシグナルを取得

        Args:
            symbol: シンボル
            limit: 取得件数

        Returns:
            list[SignalRecord]: シグナルリスト
        """
        return (
            self.session.query(SignalRecord)
            .filter(SignalRecord.symbol == symbol)
            .order_by(SignalRecord.created_at.desc())
            .limit(limit)
            .all()
        )


class TradeRepository:
    """トレードリポジトリ"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        symbol: str,
        signal_type: str,
        volume: float,
        entry_price: float,
        opened_at: datetime,
        signal_id: str | None = None,
        backtest_id: int | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> TradeRecord:
        """トレードを作成

        Args:
            symbol: シンボル
            signal_type: シグナル種別
            volume: ロット数
            entry_price: エントリー価格
            opened_at: オープン時刻
            signal_id: シグナルID
            backtest_id: バックテストID
            stop_loss: 損切価格
            take_profit: 利確価格

        Returns:
            TradeRecord: 作成されたトレード
        """
        trade = TradeRecord(
            trade_id=str(uuid4()),
            signal_id=signal_id,
            backtest_id=backtest_id,
            symbol=symbol,
            signal_type=signal_type,
            volume=volume,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            opened_at=opened_at,
            is_open=True,
        )
        self.session.add(trade)
        self.session.flush()
        return trade

    def close(
        self,
        trade: TradeRecord,
        exit_price: float,
        closed_at: datetime,
        exit_reason: str,
        profit_loss: float,
        profit_loss_pips: float | None = None,
    ) -> TradeRecord:
        """トレードを決済

        Args:
            trade: トレード
            exit_price: 決済価格
            closed_at: 決済時刻
            exit_reason: 決済理由
            profit_loss: 損益
            profit_loss_pips: 損益（pips）

        Returns:
            TradeRecord: 更新されたトレード
        """
        trade.exit_price = exit_price
        trade.closed_at = closed_at
        trade.exit_reason = exit_reason
        trade.profit_loss = profit_loss
        trade.profit_loss_pips = profit_loss_pips
        trade.is_open = False
        self.session.flush()
        return trade

    def get_by_id(self, trade_id: str) -> TradeRecord | None:
        """IDでトレードを取得

        Args:
            trade_id: トレードID

        Returns:
            TradeRecord | None: トレード
        """
        return (
            self.session.query(TradeRecord)
            .filter(TradeRecord.trade_id == trade_id)
            .first()
        )

    def get_open_trades(self, symbol: str | None = None) -> list[TradeRecord]:
        """オープントレードを取得

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[TradeRecord]: トレードリスト
        """
        query = self.session.query(TradeRecord).filter(
            TradeRecord.is_open == True
        )
        if symbol:
            query = query.filter(TradeRecord.symbol == symbol)
        return query.all()

    def get_by_backtest(self, backtest_id: int) -> list[TradeRecord]:
        """バックテストIDでトレードを取得

        Args:
            backtest_id: バックテストID

        Returns:
            list[TradeRecord]: トレードリスト
        """
        return (
            self.session.query(TradeRecord)
            .filter(TradeRecord.backtest_id == backtest_id)
            .order_by(TradeRecord.opened_at)
            .all()
        )


class BacktestRepository:
    """バックテストリポジトリ"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        name: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        end_date: datetime,
        config: dict | None = None,
        description: str | None = None,
    ) -> BacktestResult:
        """バックテスト結果を作成

        Args:
            name: 名前
            symbol: シンボル
            timeframe: 時間足
            start_date: 開始日
            end_date: 終了日
            config: 設定
            description: 説明

        Returns:
            BacktestResult: 作成されたバックテスト結果
        """
        result = BacktestResult(
            name=name,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            config=config,
            description=description,
            status="running",
        )
        self.session.add(result)
        self.session.flush()
        return result

    def update_metrics(
        self,
        backtest: BacktestResult,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        total_profit: float,
        total_loss: float,
        max_drawdown: float,
        max_drawdown_pct: float,
        sharpe_ratio: float | None = None,
        avg_trade_duration: float | None = None,
        daily_stats: dict | None = None,
        equity_curve: list | None = None,
    ) -> BacktestResult:
        """メトリクスを更新

        Args:
            backtest: バックテスト結果
            total_trades: 総トレード数
            winning_trades: 勝ちトレード数
            losing_trades: 負けトレード数
            total_profit: 総利益
            total_loss: 総損失
            max_drawdown: 最大DD
            max_drawdown_pct: 最大DD%
            sharpe_ratio: シャープレシオ
            avg_trade_duration: 平均保有時間
            daily_stats: 日次統計
            equity_curve: エクイティカーブ

        Returns:
            BacktestResult: 更新されたバックテスト結果
        """
        backtest.total_trades = total_trades
        backtest.winning_trades = winning_trades
        backtest.losing_trades = losing_trades
        backtest.total_profit = total_profit
        backtest.total_loss = total_loss
        backtest.net_profit = total_profit + total_loss
        backtest.max_drawdown = max_drawdown
        backtest.max_drawdown_pct = max_drawdown_pct
        backtest.sharpe_ratio = sharpe_ratio
        backtest.avg_trade_duration = avg_trade_duration
        backtest.daily_stats = daily_stats
        backtest.equity_curve = equity_curve

        # 計算フィールド
        if total_trades > 0:
            backtest.win_rate = winning_trades / total_trades
            backtest.avg_profit_per_trade = backtest.net_profit / total_trades
        if total_loss != 0:
            backtest.profit_factor = abs(total_profit / total_loss)
        if backtest.win_rate and backtest.avg_profit_per_trade:
            avg_win = total_profit / winning_trades if winning_trades > 0 else 0
            avg_loss = abs(total_loss / losing_trades) if losing_trades > 0 else 0
            backtest.expectancy = (
                backtest.win_rate * avg_win
                - (1 - backtest.win_rate) * avg_loss
            )

        backtest.status = "completed"
        backtest.completed_at = datetime.utcnow()
        self.session.flush()
        return backtest

    def get_by_id(self, backtest_id: int) -> BacktestResult | None:
        """IDでバックテスト結果を取得

        Args:
            backtest_id: バックテストID

        Returns:
            BacktestResult | None: バックテスト結果
        """
        return (
            self.session.query(BacktestResult)
            .filter(BacktestResult.id == backtest_id)
            .first()
        )

    def get_recent(self, limit: int = 10) -> list[BacktestResult]:
        """最近のバックテスト結果を取得

        Args:
            limit: 取得件数

        Returns:
            list[BacktestResult]: バックテスト結果リスト
        """
        return (
            self.session.query(BacktestResult)
            .order_by(BacktestResult.created_at.desc())
            .limit(limit)
            .all()
        )


class AuditRepository:
    """監査ログリポジトリ"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def log(
        self,
        event_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        reason: str | None = None,
        user: str = "system",
    ) -> AuditLog:
        """監査ログを記録

        Args:
            event_type: イベント種別
            entity_type: エンティティ種別
            entity_id: エンティティID
            before_state: 変更前状態
            after_state: 変更後状態
            reason: 理由
            user: ユーザー

        Returns:
            AuditLog: 作成されたログ
        """
        log = AuditLog(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
            user=user,
        )
        self.session.add(log)
        self.session.flush()
        return log
