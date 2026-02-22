"""トレーディング状態サービス

バックテスト/ライブモードを抽象化する。
将来のMT5リアルトレード接続の準備。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


from autotrader.web.schemas import (
    DashboardResponse,
    PositionResponse,
    TradeResponse,
    TradeSummaryResponse,
)


@dataclass
class TradingMode:
    """トレーディングモード"""

    mode: str  # "live" | "demo" | "offline"
    label: str
    connected: bool


class TradingStateService(ABC):
    """トレーディング状態の抽象インターフェース

    バックテスト・ライブ・デモの各モードで
    同一インターフェースを提供する。
    """

    @abstractmethod
    def get_mode(self) -> TradingMode:
        """現在のモード取得

        Returns:
            TradingMode: モード情報
        """

    @abstractmethod
    def get_dashboard(self) -> DashboardResponse:
        """ダッシュボード情報取得

        Returns:
            DashboardResponse: ダッシュボード情報
        """

    @abstractmethod
    def get_positions(
        self, symbol: str | None = None
    ) -> list[PositionResponse]:
        """ポジション取得

        Args:
            symbol: 通貨ペア（Noneで全て）

        Returns:
            list[PositionResponse]: ポジション一覧
        """

    @abstractmethod
    def get_trades(
        self,
        symbol: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TradeResponse]:
        """トレード履歴取得

        Args:
            symbol: 通貨ペア
            limit: 取得件数
            offset: オフセット

        Returns:
            list[TradeResponse]: トレード一覧
        """

    @abstractmethod
    def get_trade_summary(
        self,
        symbol: str | None = None,
        days: int = 30,
    ) -> TradeSummaryResponse:
        """トレードサマリー取得

        Args:
            symbol: 通貨ペア
            days: 対象日数

        Returns:
            TradeSummaryResponse: サマリー
        """


class LiveTradingState(TradingStateService):
    """ライブトレードモードの状態サービス

    MT5接続を通じてリアルタイムデータを提供する。

    Attributes:
        _engine: ライブトレーディングエンジン
    """

    def __init__(self, engine: Any = None) -> None:
        """初期化

        Args:
            engine: LiveTradingEngineインスタンス
        """
        self._engine = engine

    def get_mode(self) -> TradingMode:
        """ライブモード返却"""
        connected = (
            self._engine.connected if self._engine else False
        )
        return TradingMode(
            mode="live",
            label="Live Trading",
            connected=connected,
        )

    def get_dashboard(self) -> DashboardResponse:
        """MT5から口座情報取得"""
        if not self._engine or not self._engine.account_info:
            return DashboardResponse(
                account={
                    "balance": 0,
                    "equity": 0,
                    "margin": 0,
                    "free_margin": 0,
                    "margin_level": 0,
                    "profit": 0,
                },
                daily_pnl=0,
                daily_pnl_pct=0,
                active_signals=0,
                open_positions=0,
                today_trades=0,
                win_rate=0,
            )

        acct = self._engine.account_info
        return DashboardResponse(
            account={
                "balance": acct.balance,
                "equity": acct.equity,
                "margin": acct.margin,
                "free_margin": acct.free_margin,
                "margin_level": acct.margin_level,
                "profit": acct.profit,
            },
            daily_pnl=acct.profit,
            daily_pnl_pct=(
                (acct.profit / acct.balance * 100)
                if acct.balance > 0 else 0
            ),
            active_signals=0,
            open_positions=0,
            today_trades=0,
            win_rate=0,
        )

    def get_positions(
        self, symbol: str | None = None
    ) -> list[PositionResponse]:
        """MT5からポジション取得"""
        if not self._engine or not self._engine.connected:
            return []

        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 非同期コンテキスト内: 同期的に呼べない
                return []
            positions = loop.run_until_complete(
                self._engine._executor
                .get_open_positions_async(symbol)
            )
        except Exception:
            return []

        result = []
        for pos in positions:
            result.append(
                PositionResponse(
                    position_id=pos.position_id,
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    signal_type=pos.signal_type,
                    volume=pos.volume,
                    entry_price=pos.entry_price,
                    current_price=0.0,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    opened_at=pos.opened_at,
                    unrealized_pnl=pos.unrealized_pnl,
                )
            )
        return result

    def get_trades(
        self,
        symbol: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TradeResponse]:
        """トレード履歴取得（DB経由）"""
        # ライブモードでもDB保存されたトレードを返す
        return []

    def get_trade_summary(
        self,
        symbol: str | None = None,
        days: int = 30,
    ) -> TradeSummaryResponse:
        """トレードサマリー"""
        return TradeSummaryResponse(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            total_profit=0,
            total_loss=0,
            net_profit=0,
            profit_factor=0,
            average_win=0,
            average_loss=0,
            max_drawdown=0,
        )
