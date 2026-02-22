"""リポジトリパターン実装"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import (
    TradeRecord,
    AuditLog,
    EconomicEventRecord,
    MarketMemoryRecord,
)

if TYPE_CHECKING:
    from autotrader.adapters.fundamental.schemas import EconomicEvent


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


class EconomicEventRepository:
    """経済イベントリポジトリ"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, event: "EconomicEvent") -> EconomicEventRecord:
        """経済イベントを作成

        Args:
            event: EconomicEventデータクラス

        Returns:
            EconomicEventRecord: 作成済みレコード
        """
        record = EconomicEventRecord(
            event_id=event.event_id,
            event_time=event.event_time,
            currency=event.currency,
            symbol=event.symbol,
            event_name=event.event_name,
            impact=event.impact.value,
            actual=event.actual,
            forecast=event.forecast,
            previous=event.previous,
            source=event.source.value,
            fetched_at=event.fetched_at,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def exists(self, event_id: str) -> bool:
        """指定IDのイベントが存在するか確認

        Args:
            event_id: イベントID

        Returns:
            bool: 存在すればTrue
        """
        return (
            self.session.query(EconomicEventRecord)
            .filter(EconomicEventRecord.event_id == event_id)
            .count()
        ) > 0

    def get_upcoming(
        self,
        from_time: datetime,
        to_time: datetime,
        currencies: list[str] | None = None,
        impact: str | None = None,
    ) -> list[EconomicEventRecord]:
        """指定期間の経済イベントを取得

        Args:
            from_time: 取得開始時刻
            to_time: 取得終了時刻
            currencies: 通貨フィルタ
            impact: インパクトフィルタ（high/medium/low）

        Returns:
            list[EconomicEventRecord]: イベントリスト
        """
        query = (
            self.session.query(EconomicEventRecord)
            .filter(
                EconomicEventRecord.event_time >= from_time,
                EconomicEventRecord.event_time <= to_time,
            )
        )
        if currencies:
            query = query.filter(
                EconomicEventRecord.currency.in_(currencies)
            )
        if impact:
            query = query.filter(
                EconomicEventRecord.impact == impact
            )
        return (
            query.order_by(EconomicEventRecord.event_time).all()
        )


class MarketMemoryRepository:
    """市場記憶リポジトリ"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        memory_id: str,
        symbol: str,
        memory_type: str,
        direction_score: float,
        confidence: float,
        valid_until: datetime,
        summary: str | None = None,
        source_event: str | None = None,
        llm_reasoning: str | None = None,
    ) -> MarketMemoryRecord:
        """市場記憶を作成

        Args:
            memory_id: 記憶UUID
            symbol: シンボル
            memory_type: 記憶タイプ
            direction_score: 方向性スコア (-1.0〜+1.0)
            confidence: 確信度 (0.0〜1.0)
            valid_until: 有効期限
            summary: 要約文
            source_event: ソースイベント名
            llm_reasoning: LLM推論根拠

        Returns:
            MarketMemoryRecord: 作成済みレコード
        """
        record = MarketMemoryRecord(
            memory_id=memory_id,
            symbol=symbol,
            memory_type=memory_type,
            direction_score=direction_score,
            confidence=confidence,
            valid_until=valid_until,
            summary=summary,
            source_event=source_event,
            llm_reasoning=llm_reasoning,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get_active(
        self,
        symbol: str,
        memory_type: str,
        now: datetime,
    ) -> list[MarketMemoryRecord]:
        """有効な市場記憶を取得

        Args:
            symbol: シンボル
            memory_type: 記憶タイプ
            now: 現在時刻（UTC）

        Returns:
            list[MarketMemoryRecord]: 有効な記憶リスト
        """
        return (
            self.session.query(MarketMemoryRecord)
            .filter(
                MarketMemoryRecord.symbol == symbol,
                MarketMemoryRecord.memory_type == memory_type,
                MarketMemoryRecord.valid_until > now,
            )
            .order_by(MarketMemoryRecord.created_at.desc())
            .all()
        )

    def delete_expired(self, now: datetime) -> int:
        """期限切れ記憶を削除

        Args:
            now: 現在時刻（UTC）

        Returns:
            int: 削除件数
        """
        result = (
            self.session.query(MarketMemoryRecord)
            .filter(MarketMemoryRecord.valid_until <= now)
            .delete()
        )
        self.session.flush()
        return result
