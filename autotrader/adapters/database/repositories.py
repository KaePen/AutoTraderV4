"""リポジトリパターン実装"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import (
    MarketMemoryRecord,
    PositionStateRecord,
    TradeRecord,
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
            TradeRecord.is_open.is_(True)
        )
        if symbol:
            query = query.filter(TradeRecord.symbol == symbol)
        return query.all()


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


class PositionStateRepository:
    """ポジション管理状態リポジトリ（ローカルSQLite用）"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, state: dict) -> None:
        """管理状態をINSERT or UPDATE

        Args:
            state: position_idを含む状態dict
        """
        pos_id = state["position_id"]
        record = (
            self.session.query(PositionStateRecord)
            .filter(
                PositionStateRecord.position_id == pos_id
            )
            .first()
        )
        if record is None:
            record = PositionStateRecord(
                position_id=pos_id,
            )
            self.session.add(record)

        # 追跡値
        record.highest_price = state.get(
            "highest_price", 0.0
        )
        record.lowest_price = state.get(
            "lowest_price", 0.0
        )
        record.highest_r = state.get("highest_r", 0.0)
        record.bars_held = state.get("bars_held", 0)
        record.trailing_activated = state.get(
            "trailing_activated", False
        )
        # 管理フラグ
        record.partial_closed_1r = state.get(
            "partial_closed_1r", False
        )
        record.partial_closed_2r = state.get(
            "partial_closed_2r", False
        )
        record.tp_disabled = state.get(
            "tp_disabled", False
        )
        record.early_be_applied = state.get(
            "early_be_applied", False
        )
        record.insurance_sl_applied = state.get(
            "insurance_sl_applied", False
        )
        record.insurance_partial_applied = state.get(
            "insurance_partial_applied", False
        )
        record.half_r_partial_applied = state.get(
            "half_r_partial_applied", False
        )
        self.session.flush()

    def get_by_position_id(
        self, position_id: str,
    ) -> PositionStateRecord | None:
        """ポジションIDで管理状態を取得

        Args:
            position_id: ポジションID

        Returns:
            PositionStateRecord | None: レコード
        """
        return (
            self.session.query(PositionStateRecord)
            .filter(
                PositionStateRecord.position_id
                == position_id
            )
            .first()
        )

    def get_all(self) -> list[PositionStateRecord]:
        """全管理状態を取得

        Returns:
            list[PositionStateRecord]: 全レコード
        """
        return (
            self.session.query(PositionStateRecord).all()
        )

    def delete(self, position_id: str) -> None:
        """管理状態を削除（ポジションクローズ時）

        Args:
            position_id: ポジションID
        """
        self.session.query(PositionStateRecord).filter(
            PositionStateRecord.position_id == position_id
        ).delete()
        self.session.flush()
