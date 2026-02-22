"""バックテストWebSocketリスナー

バックテストイベントをWebSocketにブリッジする。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, Awaitable

from autotrader.backtest.events import (
    BacktestEvent,
    EventListener,
    EventType,
    ProgressEvent,
    SignalEvent,
    TradeEvent,
    MetricsEvent,
)


class WebSocketEventListener(EventListener):
    """WebSocket用イベントリスナー
    
    バックテストイベントを受け取り、WebSocketにブロードキャストする。
    同期のバックテストループから非同期のWebSocketにブリッジする。
    """
    
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        """初期化
        
        Args:
            loop: イベントループ
        """
        self._loop = loop
        self._callbacks: dict[str, Callable[[dict], Awaitable[None]]] = {}
    
    def set_callback(
        self, 
        event_name: str, 
        callback: Callable[[dict], Awaitable[None]]
    ) -> None:
        """コールバック設定
        
        Args:
            event_name: イベント名
            callback: コールバック関数
        """
        self._callbacks[event_name] = callback
    
    def on_event(self, event: BacktestEvent) -> None:
        """イベント処理
        
        Args:
            event: バックテストイベント
        """
        # 同期→非同期ブリッジ
        loop = self._loop or asyncio.get_event_loop()
        
        if event.event_type == EventType.BACKTEST_START:
            self._schedule_async(loop, "backtest_start", event.data)
        
        elif event.event_type == EventType.BACKTEST_END:
            self._schedule_async(loop, "backtest_end", event.data)
        
        elif event.event_type == EventType.YEAR_START:
            self._schedule_async(loop, "year_start", event.data)
        
        elif event.event_type == EventType.YEAR_END:
            self._schedule_async(loop, "year_end", event.data)
        
        elif event.event_type == EventType.MONTH_END:
            self._schedule_async(loop, "month_end", event.data)
        
        elif event.event_type == EventType.PROGRESS:
            if isinstance(event, ProgressEvent):
                self._schedule_async(loop, "progress", {
                    "current": event.current,
                    "total": event.total,
                    "percentage": event.percentage,
                    "elapsed_seconds": event.elapsed_seconds,
                    "eta_seconds": event.eta_seconds,
                    "message": event.message,
                })
        
        elif event.event_type == EventType.SIGNAL_GENERATED:
            if isinstance(event, SignalEvent):
                self._schedule_async(loop, "signal", {
                    "timestamp": event.timestamp.isoformat(),
                    "signal_type": event.signal_type,
                    "symbol": event.symbol,
                    "timeframe": event.timeframe,
                    "confidence": event.confidence,
                    "sl_pips": event.sl_pips,
                    "tp_pips": event.tp_pips,
                    "rationale": event.rationale,
                    "aligned_timeframes": event.aligned_timeframes,
                })
        
        elif event.event_type == EventType.POSITION_OPENED:
            if isinstance(event, TradeEvent):
                self._schedule_async(loop, "trade_open", {
                    "timestamp": event.timestamp.isoformat(),
                    "trade_id": event.trade_id,
                    "symbol": event.symbol,
                    "direction": event.direction,
                    "entry_price": event.entry_price,
                    "volume": event.volume,
                })
        
        elif event.event_type == EventType.POSITION_CLOSED:
            if isinstance(event, TradeEvent):
                self._schedule_async(loop, "trade_close", {
                    "timestamp": event.timestamp.isoformat(),
                    "trade_id": event.trade_id,
                    "symbol": event.symbol,
                    "direction": event.direction,
                    "entry_price": event.entry_price,
                    "exit_price": event.exit_price,
                    "volume": event.volume,
                    "profit_loss": event.profit_loss,
                    "exit_reason": event.exit_reason,
                    "opened_at": event.opened_at.isoformat() if event.opened_at else None,
                })
        
        elif event.event_type == EventType.METRICS_UPDATE:
            if isinstance(event, MetricsEvent):
                self._schedule_async(loop, "metrics", {
                    "balance": event.balance,
                    "equity": event.equity,
                    "win_rate": event.win_rate,
                    "profit_factor": event.profit_factor,
                    "total_trades": event.total_trades,
                    "winning_trades": event.winning_trades,
                    "losing_trades": event.losing_trades,
                    "max_drawdown": event.max_drawdown,
                })
    
    def _schedule_async(
        self,
        loop: asyncio.AbstractEventLoop,
        event_name: str,
        data: dict,
    ) -> None:
        """非同期タスクをスケジュール
        
        Args:
            loop: イベントループ
            event_name: イベント名
            data: イベントデータ
        """
        if event_name in self._callbacks:
            callback = self._callbacks[event_name]
            try:
                # スレッドセーフに非同期コルーチンをスケジュール
                asyncio.run_coroutine_threadsafe(callback(data), loop)
            except RuntimeError:
                # ループが閉じている場合は無視
                pass


def create_websocket_listener() -> WebSocketEventListener:
    """WebSocketリスナーを作成
    
    Returns:
        WebSocketEventListener: リスナーインスタンス
    """
    from autotrader.web.websocket.handlers import (
        broadcast_backtest_start,
        broadcast_backtest_end,
        broadcast_backtest_progress,
        broadcast_backtest_year_start,
        broadcast_backtest_year_end,
        broadcast_backtest_month_end,
        broadcast_backtest_trade_open,
        broadcast_backtest_trade_close,
        broadcast_backtest_signal,
        broadcast_backtest_metrics,
    )
    
    listener = WebSocketEventListener()
    listener.set_callback("backtest_start", broadcast_backtest_start)
    listener.set_callback("backtest_end", broadcast_backtest_end)
    listener.set_callback("progress", broadcast_backtest_progress)
    listener.set_callback("year_start", broadcast_backtest_year_start)
    listener.set_callback("year_end", broadcast_backtest_year_end)
    listener.set_callback("month_end", broadcast_backtest_month_end)
    listener.set_callback("trade_open", broadcast_backtest_trade_open)
    listener.set_callback("trade_close", broadcast_backtest_trade_close)
    listener.set_callback("signal", broadcast_backtest_signal)
    listener.set_callback("metrics", broadcast_backtest_metrics)
    
    return listener
