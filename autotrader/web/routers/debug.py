"""診断エンドポイント

WebSocket・EventBus・エンジンの状態を確認するためのAPI。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from autotrader.core.event_bus import get_event_bus
from autotrader.web.websocket.event_bridge import (
    bridge_event_counts,
)
from autotrader.web.websocket.manager import manager

router = APIRouter()


@router.get("/debug/ws-status")
async def ws_status(request: Request) -> dict[str, Any]:
    """WebSocket・EventBus・エンジンの診断情報

    Returns:
        dict: 接続数、イベントカウント、エンジン状態
    """
    # ConnectionManager 統計
    ws_stats = manager.get_stats()

    # EventBus 購読状況
    bus = get_event_bus()
    subscriptions: dict[str, int] = {}
    for topic, handlers in bus._handlers.items():
        subscriptions[topic] = len(handlers)

    # エンジン状態
    engines_info: list[dict[str, Any]] = []
    mgr = getattr(request.app.state, "engine_manager", None)
    if mgr:
        for sym, eng in mgr._engines.items():
            engines_info.append(
                {
                    "symbol": sym,
                    "running": eng.running,
                    "last_tick_ms": eng._last_mt5_tick_ms,
                }
            )

    return {
        "websocket": ws_stats,
        "eventbus_subscriptions": subscriptions,
        "bridge_event_counts": dict(bridge_event_counts),
        "engines": engines_info,
    }


@router.get("/debug/m1-diag")
async def m1_diagnostic(
    request: Request,
    symbol: str = "USDJPY",
) -> dict[str, Any]:
    """M1タイムフレームの診断情報

    Returns:
        dict: M1データの状態、行データ、インジケータ値
    """
    import numpy as np
    import pandas as pd

    mgr = request.app.state.engine_manager
    engine = mgr.get_engine(symbol) if mgr else None
    if engine is None:
        return {"error": f"エンジンなし: {symbol}"}

    bot = engine._bot
    result: dict[str, Any] = {
        "symbol": symbol,
        "timeframes": bot.timeframes,
        "market_data_keys": list(bot._market_data.keys()),
        "m1_in_data": "M1" in bot._market_data,
        "m1_in_time_arrays": "M1" in bot._time_arrays,
    }

    df = bot._market_data.get("M1")
    if df is not None:
        result["m1_rows"] = len(df)
        result["m1_columns"] = list(df.columns)
        last = df.iloc[-1]
        result["m1_last_row"] = {
            col: (
                None if (isinstance(v, float) and (pd.isna(v) or np.isinf(v)))
                else float(v) if isinstance(v, (float, np.floating))
                else str(v)
            )
            for col, v in last.items()
        }
        # sma_50のNaN数を確認
        if "sma_50" in df.columns:
            nan_count = df["sma_50"].isna().sum()
            result["sma_50_nan_count"] = int(nan_count)
            result["sma_50_total"] = len(df)
    else:
        result["m1_rows"] = 0

    # _get_current_row テスト
    try:
        ct = pd.Timestamp.now(tz="UTC")
        row = bot._get_current_row("M1", ct)
        if row is not None:
            result["current_row_close"] = float(row.get("close", 0))
            result["current_row_sma_20"] = (
                None if pd.isna(row.get("sma_20")) else float(row.get("sma_20", 0))
            )
            result["current_row_sma_50"] = (
                None if pd.isna(row.get("sma_50")) else float(row.get("sma_50", 0))
            )
            result["current_row_index"] = int(
                bot._current_indices.get("M1", -1)
            )
        else:
            result["current_row"] = None
    except Exception as e:
        result["current_row_error"] = str(e)

    return result


@router.get("/debug/simulate-dd")
async def simulate_dd(
    request: Request,
    pct: float = 0.0,
) -> dict[str, Any]:
    """DD状態をシミュレートしてWebUI表示を確認

    Args:
        pct: シミュレートするDD% (0でリセット)

    Returns:
        dict: 適用後のDD状態
    """
    mgr = getattr(request.app.state, "engine_manager", None)
    if mgr is None:
        return {"error": "EngineManager未初期化"}

    # DD関連のデバッグアラートをクリア
    mgr._debug_alerts = [
        a for a in mgr._debug_alerts
        if not a.get("type", "").startswith("portfolio_dd")
    ]

    if pct <= 0:
        # リセット
        mgr._current_dd_pct = 0.0
        mgr._dd_warning_active = False
        mgr._dd_emergency_active = False
        mgr._dd_emergency_at = None
        mgr._emergency_close_done = False
        return {"action": "reset", "dd_status": mgr.dd_status}

    # DD数値のみ上書き（状態フラグは変えない→本番ロジック非発火）
    mgr._current_dd_pct = pct
    from autotrader.live.engine_manager import (
        DD_EMERGENCY_PCT,
        DD_WARNING_PCT,
    )
    # デバッグアラートのみ注入（表示確認用）
    if pct >= DD_EMERGENCY_PCT:
        mgr._debug_alerts.append({
            "type": "portfolio_dd_emergency",
            "message": (
                f"DD緊急停止 {pct:.2f}% "
                f"(>= {DD_EMERGENCY_PCT}%) "
                f"— 全決済済・エントリー停止中"
            ),
            "severity": "danger",
        })
    elif pct >= DD_WARNING_PCT:
        mgr._debug_alerts.append({
            "type": "portfolio_dd_warning",
            "message": f"DD警告 {pct:.2f}% (>= {DD_WARNING_PCT}%)",
            "severity": "warning",
        })

    # ピーク未設定なら仮の値をセット
    if mgr._peak_equity <= 0:
        mgr._peak_equity = 1_000_000

    return {
        "action": f"simulate DD {pct}%",
        "dd_status": mgr.dd_status,
    }


# 全アラート種別のプリセット
_ALERT_PRESETS: dict[str, dict[str, str]] = {
    "weekend_cutoff": {
        "type": "weekend_cutoff",
        "message": "週末カットオフ発動中",
        "severity": "danger",
    },
    "weekend_cutoff_warn": {
        "type": "weekend_cutoff",
        "message": "週末カットオフまで 60 分",
        "severity": "warning",
    },
    "hold_constraint": {
        "type": "hold_constraint",
        "message": "SoftGuardペナルティ超過でHOLD",
        "severity": "info",
    },
    "position_limit_symbol": {
        "type": "position_limit_symbol",
        "message": "ポジション上限 1/1",
        "severity": "warning",
    },
    "position_limit_global": {
        "type": "position_limit_global",
        "message": "グローバルポジション上限 4/4",
        "severity": "warning",
    },
    "exposure_limit": {
        "type": "exposure_limit",
        "message": "ロット上限 5.00/5.0",
        "severity": "warning",
    },
    "dd_warning": {
        "type": "portfolio_dd_warning",
        "message": "DD警告 3.12% (>= 3%)",
        "severity": "warning",
    },
    "dd_emergency": {
        "type": "portfolio_dd_emergency",
        "message": "DD緊急停止 5.23% (>= 5%) — 全決済済・エントリー停止中",
        "severity": "danger",
    },
    "high_impact": {
        "type": "high_impact_event",
        "message": "重要指標まで 15 分",
        "severity": "danger",
    },
}


@router.get("/debug/simulate-alert")
async def simulate_alert(
    request: Request,
    preset: str = "",
    type: str = "",
    message: str = "",
    severity: str = "warning",
) -> dict[str, Any]:
    """アラートpillをシミュレート表示

    プリセット名またはカスタムパラメータで指定可能。

    プリセット一覧: /api/debug/alert-presets

    Args:
        preset: プリセット名（指定時はtype/message/severityを無視）
        type: アラートtype（カスタム用）
        message: 表示メッセージ（カスタム用）
        severity: danger/warning/info

    Examples:
        /api/debug/simulate-alert?preset=dd_warning
        /api/debug/simulate-alert?preset=weekend_cutoff
        /api/debug/simulate-alert?type=custom&message=テスト&severity=danger
    """
    mgr = getattr(request.app.state, "engine_manager", None)
    if mgr is None:
        return {"error": "EngineManager未初期化"}

    if preset:
        if preset == "clear":
            mgr._debug_alerts.clear()
            return {
                "action": "cleared",
                "active_debug_alerts": [],
            }
        alert = _ALERT_PRESETS.get(preset)
        if alert is None:
            return {
                "error": f"不明なプリセット: {preset}",
                "available": list(_ALERT_PRESETS.keys()),
            }
        mgr._debug_alerts.append(dict(alert))
    elif message:
        mgr._debug_alerts.append({
            "type": type or "debug",
            "message": message,
            "severity": severity,
        })
    else:
        return {
            "error": "preset または message を指定してください",
            "available_presets": list(_ALERT_PRESETS.keys()),
        }

    return {
        "action": "added",
        "active_debug_alerts": mgr._debug_alerts,
    }


@router.get("/debug/alert-presets")
async def alert_presets() -> dict[str, Any]:
    """利用可能なアラートプリセット一覧"""
    return {
        "presets": _ALERT_PRESETS,
        "usage": (
            "/api/debug/simulate-alert?preset=<name> で追加、"
            "?preset=clear で全消去"
        ),
    }
