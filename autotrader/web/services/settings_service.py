"""設定サービス"""

from __future__ import annotations

import dataclasses
import logging

from autotrader.config.config_loader import ConfigLoader
from autotrader.config.settings import Settings
from autotrader.decision.unified import UnifiedBotConfig
from autotrader.decision.unified.position_manager import (
    PositionManagerConfig,
)
from autotrader.web.schemas import SettingsResponse
from autotrader.web.schemas.requests import SettingsUpdateRequest
from autotrader.web.schemas.responses import (
    CapitalManagementConfigResponse,
    EntryFilterConfigResponse,
    NotificationConfigResponse,
    PositionManagementConfigResponse,
    RiskConfigResponse,
    TradingConfigResponse,
)

logger = logging.getLogger(__name__)

# シングルトンインスタンス
_instance: SettingsService | None = None


def get_settings_service() -> SettingsService:
    """SettingsServiceシングルトンを取得

    Returns:
        SettingsService: 設定サービスインスタンス
    """
    global _instance
    if _instance is None:
        from autotrader.config.settings import get_settings

        _instance = SettingsService(get_settings())
    return _instance


def reset_settings_service() -> None:
    """シングルトンをリセット（テスト用）"""
    global _instance
    _instance = None


class SettingsService:
    """設定サービス

    YAML設定ファイルとLiveEngineを統合管理する。

    Attributes:
        _settings: アプリケーション設定
        _config_loader: YAML設定ローダー
        _engine: LiveTradingEngineへの参照
        _engine_manager: EngineManagerへの参照
        _bot_config: 現在のBot設定
        _pm_config: 現在のPM設定
    """

    # 通知設定（メモリ内管理）
    _notification_config: dict = {
        "enabled": True,
        "min_confidence": 0.5,
        "sound_enabled": True,
    }

    def __init__(
        self,
        settings: Settings,
        config_loader: ConfigLoader | None = None,
    ) -> None:
        """初期化

        Args:
            settings: アプリケーション設定
            config_loader: YAML設定ローダー
        """
        self._settings = settings
        self._config_loader = config_loader or ConfigLoader()
        self._engine = None
        self._engine_manager = None

        # YAMLから初期設定読み込み
        self._bot_config, self._pm_config = (
            self._config_loader.load_live_config()
        )

    def set_engine(self, engine: object) -> None:
        """LiveTradingEngineの参照を設定

        lifespan内でエンジン初期化後に呼び出す。

        Args:
            engine: LiveTradingEngineインスタンス
        """
        self._engine = engine

    def set_engine_manager(self, mgr: object) -> None:
        """EngineManagerの参照を設定

        lifespan内でマネージャー初期化後に呼び出す。

        Args:
            mgr: EngineManagerインスタンス
        """
        self._engine_manager = mgr

    @property
    def bot_config(self) -> UnifiedBotConfig:
        """現在のBot設定（デフォルトシンボル用）"""
        return self._bot_config

    @property
    def pm_config(self) -> PositionManagerConfig:
        """現在のPM設定（デフォルトシンボル用）"""
        return self._pm_config

    def get_config_for_symbol(
        self,
        symbol: str,
    ) -> tuple[UnifiedBotConfig, PositionManagerConfig]:
        """ペア別設定を構築（ライブ用: multi_mode=True）

        Args:
            symbol: 通貨ペアシンボル

        Returns:
            tuple: (UnifiedBotConfig, PositionManagerConfig)
        """
        return self._config_loader.load_preset_config(
            symbol,
            multi_mode=True,
        )

    def get_current_settings(self) -> SettingsResponse:
        """現在の設定を取得

        Returns:
            SettingsResponse: 設定情報
        """
        bot = self._bot_config
        pm = self._pm_config

        trading = TradingConfigResponse(
            entry_filter=EntryFilterConfigResponse(
                range_day_bbw_threshold=(bot.range_day_bbw_threshold),
                range_day_score_premium=(bot.range_day_score_premium),
                weak_hours_enabled=bot.weak_hours_enabled,
                weak_hours_score_premium=(bot.weak_hours_score_premium),
            ),
            capital_management=CapitalManagementConfigResponse(
                use_dynamic_lot=bot.use_dynamic_lot,
                base_risk_pct=bot.base_risk_pct,
                max_lot_per_trade=bot.max_lot_per_trade,
                max_total_exposure_lot=(bot.max_total_exposure_lot),
                equity_floor_pct=bot.equity_floor_pct,
                slippage_buffer_pips=bot.slippage_buffer_pips,
            ),
            position_management=PositionManagementConfigResponse(
                enable_position_manager=(bot.enable_position_manager),
                partial_close_1r_ratio=(pm.partial_close_1r_ratio),
                partial_close_2r_ratio=(pm.partial_close_2r_ratio),
                breakeven_at_1r=pm.breakeven_at_1r,
                trailing_start_r=pm.trailing_start_r,
                trailing_atr_multiplier=(pm.trailing_atr_multiplier),
                time_exit_enabled=pm.time_exit_enabled,
                spread_pips=pm.spread_pips,
                slippage_pips=pm.slippage_pips,
                early_breakeven_r=pm.early_breakeven_r,
                early_breakeven_enabled=(pm.early_breakeven_enabled),
                disable_tp_after_partial=(pm.disable_tp_after_partial),
                signal_rev_close_ratio=(pm.signal_rev_close_ratio),
                stagnation_exit_minutes=(pm.stagnation_exit_minutes),
                stagnation_min_mfe_r=(pm.stagnation_min_mfe_r),
                range_day_be_disabled=(pm.range_day_be_disabled),
                range_day_early_be_r=(pm.range_day_early_be_r),
                range_day_fast_be_enabled=(pm.range_day_fast_be_enabled),
                range_day_fast_be_minutes=(pm.range_day_fast_be_minutes),
                range_day_stagnation_enabled=(pm.range_day_stagnation_enabled),
                range_day_stagnation_stage1_minutes=(
                    pm.range_day_stagnation_stage1_minutes
                ),
                range_day_stagnation_stage1_min_mfe_r=(
                    pm.range_day_stagnation_stage1_min_mfe_r
                ),
                range_day_stagnation_stage2_minutes=(
                    pm.range_day_stagnation_stage2_minutes
                ),
                range_day_stagnation_stage2_min_mfe_r=(
                    pm.range_day_stagnation_stage2_min_mfe_r
                ),
                early_partial_close_enabled=(pm.early_partial_close_enabled),
                early_partial_close_ratio=(pm.early_partial_close_ratio),
                range_day_insurance_enabled=(pm.range_day_insurance_enabled),
                range_day_insurance_max_minutes=(
                    pm.range_day_insurance_max_minutes
                ),
                range_day_insurance_sl_offset_r=(
                    pm.range_day_insurance_sl_offset_r
                ),
                range_day_insurance_partial_ratio=(
                    pm.range_day_insurance_partial_ratio
                ),
                insurance_trigger_r=pm.insurance_trigger_r,
                insurance_block_high_mfe_r=(pm.insurance_block_high_mfe_r),
                insurance_min_holding_minutes=(
                    pm.insurance_min_holding_minutes
                ),
                range_day_half_r_partial_enabled=(
                    pm.range_day_half_r_partial_enabled
                ),
                range_day_half_r_partial_ratio=(
                    pm.range_day_half_r_partial_ratio
                ),
                range_day_half_r_trigger=(pm.range_day_half_r_trigger),
            ),
        )

        return SettingsResponse(
            trading=trading,
            risk=RiskConfigResponse(
                max_daily_loss_pct=(self._settings.max_daily_loss_pct),
                max_position_count=(self._settings.max_position_count),
                min_margin_ratio=(self._settings.min_margin_ratio),
            ),
            notification=NotificationConfigResponse(
                enabled=self._notification_config["enabled"],
                min_confidence=self._notification_config["min_confidence"],
                sound_enabled=self._notification_config["sound_enabled"],
            ),
        )

    def update_settings(
        self,
        request: SettingsUpdateRequest,
    ) -> SettingsResponse:
        """設定を更新

        トレーディング設定 → エンジン反映 + YAML永続化。
        通知設定 → メモリ内更新。

        Args:
            request: 更新リクエスト

        Returns:
            SettingsResponse: 更新後の設定
        """
        # 通知設定のランタイム更新
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

        # トレーディング設定の更新
        if request.trading:
            self._apply_trading_update(request.trading)

        return self.get_current_settings()

    @property
    def demo_mode_enabled(self) -> bool:
        """デモモード有効状態"""
        return self._bot_config.demo_mode

    def _apply_to_all_engines(
        self,
        bot_config: UnifiedBotConfig | None = None,
        pm_config: PositionManagerConfig | None = None,
    ) -> None:
        """全エンジンにペア別設定を反映

        各エンジンのシンボルに応じたプリセットを
        リロードして適用する。引数が指定された場合は
        後方互換としてその値を全エンジンに適用する。

        Args:
            bot_config: Bot設定（None時はペア別リロード）
            pm_config: PM設定（None時はペア別リロード）
        """
        if self._engine_manager is not None:
            for sym, engine in self._engine_manager.engines.items():
                if bot_config is not None:
                    engine.update_bot_config(bot_config)
                else:
                    sym_bot, sym_pm = self.get_config_for_symbol(sym)
                    engine.update_bot_config(sym_bot)
                    engine.update_pm_config(sym_pm)
                if pm_config is not None:
                    engine.update_pm_config(pm_config)
        elif self._engine is not None:
            if bot_config is not None:
                self._engine.update_bot_config(bot_config)
            if pm_config is not None:
                self._engine.update_pm_config(pm_config)

    def enable_demo_mode(self) -> UnifiedBotConfig:
        """デモモードを有効化

        demo_trading.yamlの設定でBotConfigを差し替え。
        全エンジンにも即時反映する。

        Returns:
            UnifiedBotConfig: 更新後のBot設定
        """
        demo_bot, demo_pm = self._config_loader.load_demo_config()
        self._bot_config = demo_bot
        self._pm_config = demo_pm

        self._apply_to_all_engines(demo_bot, demo_pm)

        logger.info("デモモード有効化")
        return demo_bot

    def disable_demo_mode(self) -> UnifiedBotConfig:
        """デモモードを無効化

        live_trading.yamlの設定でBotConfigを差し替え。
        全エンジンにも即時反映する。

        Returns:
            UnifiedBotConfig: 更新後のBot設定
        """
        live_bot, live_pm = self._config_loader.load_live_config()
        self._bot_config = live_bot
        self._pm_config = live_pm

        self._apply_to_all_engines(live_bot, live_pm)

        logger.info("デモモード無効化（本番設定に戻す）")
        return live_bot

    def _apply_trading_update(
        self,
        trading_update: object,
    ) -> None:
        """トレーディング設定を適用

        frozen dataclassはasdict→update→再構築。

        Args:
            trading_update: TradingConfigUpdate
        """
        from autotrader.web.schemas.requests import (
            TradingConfigUpdate,
        )

        if not isinstance(trading_update, TradingConfigUpdate):
            return

        # PM設定の更新
        if trading_update.position_management:
            pm_dict = dataclasses.asdict(self._pm_config)
            update_data = trading_update.position_management.model_dump(
                exclude_none=True,
            )
            pm_dict.update(update_data)
            new_pm = PositionManagerConfig(**pm_dict)
            self._pm_config = new_pm

            # 全エンジンに反映
            if self._engine_manager is not None:
                for engine in self._engine_manager.engines.values():
                    engine.update_pm_config(new_pm)
            elif self._engine is not None:
                self._engine.update_pm_config(new_pm)

            # YAML永続化
            self._config_loader.save_pm_config(new_pm)
            logger.info("PM設定更新+永続化完了")

        # Bot設定の更新（entry_filter, capital_management）
        bot_updates: dict = {}
        if trading_update.entry_filter:
            bot_updates.update(
                trading_update.entry_filter.model_dump(
                    exclude_none=True,
                )
            )
        if trading_update.capital_management:
            bot_updates.update(
                trading_update.capital_management.model_dump(
                    exclude_none=True,
                )
            )

        if bot_updates:
            bot_dict = dataclasses.asdict(self._bot_config)
            bot_dict.update(bot_updates)
            new_bot = UnifiedBotConfig(**bot_dict)
            self._bot_config = new_bot

            # 全エンジンに反映
            if self._engine_manager is not None:
                for engine in self._engine_manager.engines.values():
                    engine.update_bot_config(new_bot)
            elif self._engine is not None:
                self._engine.update_bot_config(new_bot)

            # YAML永続化
            self._config_loader.save_bot_config(new_bot)
            logger.info("Bot設定更新+永続化完了")
