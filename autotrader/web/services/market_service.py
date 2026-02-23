"""市場データサービス"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from autotrader.adapters.database.models import TradeRecord
from autotrader.core.enums import ExitReason, Timeframe
from autotrader.web.schemas import (
    DashboardResponse,
    PositionResponse,
    TradeResponse,
    TradeSummaryResponse,
    IndicatorResponse,
    CandleResponse,
)
from autotrader.web.schemas.responses import AccountInfoResponse
from autotrader.web.services.candle_service import CandleService


def _parse_exit_reason(
    value: str | None,
) -> ExitReason | None:
    """DBのexit_reason文字列をExitReasonに変換。

    Args:
        value: DB保存値

    Returns:
        ExitReason | None: 変換結果（無効値はNone）
    """
    if value is None:
        return None
    try:
        return ExitReason(value)
    except ValueError:
        return None


class MarketService:
    """市場データサービス

    Attributes:
        db: DBセッション
    """

    def __init__(self, db: Session) -> None:
        """初期化

        Args:
            db: DBセッション
        """
        self._db = db
        self._candle_service = CandleService()

    def get_dashboard(
        self,
        account_override: AccountInfoResponse | None = None,
    ) -> DashboardResponse:
        """ダッシュボード情報を取得

        Args:
            account_override: MT5リアル口座情報（優先使用）

        Returns:
            DashboardResponse: ダッシュボード情報
        """
        # MT5接続時はリアルデータ、未接続時はデフォルト値
        account = account_override or AccountInfoResponse(
            balance=1_000_000.0,
            equity=1_000_000.0,
        )

        # 本日のトレード集計
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_trades = (
            self._db.query(TradeRecord)
            .filter(
                TradeRecord.closed_at >= today,
                TradeRecord.is_open.is_(False),
            )
            .all()
        )

        daily_pnl = sum(
            t.profit_loss or 0.0 for t in today_trades
        )
        wins = sum(
            1 for t in today_trades if (t.profit_loss or 0) > 0
        )
        total = len(today_trades)
        win_rate = (wins / total * 100) if total > 0 else 0.0

        # オープンポジション数
        open_count = (
            self._db.query(TradeRecord)
            .filter(TradeRecord.is_open.is_(True))
            .count()
        )

        return DashboardResponse(
            account=account,
            daily_pnl=daily_pnl,
            daily_pnl_pct=(
                daily_pnl / account.balance * 100
                if account.balance > 0
                else 0.0
            ),
            active_signals=0,
            open_positions=open_count,
            today_trades=total,
            win_rate=win_rate,
        )

    def get_positions(
        self, symbol: str | None
    ) -> list[PositionResponse]:
        """オープンポジションを取得

        Args:
            symbol: 通貨ペア

        Returns:
            list[PositionResponse]: ポジション一覧
        """
        query = self._db.query(TradeRecord).filter(
            TradeRecord.is_open.is_(True)
        )
        if symbol:
            query = query.filter(TradeRecord.symbol == symbol)

        trades = query.all()
        return [
            PositionResponse(
                position_id=t.trade_id,
                symbol=t.symbol,
                signal_type=t.signal_type,
                volume=t.volume,
                entry_price=t.entry_price,
                stop_loss=t.stop_loss,
                take_profit=t.take_profit,
                opened_at=t.opened_at,
            )
            for t in trades
        ]

    def get_trades(
        self, symbol: str | None, limit: int, offset: int
    ) -> list[TradeResponse]:
        """トレード履歴を取得

        Args:
            symbol: 通貨ペア
            limit: 取得件数
            offset: オフセット

        Returns:
            list[TradeResponse]: トレード履歴
        """
        query = self._db.query(TradeRecord)
        if symbol:
            query = query.filter(TradeRecord.symbol == symbol)

        trades = (
            query.order_by(TradeRecord.opened_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            TradeResponse(
                trade_id=t.trade_id,
                ticket=t.ticket or 0,
                is_open=t.is_open,
                symbol=t.symbol,
                signal_type=t.signal_type,
                volume=t.volume,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                stop_loss=t.stop_loss,
                take_profit=t.take_profit,
                profit_loss=t.profit_loss,
                profit_loss_pips=t.profit_loss_pips,
                exit_reason=_parse_exit_reason(t.exit_reason),
                opened_at=t.opened_at,
                closed_at=t.closed_at,
            )
            for t in trades
        ]

    def get_trade_summary(
        self, symbol: str | None, days: int
    ) -> TradeSummaryResponse:
        """トレードサマリーを取得

        Args:
            symbol: 通貨ペア
            days: 集計日数

        Returns:
            TradeSummaryResponse: トレードサマリー
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        query = self._db.query(TradeRecord).filter(
            TradeRecord.is_open.is_(False),
            TradeRecord.closed_at >= since,
        )
        if symbol:
            query = query.filter(TradeRecord.symbol == symbol)

        trades = query.all()
        if not trades:
            return TradeSummaryResponse()

        wins = [
            t for t in trades if (t.profit_loss or 0) > 0
        ]
        losses = [
            t for t in trades if (t.profit_loss or 0) <= 0
        ]
        total_profit = sum(t.profit_loss or 0 for t in wins)
        total_loss = sum(t.profit_loss or 0 for t in losses)

        return TradeSummaryResponse(
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=(
                len(wins) / len(trades) * 100
                if trades
                else 0.0
            ),
            total_profit=total_profit,
            total_loss=total_loss,
            net_profit=total_profit + total_loss,
            profit_factor=(
                abs(total_profit / total_loss)
                if total_loss != 0
                else 0.0
            ),
            average_win=(
                total_profit / len(wins) if wins else 0.0
            ),
            average_loss=(
                total_loss / len(losses) if losses else 0.0
            ),
        )

    def get_indicators(
        self, symbol: str, timeframe: Timeframe
    ) -> IndicatorResponse:
        """指標スナップショットを取得

        Args:
            symbol: 通貨ペア
            timeframe: 時間足

        Returns:
            IndicatorResponse: 指標情報
        """
        # ライブモード未接続時はタイムスタンプのみ
        return IndicatorResponse(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(timezone.utc),
        )

    def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
        end_time: datetime | None = None,
    ) -> list[CandleResponse]:
        """ローソク足データを取得

        Args:
            symbol: 通貨ペア
            timeframe: 時間足
            limit: 取得本数
            end_time: この時刻より前のデータを取得

        Returns:
            list[CandleResponse]: ローソク足一覧
        """
        return self._candle_service.get_candles(
            symbol=symbol,
            timeframe=timeframe.value,
            limit=limit,
            end_time=end_time,
        )
