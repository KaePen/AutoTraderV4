"""市場データサービス"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from autotrader.adapters.database.models import TradeRecord
from autotrader.core.enums import ExitReason, Timeframe
from autotrader.web.schemas import (
    CandleResponse,
    DashboardResponse,
    IndicatorResponse,
    PositionResponse,
    TradeResponse,
    TradeSummaryResponse,
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

        # JST基準で本日の開始時刻を計算
        _jst = timezone(timedelta(hours=9))
        _now_jst = datetime.now(_jst)
        today = _now_jst.replace(
            hour=0, minute=0, second=0, microsecond=0,
        ).astimezone(timezone.utc)

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
        daily_wins = sum(
            1
            for t in today_trades
            if (t.profit_loss or 0) > 0
        )
        daily_total = len(today_trades)
        daily_win_rate = (
            (daily_wins / daily_total * 100)
            if daily_total > 0
            else 0.0
        )

        # 全体勝率
        all_closed = (
            self._db.query(TradeRecord)
            .filter(TradeRecord.is_open.is_(False))
            .count()
        )
        all_wins = (
            self._db.query(TradeRecord)
            .filter(
                TradeRecord.is_open.is_(False),
                TradeRecord.profit_loss > 0,
            )
            .count()
        )
        total_win_rate = (
            (all_wins / all_closed * 100)
            if all_closed > 0
            else 0.0
        )

        # 週間・月間・全履歴の損益集計（JST基準）
        week_start = today - timedelta(
            days=_now_jst.weekday(),
        )
        month_start = _now_jst.replace(
            day=1, hour=0, minute=0, second=0,
            microsecond=0,
        ).astimezone(timezone.utc)

        weekly_pnl = self._db.query(
            func.coalesce(func.sum(TradeRecord.profit_loss), 0.0)
        ).filter(
            TradeRecord.closed_at >= week_start,
            TradeRecord.is_open.is_(False),
        ).scalar()

        monthly_pnl = self._db.query(
            func.coalesce(func.sum(TradeRecord.profit_loss), 0.0)
        ).filter(
            TradeRecord.closed_at >= month_start,
            TradeRecord.is_open.is_(False),
        ).scalar()

        # 全履歴: countとsumを1クエリで
        total_row = self._db.query(
            func.coalesce(
                func.sum(TradeRecord.profit_loss), 0.0
            ),
            func.count(),
        ).filter(TradeRecord.is_open.is_(False)).one()
        total_pnl, total_trades_count = total_row

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
            weekly_pnl=float(weekly_pnl),
            monthly_pnl=float(monthly_pnl),
            total_pnl=float(total_pnl),
            total_trades=int(total_trades_count),
            active_signals=0,
            open_positions=open_count,
            today_trades=daily_total,
            win_rate=daily_win_rate,
            total_win_rate=total_win_rate,
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
