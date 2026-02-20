"""リポジトリパターン実装"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import (
    TradeRecord,
    AuditLog,
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
        stop_loss: float | None = None,
        take_profit: float | None = None,
        ticket: int | None = None,
    ) -> TradeRecord:
        """トレードを作成

        Args:
            symbol: シンボル
            signal_type: シグナル種別
            volume: ロット数
            entry_price: エントリー価格
            opened_at: オープン時刻
            stop_loss: 損切価格
            take_profit: 利確価格
            ticket: MT5チケットID

        Returns:
            TradeRecord: 作成されたトレード
        """
        trade = TradeRecord(
            trade_id=str(uuid4()),
            ticket=ticket,
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
