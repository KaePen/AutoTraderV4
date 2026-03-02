"""イベント発行サービス

WebSocket/EventBus経由のUI更新データ配信を担当する。
"""

from __future__ import annotations

import logging

from autotrader.core.event_bus import event_bus

logger = logging.getLogger(__name__)


class EventPublisher:
    """tick完了後のUIデータ一括配信を担うサービス

    Attributes:
        _engine: LiveTradingEngineへの参照（データ取得用）
    """

    def __init__(self, engine) -> None:
        """初期化

        Args:
            engine: LiveTradingEngineへの参照
        """
        self._engine = engine

    async def broadcast_tick_update(self) -> None:
        """tick完了後に全UIデータをダッシュボードへ一括配信

        analysis / account / positions / radar を
        1ペイロードで送信。フロントエンドはこのイベントを
        受信してUIを全更新する。
        """
        payload = self.build_tick_payload()
        await event_bus.publish("tick.completed", payload)

    def build_tick_payload(self) -> dict:
        """tick_updateペイロードを構築

        Returns:
            dict: analysis / account / positions / radar
        """
        engine = self._engine

        # --- analysis ---
        cs = engine._last_analysis
        tick_time = engine._last_tick_time
        if cs is not None:
            analysis = {
                "symbol": engine._config.symbol,
                "direction": cs.direction.value,
                "confidence": cs.confidence,
                "consensus_score": cs.consensus_score,
                "entry_threshold": (
                    engine.get_current_entry_threshold(cs.mode)
                    or cs.entry_threshold
                ),
                "regime": cs.regime,
                "mode": cs.mode,
                "rationale": cs.rationale,
                "htf_alignment": cs.htf_alignment,
                "penalty_total": cs.penalty_total,
                "penalty_breakdown": dict(cs.penalty_breakdown),
                "trend_strength": cs.trend_strength,
                "aligned_tfs": list(cs.aligned_tfs),
                "tf_scores": dict(cs.scores),
                "tf_breakdowns": {
                    k: dict(v) for k, v in cs.tf_score_breakdowns.items()
                },
                "tf_directions": dict(cs.tf_directions),
                "last_tick_time": (
                    tick_time.isoformat() if tick_time else None
                ),
                "demo_mode": engine.demo_mode_enabled,
                "engine_running": engine._running,
                "auto_trade_enabled": (engine._enable_auto_trade),
                "mt5_connected": engine.connected,
                "buy_score": cs.buy_score,
                "sell_score": cs.sell_score,
            }
        else:
            analysis = {
                "symbol": engine._config.symbol,
                "engine_running": engine._running,
                "mt5_connected": engine.connected,
                "auto_trade_enabled": (engine._enable_auto_trade),
                "demo_mode": engine.demo_mode_enabled,
            }

        # --- account (metrics用) ---
        acc = engine._account_info
        account = {
            "balance": acc.balance if acc else 0.0,
            "equity": acc.equity if acc else 0.0,
            "margin": acc.margin if acc else 0.0,
            "free_margin": acc.free_margin if acc else 0.0,
            "profit": acc.profit if acc else 0.0,
        }

        # --- radar ---
        grouped: dict[str, list] = {}
        for s in engine._signal_history:
            if s.signal_type.value != "HOLD":
                grouped.setdefault(s.symbol, []).append(s)
        radar = {
            sym: sorted(
                sigs,
                key=lambda x: x.confidence,
                reverse=True,
            )
            for sym, sigs in grouped.items()
        }
        radar_serialized = {
            sym: [
                {
                    "signal_id": s.signal_id,
                    "signal_type": s.signal_type.value,
                    "timeframe": s.timeframe.value
                    if hasattr(s.timeframe, "value")
                    else str(s.timeframe),
                    "confidence": s.confidence,
                    "confidence_level": (
                        s.confidence_level.value
                        if hasattr(s.confidence_level, "value")
                        else str(s.confidence_level)
                    ),
                    "reasoning": s.reasoning,
                }
                for s in sigs
            ]
            for sym, sigs in radar.items()
        }

        # --- indicators ---
        indicators: dict[str, dict] = {}
        if engine._bot and hasattr(engine._bot, "_market_data"):
            for tf in engine._bot._market_data:
                indicators[tf] = engine._data_feed.extract_indicators(tf)

        return {
            "analysis": analysis,
            "account": account,
            "positions": engine._cached_positions,
            "radar": radar_serialized,
            "indicators": indicators,
        }
