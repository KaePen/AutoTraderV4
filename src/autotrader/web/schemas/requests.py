"""リクエストスキーマ"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NotificationConfigUpdate(BaseModel):
    """通知設定更新リクエスト

    Attributes:
        enabled: 有効
        min_confidence: 最小確度
        sound_enabled: サウンド有効
    """

    enabled: bool | None = None
    min_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    sound_enabled: bool | None = None


class TradingConfigUpdate(BaseModel):
    """トレーディング設定更新リクエスト（UnifiedBotConfig対応）

    Attributes:
        range_day_bbw_threshold: RANGE×DAY bbw閾値
        range_day_score_premium: RANGE×DAY スコアプレミアム
        weak_hours_enabled: Weak Hoursフィルター有効
        weak_hours_score_premium: Weak Hoursスコアプレミアム
        tokyo_night_swing_enabled: 東京深夜SWINGフィルター有効
        tokyo_night_swing_premium: 東京深夜SWINGプレミアム
        use_dynamic_lot: 動的ロット有効
        base_risk_pct: 基本リスク率
        max_lot_per_trade: 最大ロット/トレード
        max_total_exposure_lot: 最大総エクスポージャー
        equity_floor_pct: エクイティフロア率
        slippage_buffer_pips: SLバッファ(pips)
        enable_position_manager: PM有効
        stagnation_min_mfe_r: 停滞最小MFE R
        range_day_early_be_r: RANGE×DAY早期BE R
        insurance_trigger_r: 保険トリガーR
    """

    # エントリーフィルター
    range_day_bbw_threshold: float | None = Field(
        default=None, ge=0.05, le=0.50
    )
    range_day_score_premium: float | None = Field(
        default=None, ge=0.0, le=2.0
    )
    weak_hours_enabled: bool | None = None
    weak_hours_score_premium: float | None = Field(
        default=None, ge=0.0, le=2.0
    )
    tokyo_night_swing_enabled: bool | None = None
    tokyo_night_swing_premium: float | None = Field(
        default=None, ge=0.0, le=2.0
    )

    # 資金管理
    use_dynamic_lot: bool | None = None
    base_risk_pct: float | None = Field(
        default=None, ge=0.005, le=0.10
    )
    max_lot_per_trade: float | None = Field(
        default=None, ge=0.01, le=10.0
    )
    max_total_exposure_lot: float | None = Field(
        default=None, ge=0.1, le=20.0
    )
    equity_floor_pct: float | None = Field(
        default=None, ge=0.10, le=0.90
    )
    slippage_buffer_pips: float | None = Field(
        default=None, ge=0.0, le=10.0
    )

    # ポジション管理
    enable_position_manager: bool | None = None
    stagnation_min_mfe_r: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    range_day_early_be_r: float | None = Field(
        default=None, ge=0.0, le=2.0
    )
    insurance_trigger_r: float | None = Field(
        default=None, ge=0.0, le=3.0
    )


class SettingsUpdateRequest(BaseModel):
    """設定更新リクエスト

    Attributes:
        trading: トレーディング設定
        notification: 通知設定
    """

    trading: TradingConfigUpdate | None = None
    notification: NotificationConfigUpdate | None = None
