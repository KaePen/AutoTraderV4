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


class EntryFilterUpdate(BaseModel):
    """エントリーフィルター設定更新"""

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


class CapitalManagementUpdate(BaseModel):
    """資金管理設定更新"""

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


class PositionManagementUpdate(BaseModel):
    """ポジション管理設定更新（全PMフィールド）"""

    # 部分利確
    partial_close_1r_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    partial_close_2r_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    breakeven_at_1r: bool | None = None
    # トレーリング
    trailing_start_r: float | None = Field(
        default=None, ge=0.5, le=5.0
    )
    trailing_atr_multiplier: float | None = Field(
        default=None, ge=0.5, le=5.0
    )
    # 時間決済
    time_exit_enabled: bool | None = None
    # コスト
    spread_pips: float | None = Field(
        default=None, ge=0.0, le=10.0
    )
    slippage_pips: float | None = Field(
        default=None, ge=0.0, le=5.0
    )
    # BE基本設定
    early_breakeven_r: float | None = Field(
        default=None, ge=0.0, le=2.0
    )
    early_breakeven_enabled: bool | None = None
    disable_tp_after_partial: bool | None = None
    signal_rev_close_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    # Stagnation
    stagnation_exit_minutes: float | None = Field(
        default=None, ge=10.0, le=600.0
    )
    stagnation_min_mfe_r: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    # BE制御（RANGE×DAY）
    range_day_be_disabled: bool | None = None
    range_day_early_be_r: float | None = Field(
        default=None, ge=0.0, le=2.0
    )
    range_day_fast_be_enabled: bool | None = None
    range_day_fast_be_minutes: float | None = Field(
        default=None, ge=10.0, le=300.0
    )
    # RANGE×DAY stagnation段階化
    range_day_stagnation_enabled: bool | None = None
    range_day_stagnation_stage1_minutes: float | None = Field(
        default=None, ge=10.0, le=300.0
    )
    range_day_stagnation_stage1_min_mfe_r: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    range_day_stagnation_stage2_minutes: float | None = Field(
        default=None, ge=10.0, le=300.0
    )
    range_day_stagnation_stage2_min_mfe_r: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    # 0.5R早期部分利確
    early_partial_close_enabled: bool | None = None
    early_partial_close_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    # RANGE×DAY 保険
    range_day_insurance_enabled: bool | None = None
    range_day_insurance_max_minutes: float | None = Field(
        default=None, ge=5.0, le=120.0
    )
    range_day_insurance_sl_offset_r: float | None = Field(
        default=None, ge=-1.0, le=1.0
    )
    range_day_insurance_partial_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    # TP_EARLY厳格化
    insurance_trigger_r: float | None = Field(
        default=None, ge=0.0, le=3.0
    )
    insurance_block_high_mfe_r: float | None = Field(
        default=None, ge=0.0, le=3.0
    )
    insurance_min_holding_minutes: float | None = Field(
        default=None, ge=0.0, le=120.0
    )
    # 0.5R部分利確
    range_day_half_r_partial_enabled: bool | None = None
    range_day_half_r_partial_ratio: float | None = Field(
        default=None, ge=0.0, le=1.0
    )
    range_day_half_r_trigger: float | None = Field(
        default=None, ge=0.1, le=2.0
    )


class TradingConfigUpdate(BaseModel):
    """トレーディング設定更新リクエスト（ネスト構造）

    Attributes:
        entry_filter: エントリーフィルター設定
        capital_management: 資金管理設定
        position_management: ポジション管理設定
    """

    entry_filter: EntryFilterUpdate | None = None
    capital_management: CapitalManagementUpdate | None = (
        None
    )
    position_management: PositionManagementUpdate | None = (
        None
    )


class SettingsUpdateRequest(BaseModel):
    """設定更新リクエスト

    Attributes:
        trading: トレーディング設定
        notification: 通知設定
    """

    trading: TradingConfigUpdate | None = None
    notification: NotificationConfigUpdate | None = None


class SwitchAccountRequest(BaseModel):
    """口座切替リクエスト

    パスワードはMT5に保存されているものを使用する。
    APIでのパスワード送信は廃止（セキュリティ向上）。

    Attributes:
        login: MT5ログインID
        server: サーバー名
    """

    login: int
    server: str


class ReloadLogicRequest(BaseModel):
    """トレードロジックリロードリクエスト

    Attributes:
        symbol: 対象シンボル（None = 全エンジン）
        dry_run: True の場合は変更検知のみ実行しリロードしない
    """

    symbol: str | None = None
    dry_run: bool = False


class AccountPresetRequest(BaseModel):
    """口座プリセット登録リクエスト

    Attributes:
        login: MT5ログインID
        server: サーバー名
        name: 表示名
    """

    login: int
    server: str
    name: str = ""
