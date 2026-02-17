"""設定サービス"""

from __future__ import annotations

from autotrader.config.settings import Settings
from autotrader.decision.unified import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)
from autotrader.web.schemas import SettingsResponse
from autotrader.web.schemas.requests import SettingsUpdateRequest
from autotrader.web.schemas.responses import (
    EntryFilterConfigResponse,
    CapitalManagementConfigResponse,
    PositionManagementConfigResponse,
    TradingConfigResponse,
    RiskConfigResponse,
    NotificationConfigResponse,
)


class SettingsService:
    """設定サービス

    Attributes:
        settings: アプリケーション設定
    """

    # 通知設定（メモリ内管理）
    _notification_config: dict = {
        "enabled": True,
        "min_confidence": 0.5,
        "sound_enabled": True,
    }

    def __init__(self, settings: Settings) -> None:
        """初期化

        Args:
            settings: アプリケーション設定
        """
        self._settings = settings
        self._bot_config = UnifiedBotConfig()
        self._pm_config = PositionManagerConfig()

    def get_current_settings(self) -> SettingsResponse:
        """現在の設定を取得

        Returns:
            SettingsResponse: 設定情報
        """
        bot = self._bot_config
        pm = self._pm_config

        trading = TradingConfigResponse(
            entry_filter=EntryFilterConfigResponse(
                range_day_bbw_threshold=bot.range_day_bbw_threshold,
                range_day_score_premium=bot.range_day_score_premium,
                weak_hours_enabled=bot.weak_hours_enabled,
                weak_hours_score_premium=(
                    bot.weak_hours_score_premium
                ),
                tokyo_night_swing_enabled=(
                    bot.tokyo_night_swing_enabled
                ),
                tokyo_night_swing_premium=(
                    bot.tokyo_night_swing_premium
                ),
            ),
            capital_management=CapitalManagementConfigResponse(
                use_dynamic_lot=bot.use_dynamic_lot,
                base_risk_pct=bot.base_risk_pct,
                max_lot_per_trade=bot.max_lot_per_trade,
                max_total_exposure_lot=bot.max_total_exposure_lot,
                equity_floor_pct=bot.equity_floor_pct,
                slippage_buffer_pips=bot.slippage_buffer_pips,
            ),
            position_management=PositionManagementConfigResponse(
                enable_position_manager=bot.enable_position_manager,
                stagnation_min_mfe_r=pm.stagnation_min_mfe_r,
                range_day_early_be_r=pm.range_day_early_be_r,
                insurance_trigger_r=pm.insurance_trigger_r,
                partial_close_1r_ratio=pm.partial_close_1r_ratio,
                trailing_start_r=pm.trailing_start_r,
            ),
        )

        return SettingsResponse(
            trading=trading,
            risk=RiskConfigResponse(
                max_daily_loss_pct=self._settings.max_daily_loss_pct,
                max_position_count=(
                    self._settings.max_position_count
                ),
                min_margin_ratio=self._settings.min_margin_ratio,
            ),
            notification=NotificationConfigResponse(
                enabled=self._notification_config["enabled"],
                min_confidence=self._notification_config[
                    "min_confidence"
                ],
                sound_enabled=self._notification_config[
                    "sound_enabled"
                ],
            ),
        )

    def update_settings(
        self, request: SettingsUpdateRequest
    ) -> SettingsResponse:
        """設定を更新

        Args:
            request: 更新リクエスト

        Returns:
            SettingsResponse: 更新後の設定
        """
        # 通知設定のみランタイム更新可能
        if request.notification:
            if request.notification.enabled is not None:
                self._notification_config["enabled"] = (
                    request.notification.enabled
                )
            if request.notification.min_confidence is not None:
                self._notification_config["min_confidence"] = (
                    request.notification.min_confidence
                )
            if request.notification.sound_enabled is not None:
                self._notification_config["sound_enabled"] = (
                    request.notification.sound_enabled
                )

        return self.get_current_settings()
