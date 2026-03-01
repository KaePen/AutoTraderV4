"""ブロードキャストサービス

tick完了時のWebSocket配信ペイロード構築・送信を担当する。
"""

from __future__ import annotations

import logging

from autotrader.core.event_bus import event_bus

logger = logging.getLogger(__name__)


class BroadcastService:
    """tick完了時のWebSocket配信サービス

    エンジンから状態スナップショットを受け取り、
    ダッシュボード向けペイロードを構築・配信する。
    """

    async def broadcast_tick_update(
        self,
        payload: dict,
    ) -> None:
        """tick完了後に全UIデータをダッシュボードへ一括配信

        analysis / account / positions / radar を
        1ペイロードで送信。

        Args:
            payload: _build_tick_payloadで構築済みのペイロード
        """
        await event_bus.publish("tick.completed", payload)

    @staticmethod
    def build_tick_payload(
        *,
        config_symbol: str,
        last_analysis,
        last_tick_time,
        running: bool,
        connected: bool,
        enable_auto_trade: bool,
        demo_mode_enabled: bool,
        account_info,
        cached_positions: list[dict],
        signal_history: list,
        bot,
        get_current_entry_threshold,
        extract_indicators,
    ) -> dict:
        """tick_updateペイロードを構築

        Args:
            config_symbol: 設定シンボル
            last_analysis: 直近分析結果
            last_tick_time: 直近tick時刻
            running: エンジン実行中フラグ
            connected: MT5接続状態
            enable_auto_trade: 自動取引ON/OFF
            demo_mode_enabled: デモモードフラグ
            account_info: 口座情報
            cached_positions: キャッシュ済みポジション
            signal_history: シグナル履歴
            bot: TradeBotインスタンス
            get_current_entry_threshold: 閾値取得関数
            extract_indicators: 指標抽出関数

        Returns:
            dict: analysis / account / positions / radar /
                indicators
        """
        # --- analysis ---
        cs = last_analysis
        tick_time = last_tick_time
        if cs is not None:
            analysis = {
                "symbol": config_symbol,
                "direction": cs.direction.value,
                "confidence": cs.confidence,
                "consensus_score": cs.consensus_score,
                "entry_threshold": (
                    get_current_entry_threshold(cs.mode) or cs.entry_threshold
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
                "demo_mode": demo_mode_enabled,
                "engine_running": running,
                "auto_trade_enabled": enable_auto_trade,
                "mt5_connected": connected,
                "buy_score": cs.buy_score,
                "sell_score": cs.sell_score,
            }
        else:
            analysis = {
                "symbol": config_symbol,
                "engine_running": running,
                "mt5_connected": connected,
                "auto_trade_enabled": enable_auto_trade,
                "demo_mode": demo_mode_enabled,
            }

        # --- account (metrics用) ---
        acc = account_info
        account = {
            "balance": acc.balance if acc else 0.0,
            "equity": acc.equity if acc else 0.0,
            "margin": acc.margin if acc else 0.0,
            "free_margin": acc.free_margin if acc else 0.0,
            "profit": acc.profit if acc else 0.0,
        }

        # --- radar (シグナル履歴からHOLD除外・信頼度降順) ---
        grouped: dict[str, list] = {}
        for s in signal_history:
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

        # --- indicators (エンジン計算済みデータから取得) ---
        indicators: dict[str, dict] = {}
        if bot and hasattr(bot, "_market_data"):
            for tf in bot._market_data:
                indicators[tf] = extract_indicators(tf)

        return {
            "analysis": analysis,
            "account": account,
            "positions": cached_positions,
            "radar": radar_serialized,
            "indicators": indicators,
        }
