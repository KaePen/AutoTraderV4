"""シグナルサービス"""

from __future__ import annotations

from sqlalchemy.orm import Session

from autotrader.adapters.database.repositories import (
    SignalRepository,
)
from autotrader.web.schemas import SignalResponse


class SignalService:
    """シグナルサービス

    Attributes:
        db: DBセッション
    """

    def __init__(self, db: Session) -> None:
        """初期化

        Args:
            db: DBセッション
        """
        self._db = db
        self._signal_repo = SignalRepository(db)

    def get_current_signals(
        self, symbol: str
    ) -> list[SignalResponse]:
        """現在のシグナルを取得

        Args:
            symbol: 通貨ペア

        Returns:
            list[SignalResponse]: シグナル一覧
        """
        records = self._signal_repo.get_recent(
            symbol=symbol, limit=5
        )
        return [
            SignalResponse(
                signal_id=r.signal_id,
                symbol=r.symbol,
                timeframe=r.timeframe,
                signal_type=r.signal_type,
                confidence=r.confidence,
                confidence_level=(
                    "HIGH"
                    if r.confidence >= 0.7
                    else "MEDIUM"
                    if r.confidence >= 0.4
                    else "LOW"
                ),
                stop_loss=r.stop_loss_price,
                take_profit=r.target_price,
                reasoning=r.reasoning or "",
                created_at=r.created_at,
                indicators_snapshot=(
                    r.indicators_snapshot or {}
                ),
            )
            for r in records
        ]

    def get_signal_history(
        self, symbol: str, limit: int, offset: int
    ) -> list[SignalResponse]:
        """シグナル履歴を取得

        Args:
            symbol: 通貨ペア
            limit: 取得件数
            offset: オフセット

        Returns:
            list[SignalResponse]: シグナル履歴
        """
        records = self._signal_repo.get_recent(
            symbol=symbol, limit=limit
        )
        return [
            SignalResponse(
                signal_id=r.signal_id,
                symbol=r.symbol,
                timeframe=r.timeframe,
                signal_type=r.signal_type,
                confidence=r.confidence,
                confidence_level=(
                    "HIGH"
                    if r.confidence >= 0.7
                    else "MEDIUM"
                    if r.confidence >= 0.4
                    else "LOW"
                ),
                stop_loss=r.stop_loss_price,
                take_profit=r.target_price,
                reasoning=r.reasoning or "",
                created_at=r.created_at,
                indicators_snapshot=(
                    r.indicators_snapshot or {}
                ),
            )
            for r in records
        ]
