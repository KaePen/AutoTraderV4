"""リポジトリパターン実装"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import (
    PositionStateRecord,
    TradeRecord,
)
from autotrader.core.entities import Trade
from autotrader.core.enums import ExitReason, SignalType


class TradeRepository:
    """トレードリポジトリ

    ORM Model (TradeRecord) とドメインエンティティ (Trade) の
    変換を内部で行い、外部にはドメインエンティティのみを公開する。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _to_entity(record: TradeRecord) -> Trade:
        """ORM Model → Domain Entity 変換

        Args:
            record: TradeRecord ORM モデル

        Returns:
            Trade: ドメインエンティティ
        """
        # signal_type: str → SignalType enum
        signal_type = SignalType(record.signal_type)

        # exit_reason: str | None → ExitReason | None
        exit_reason: ExitReason | None = None
        if record.exit_reason is not None:
            try:
                exit_reason = ExitReason(record.exit_reason)
            except ValueError:
                exit_reason = None

        return Trade(
            trade_id=record.trade_id,
            ticket=record.ticket or 0,
            symbol=record.symbol,
            signal_type=signal_type,
            volume=record.volume,
            entry_price=record.entry_price,
            exit_price=record.exit_price,
            stop_loss=record.stop_loss,
            take_profit=record.take_profit,
            profit_loss=record.profit_loss,
            profit_loss_pips=record.profit_loss_pips,
            exit_reason=exit_reason,
            opened_at=record.opened_at,
            closed_at=record.closed_at,
        )

    @staticmethod
    def _to_record(trade: Trade) -> TradeRecord:
        """Domain Entity → ORM Model 変換

        Args:
            trade: ドメインエンティティ

        Returns:
            TradeRecord: ORM モデル
        """
        return TradeRecord(
            trade_id=trade.trade_id,
            ticket=trade.ticket or None,
            symbol=trade.symbol,
            signal_type=trade.signal_type.value,
            volume=trade.volume,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            stop_loss=trade.stop_loss,
            take_profit=trade.take_profit,
            profit_loss=trade.profit_loss,
            profit_loss_pips=trade.profit_loss_pips,
            exit_reason=(
                trade.exit_reason.value
                if trade.exit_reason
                else None
            ),
            opened_at=trade.opened_at,
            closed_at=trade.closed_at,
            is_open=trade.closed_at is None,
        )

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
        entry_own_score: float = 0.0,
    ) -> Trade:
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
            entry_own_score: エントリー時のエッジスコア

        Returns:
            Trade: 作成されたトレード
        """
        record = TradeRecord(
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
            entry_own_score=entry_own_score,
        )
        self.session.add(record)
        self.session.flush()
        return self._to_entity(record)

    def close(
        self,
        trade_id: str,
        exit_price: float,
        closed_at: datetime,
        exit_reason: str,
        profit_loss: float,
        profit_loss_pips: float | None = None,
        final_stop_loss: float | None = None,
        final_take_profit: float | None = None,
    ) -> Trade | None:
        """トレードを決済

        Args:
            trade_id: トレードID
            exit_price: 決済価格
            closed_at: 決済時刻
            exit_reason: 決済理由
            profit_loss: 損益
            profit_loss_pips: 損益（pips）
            final_stop_loss: 決済時点の最終SL価格
            final_take_profit: 決済時点の最終TP価格

        Returns:
            Trade | None: 更新されたトレード（未発見時None）
        """
        record = (
            self.session.query(TradeRecord)
            .filter(TradeRecord.trade_id == trade_id)
            .first()
        )
        if record is None:
            return None
        record.exit_price = exit_price
        record.closed_at = closed_at
        record.exit_reason = exit_reason
        record.profit_loss = profit_loss
        record.profit_loss_pips = profit_loss_pips
        # トレーリング/BE移動後の最終SL/TPでDB更新
        if final_stop_loss is not None:
            record.stop_loss = final_stop_loss
        if final_take_profit is not None:
            record.take_profit = final_take_profit
        record.is_open = False
        self.session.flush()
        return self._to_entity(record)

    def update_volume(
        self,
        trade_id: str,
        new_volume: float,
        partial_profit: float = 0.0,
    ) -> None:
        """部分決済後のボリューム更新

        部分決済でロットが減少した際にDBを同期更新する。
        partial_profitは確定済み部分損益を加算する。

        Args:
            trade_id: トレードID
            new_volume: 更新後のボリューム
            partial_profit: 部分決済の確定損益
        """
        record = (
            self.session.query(TradeRecord)
            .filter(TradeRecord.trade_id == trade_id)
            .first()
        )
        if record is None:
            return
        record.volume = round(new_volume, 2)
        if partial_profit != 0.0:
            current_pnl = record.profit_loss or 0.0
            record.profit_loss = round(
                current_pnl + partial_profit, 2
            )
        self.session.flush()

    def get_by_id(self, trade_id: str) -> Trade | None:
        """IDでトレードを取得

        Args:
            trade_id: トレードID

        Returns:
            Trade | None: トレード
        """
        record = (
            self.session.query(TradeRecord)
            .filter(TradeRecord.trade_id == trade_id)
            .first()
        )
        return self._to_entity(record) if record else None

    def get_open_trades(
        self, symbol: str | None = None,
    ) -> list[Trade]:
        """オープントレードを取得

        Args:
            symbol: シンボル（Noneで全て）

        Returns:
            list[Trade]: トレードリスト
        """
        query = self.session.query(TradeRecord).filter(
            TradeRecord.is_open.is_(True)
        )
        if symbol:
            query = query.filter(
                TradeRecord.symbol == symbol
            )
        return [
            self._to_entity(r) for r in query.all()
        ]


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

        # エントリー時スコア（再起動後の劣化計算継続用）
        # 既存値が0より大きい場合のみ上書き（0へのリセットを防ぐ）
        new_score = state.get("entry_own_score", 0.0)
        if new_score > 0:
            record.entry_own_score = new_score

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
