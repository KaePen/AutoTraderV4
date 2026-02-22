"""スキーマモジュール"""

from __future__ import annotations

from autotrader.web.schemas.responses import (
    ApiResponse,
    HealthResponse,
    DashboardResponse,
    SignalResponse,
    PositionResponse,
    TradeResponse,
    TradeSummaryResponse,
    IndicatorResponse,
    IndicatorSeriesResponse,
    CandleResponse,
    SettingsResponse,
    TradingConfigResponse,
    EntryFilterConfigResponse,
    CapitalManagementConfigResponse,
    PositionManagementConfigResponse,
    MT5StatusResponse,
    TradingModeResponse,
    AnalysisResponse,
    AccountPresetResponse,
    AccountPresetsResponse,
)
from autotrader.web.schemas.requests import (
    SettingsUpdateRequest,
    SwitchAccountRequest,
    AccountPresetRequest,
    TradingConfigUpdate,
)

__all__ = [
    "ApiResponse",
    "HealthResponse",
    "DashboardResponse",
    "SignalResponse",
    "PositionResponse",
    "TradeResponse",
    "TradeSummaryResponse",
    "IndicatorResponse",
    "IndicatorSeriesResponse",
    "CandleResponse",
    "SettingsResponse",
    "SettingsUpdateRequest",
    "SwitchAccountRequest",
    "TradingConfigResponse",
    "TradingConfigUpdate",
    "EntryFilterConfigResponse",
    "CapitalManagementConfigResponse",
    "PositionManagementConfigResponse",
    "MT5StatusResponse",
    "TradingModeResponse",
    "AnalysisResponse",
    "AccountPresetResponse",
    "AccountPresetsResponse",
    "AccountPresetRequest",
]
