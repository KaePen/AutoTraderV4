"""ポジション管理モジュール

ExitManager + PartialCloseManagerの統合。保有中の戦術を一元管理する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import logging

from autotrader.core.entities import (
    PositionState,
    PositionStateMachine,
)
from autotrader.core.enums import ExitReason, SignalType
from autotrader.decision.unified.mode_selector import (
    UNIVERSAL_MODE,
    TradingPlan,
)

logger = logging.getLogger(__name__)


class ManagementActionType(str, Enum):
    """管理アクション種別"""

    HOLD = "hold"                    # 保持
    UPDATE_SL = "update_sl"          # SL更新
    PARTIAL_CLOSE = "partial_close"  # 部分決済
    FULL_CLOSE = "full_close"        # 全決済


@dataclass(frozen=True)
class ManagementAction:
    """管理アクション

    Attributes:
        action_type: アクション種別
        close_ratio: 決済比率（0=SL更新のみ、1.0=全決済）
        new_sl: 新SL価格（None=変更なし）
        reason: 理由
        exit_reason: 決済理由（決済時のみ）
    """

    action_type: ManagementActionType
    close_ratio: float
    new_sl: float | None
    reason: str
    exit_reason: ExitReason | None = None
    trigger_price: float = 0.0

    @classmethod
    def hold(cls, reason: str = "継続保有") -> "ManagementAction":
        """保持アクションを作成"""
        return cls(
            action_type=ManagementActionType.HOLD,
            close_ratio=0.0,
            new_sl=None,
            reason=reason,
        )

    @classmethod
    def update_sl(cls, new_sl: float, reason: str) -> "ManagementAction":
        """SL更新アクションを作成"""
        return cls(
            action_type=ManagementActionType.UPDATE_SL,
            close_ratio=0.0,
            new_sl=new_sl,
            reason=reason,
        )

    @classmethod
    def partial_close(
        cls,
        ratio: float,
        new_sl: float | None,
        reason: str,
        exit_reason: ExitReason = ExitReason.TAKE_PROFIT,
        trigger_price: float = 0.0,
    ) -> "ManagementAction":
        """部分決済アクションを作成"""
        return cls(
            action_type=ManagementActionType.PARTIAL_CLOSE,
            close_ratio=ratio,
            new_sl=new_sl,
            reason=reason,
            exit_reason=exit_reason,
            trigger_price=trigger_price,
        )

    @classmethod
    def full_close(
        cls,
        reason: str,
        exit_reason: ExitReason,
        trigger_price: float = 0.0,
    ) -> "ManagementAction":
        """全決済アクションを作成"""
        return cls(
            action_type=ManagementActionType.FULL_CLOSE,
            close_ratio=1.0,
            new_sl=None,
            reason=reason,
            exit_reason=exit_reason,
            trigger_price=trigger_price,
        )


@dataclass
class ManagedPosition:
    """管理対象ポジション

    Attributes:
        position_id: ポジションID
        direction: ポジション方向
        entry_price: エントリー価格
        entry_time: エントリー時刻
        original_sl: 元のSL価格
        original_tp: 元のTP価格
        current_sl: 現在のSL価格
        initial_volume: 初期ロット数
        remaining_volume: 残ロット数
        plan: トレーディングプラン
        highest_price: 到達最高価格（BUY用）
        lowest_price: 到達最安価格（SELL用）
        bars_held: 保有バー数
        current_r: 現在のR値
        highest_r: 到達最高R値
    """

    position_id: str
    direction: SignalType
    entry_price: float
    entry_time: datetime
    original_sl: float
    original_tp: float
    current_sl: float
    initial_volume: float
    remaining_volume: float
    plan: TradingPlan
    highest_price: float = field(default=0.0)
    lowest_price: float = field(default=0.0)
    bars_held: int = 0
    current_r: float = 0.0
    highest_r: float = 0.0
    trailing_activated: bool = False
    # エッジ劣化監視: エントリー時の自方向スコアを保存
    entry_own_score: float = 0.0
    # MFE最終更新時刻（利益停滞検出用）
    mfe_last_update_time: datetime | None = None
    state_machine: PositionStateMachine = field(
        default_factory=lambda: PositionStateMachine(
            PositionState.OPEN,
        ),
    )

    @property
    def r_value(self) -> float:
        """1R値（SL距離）を取得"""
        return abs(self.entry_price - self.original_sl)

    def update_price(
        self,
        current_price: float,
        current_time: datetime | None = None,
    ) -> None:
        """価格更新時に最高/最安価格を更新

        Args:
            current_price: 現在価格
            current_time: 現在時刻（MFE更新時刻の追跡用）
        """
        if self.direction == SignalType.BUY:
            self.highest_price = max(self.highest_price, current_price)
            self.current_r = (
                (current_price - self.entry_price) / self.r_value
                if self.r_value > 0 else 0.0
            )
        else:
            # SELL: lowest_priceで最安値を追跡
            if self.lowest_price <= 0.0:
                self.lowest_price = current_price
            else:
                self.lowest_price = min(
                    self.lowest_price, current_price,
                )
            self.current_r = (
                (self.entry_price - current_price) / self.r_value
                if self.r_value > 0 else 0.0
            )

        prev_highest_r = self.highest_r
        self.highest_r = max(self.highest_r, self.current_r)
        # MFEが更新された場合、最終更新時刻を記録
        if self.highest_r > prev_highest_r and current_time is not None:
            self.mfe_last_update_time = current_time


@dataclass(frozen=True)
class PositionManagerConfig:
    """ポジション管理設定

    Attributes:
        partial_close_1r_ratio: 1R到達時の部分決済比率
        partial_close_2r_ratio: 2R到達時の部分決済比率
        breakeven_at_1r: 1Rで建値移動するか
        trailing_start_r: トレーリング開始R値
        trailing_atr_multiplier: ATRトレーリング倍率
        trailing_stage2_enabled: 2段階トレーリングを有効にするか
        trailing_stage2_r: Stage2開始R値
        trailing_stage2_atr_multiplier: Stage2 ATR倍率
        time_exit_enabled: 時間決済を有効にするか
        spread_pips: スプレッド（pips）
        slippage_pips: スリッページ（pips）
        be_enabled_modes: BE移動を有効にするモード
        early_breakeven_r: 早期BE移動のR閾値
        early_breakeven_enabled: 早期BE移動を有効にするか
        disable_tp_after_partial: 1R部分利確後にTPを無効化するか
        be_cushion_pips: BE移動時の利益方向クッション(pips)
    """

    partial_close_1r_ratio: float = 0.50
    partial_close_2r_ratio: float = 0.05
    # 1.5R部分利確（BT検証: stage2トレーリングが先に発動するため実質不使用）
    partial_close_15r_enabled: bool = False
    partial_close_15r_ratio: float = 0.20
    breakeven_at_1r: bool = True
    trailing_start_r: float = 0.5
    trailing_atr_multiplier: float = 2.0
    # 2段階トレーリング: 1.2R到達でATR倍率を引き締め（BT検証: 1.0Rより1.2Rが優位）
    trailing_stage2_enabled: bool = True
    trailing_stage2_r: float = 1.2
    trailing_stage2_atr_multiplier: float = 1.2
    # 3段階トレーリング: BT検証で実質不使用のためデフォルト無効
    trailing_stage3_enabled: bool = False
    trailing_stage3_r: float = 1.5
    trailing_stage3_atr_multiplier: float = 0.7
    time_exit_enabled: bool = True
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    be_enabled_modes: tuple[str, ...] = (
        UNIVERSAL_MODE,
    )
    early_breakeven_r: float = 0.6  # BT検証(0000062): 0.3Rは過剰, 0.6Rが最適
    early_breakeven_enabled: bool = True
    disable_tp_after_partial: bool = True
    signal_rev_close_ratio: float = 0.0
    stagnation_exit_minutes: float = 90.0
    stagnation_min_mfe_r: float = 0.10
    # 超早期exit（MFE<0.2R + 30分経過）
    very_early_exit_enabled: bool = False
    very_early_exit_minutes: float = 30.0
    very_early_exit_mfe_r: float = 0.2
    # P0-1: RANGE×DAY BE制御
    range_day_be_disabled: bool = True
    range_day_early_be_r: float = 0.3
    # P0-1: 速度ベースBE（RANGE×DAY）
    range_day_fast_be_enabled: bool = True
    range_day_fast_be_minutes: float = 90.0
    # P0-2: RANGE×DAY stagnation段階化
    range_day_stagnation_enabled: bool = False
    range_day_stagnation_stage1_minutes: float = 45.0
    range_day_stagnation_stage1_min_mfe_r: float = 0.05
    range_day_stagnation_stage2_minutes: float = 60.0
    range_day_stagnation_stage2_min_mfe_r: float = 0.10
    # P0-3: 0.5R小利確（デフォルトOFF）
    early_partial_close_enabled: bool = False
    early_partial_close_ratio: float = 0.25
    # RANGE×DAY 軽い保険（スパイク反転防止）
    range_day_insurance_enabled: bool = False
    range_day_insurance_max_minutes: float = 30.0
    range_day_insurance_sl_offset_r: float = -0.1
    range_day_insurance_partial_ratio: float = 0.20
    # TP_EARLY厳格化
    insurance_trigger_r: float = 1.0
    insurance_block_high_mfe_r: float = 0.8
    insurance_min_holding_minutes: float = 15.0
    # RANGE×DAY 0.5R部分確定
    range_day_half_r_partial_enabled: bool = False
    range_day_half_r_partial_ratio: float = 0.20
    range_day_half_r_trigger: float = 0.5
    # BE移動クッション: BE価格に余裕を持たせノイズによるBE_HIT削減
    be_cushion_pips: float = 3.0
    # pip単位（JPY=0.01, USD=0.0001）
    pip_unit: float = 0.01
    # コンセンサス逆転exit
    consensus_exit_enabled: bool = True
    # 逆方向スコアがこの閾値以上で発動
    consensus_exit_threshold: float = 6.0
    # ポジション方向スコアがこの閾値以下で発動
    consensus_exit_own_max: float = 3.0
    # 含み損時のみ発動（含み益時はトレーリングに任せる）
    consensus_exit_loss_only: bool = False
    # 利益反転ガード: MFE到達後の利益急落で早期退出
    profit_reversal_enabled: bool = False
    # MFEがこのR値以上に達した後に発動対象
    profit_reversal_mfe_r: float = 0.3
    # highest_rからの下落がこの値以上で発動
    profit_reversal_drop_r: float = 0.25
    # current_rがこの値以下で発動
    profit_reversal_max_r: float = 0.05
    # ユニバーサル0.5R部分利確（全レジーム対応）
    universal_half_r_enabled: bool = False
    universal_half_r_trigger: float = 0.5
    universal_half_r_ratio: float = 0.25
    # 段階的STAGNATION: 3段階で早期に停滞を検出
    progressive_stagnation_enabled: bool = False
    # Stage1: 早期検出（60分 + MFE<0.05R + 含み損）
    stagnation_stage1_minutes: float = 60.0
    stagnation_stage1_mfe_r: float = 0.05
    stagnation_stage1_max_r: float = -0.15
    # Stage2: 中期検出（90分 + MFE<0.10R + 含み損）
    stagnation_stage2_minutes: float = 90.0
    stagnation_stage2_mfe_r: float = 0.10
    stagnation_stage2_max_r: float = -0.10
    # レジーム別stagnation時間の上書き（None=ハードコード値を使用）
    stag_trend_minutes: float | None = None
    stag_range_minutes: float | None = None
    # BREAKOUT時のstagnation時間（None=TREND値を使用）
    stag_breakout_minutes: float | None = None
    # STAGNATION予防的SL引き締め
    stag_pretighten_enabled: bool = True
    # stag時間の何%で発動するか
    stag_pretighten_pct: float = 0.80
    # MFE閾値（これ未満で発動）
    stag_pretighten_mfe_r: float = 0.10
    # SLターゲットR値（エントリーからの距離）
    stag_pretighten_sl_r: float = -0.05
    # 早期利益ガード: 小利益+センチメント悪化で早期撤退
    early_profit_guard_enabled: bool = True
    # MFE最低値（一度は有利に動いた証拠）
    early_profit_guard_min_mfe_r: float = 0.05
    # 現在含み益の最低R値
    early_profit_guard_min_r: float = 0.0
    # 大利益は対象外（profit_reversalに任せる）
    early_profit_guard_max_r: float = 0.30
    # 逆方向スコア - 自方向スコアの差（これ以上で発動）
    early_profit_guard_score_diff: float = 1.0
    # 逆方向スコアの最低値（ノイズ排除）
    early_profit_guard_min_opp_score: float = 4.0
    # 最低保有時間（分、エントリーノイズ排除）
    early_profit_guard_min_hold_minutes: float = 5.0
    # 最大保有時間の上書き（分）— 全TF共通で適用
    # None=TFベースのデフォルト値を使用
    max_holding_minutes_override: float | None = None
    # レジーム別トレーリングATR倍率（None=グローバル値を使用）
    trailing_trend_atr_multiplier: float | None = None
    trailing_trend_stage2_atr_multiplier: float | None = None
    # レジーム別最大保有時間（分）（None=TFベースのデフォルト値を使用）
    max_holding_minutes_trend: float | None = None
    # 指標前ポジション管理
    pre_event_exit_enabled: bool = False
    pre_event_minutes: float = 30.0
    pre_event_profit_close: bool = True
    pre_event_loss_tighten: bool = True
    pre_event_tighten_r: float = -0.3
    # 週末前ポジション強制クローズ（金曜日の指定時刻UTC以降）
    weekend_close_enabled: bool = True
    weekend_close_hour: int = 20
    weekend_close_minute: int = 30
    # エッジ劣化監視: エントリー時のコンセンサススコアと比較して劣化率を監視
    # モードA: 純粋エッジ劣化exit
    edge_decay_exit_enabled: bool = True
    # エントリースコアからの劣化率がこの閾値以上で発動（0.40=40%劣化）
    edge_decay_exit_threshold: float = 0.50
    # 最低保有バー数（エントリー直後のノイズ排除）
    edge_decay_exit_min_bars: int = 5
    # 許容最大損失R値（これより大きな損失は通常SLに任せる）
    edge_decay_exit_max_loss_r: float = -0.3
    # モードB: 利益侵食 + エッジ劣化 複合exit（利益を守る）
    edge_decay_profit_exit_enabled: bool = True
    # MFEピークからの利益侵食率がこの閾値以上で発動（0.60=60%侵食）
    edge_decay_profit_erosion_threshold: float = 0.60
    # モードB発動に必要な最低エッジ劣化率（緩い閾値）
    edge_decay_profit_decay_min: float = 0.25
    # Stagnation連携: エッジ劣化時にstagnation時間閾値を短縮
    edge_decay_stagnation_enabled: bool = True
    # この劣化率以上でstagnation閾値を短縮
    edge_decay_stagnation_threshold: float = 0.35
    # stagnation時間をこの倍率に短縮（0.65=65%に短縮）
    edge_decay_stagnation_multiplier: float = 0.65
    # --- Profit Plateau Exit（利益停滞検出による動的エグジット）---
    # MFE（最高含み益）の更新が停止した事実を検出し、利益が残るうちに決済する。
    # MFE分析結果: 中央値23分でMFEピーク到達、1時間以降は利益フラット。
    profit_plateau_enabled: bool = False
    # 発動に必要な最低MFE（一度はこれだけ有利に動いた証拠）
    profit_plateau_min_mfe_r: float = 0.15
    # MFE更新が停止してからの待機時間（分）
    profit_plateau_stall_minutes: float = 15.0
    # 現在まだ含み益であること（この値以上）
    profit_plateau_min_current_r: float = 0.0
    # ピークからの後退率がこの値以上なら待機時間を短縮
    profit_plateau_retreat_threshold: float = 0.40
    # 後退検出時の待機時間短縮倍率
    profit_plateau_retreat_multiplier: float = 0.5


class PositionManager:
    """ポジション管理器

    保有中の管理を統合:
    1. SL/TP到達チェック
    2. Time exit（mode依存の最大保有時間）
    3. シグナル反転撤退
    4. 部分利確（R値ベース: 1R/2R/3R以降トレーリング）
    5. トレーリング更新（ATRベース、建値移動）
    """

    # デフォルト最大保有時間（分）※動的TFにより上書きされる
    DEFAULT_MAX_HOLDING_MINUTES: int = 480

    def __init__(self, config: PositionManagerConfig | None = None) -> None:
        """初期化

        Args:
            config: ポジション管理設定
        """
        self.config = config or PositionManagerConfig()
        self._positions: dict[str, ManagedPosition] = {}
        self._partial_closed_1r: set[str] = set()
        self._partial_closed_2r: set[str] = set()
        self._partial_closed_15r: set[str] = set()
        self._early_be_applied: set[str] = set()
        self._tp_disabled: set[str] = set()
        self._insurance_sl_applied: set[str] = set()
        self._insurance_partial_applied: set[str] = set()
        self._half_r_partial_applied: set[str] = set()

    def register_position(
        self,
        position_id: str,
        direction: SignalType,
        entry_price: float,
        entry_time: datetime,
        sl: float,
        tp: float,
        volume: float,
        plan: TradingPlan,
        entry_own_score: float = 0.0,
    ) -> None:
        """ポジションを登録

        Args:
            position_id: ポジションID
            direction: ポジション方向
            entry_price: エントリー価格
            entry_time: エントリー時刻
            sl: SL価格
            tp: TP価格
            volume: ロット数
            plan: トレーディングプラン
            entry_own_score: エントリー時の自方向コンセンサススコア（エッジ劣化監視用）
        """
        self._positions[position_id] = ManagedPosition(
            position_id=position_id,
            direction=direction,
            entry_price=entry_price,
            entry_time=entry_time,
            original_sl=sl,
            original_tp=tp,
            current_sl=sl,
            initial_volume=volume,
            remaining_volume=volume,
            plan=plan,
            highest_price=entry_price,
            lowest_price=entry_price,
            entry_own_score=entry_own_score,
        )

    def unregister_position(self, position_id: str) -> None:
        """ポジションを登録解除

        Args:
            position_id: ポジションID
        """
        self._positions.pop(position_id, None)
        self._partial_closed_1r.discard(position_id)
        self._partial_closed_2r.discard(position_id)
        self._partial_closed_15r.discard(position_id)
        self._early_be_applied.discard(position_id)
        self._tp_disabled.discard(position_id)
        self._insurance_sl_applied.discard(position_id)
        self._insurance_partial_applied.discard(position_id)
        self._half_r_partial_applied.discard(position_id)

    def export_state(
        self, position_id: str,
    ) -> dict | None:
        """ポジション管理状態をdictにエクスポート

        DB非依存。ManagedPositionの追跡値と全7フラグを
        dictとして出力する。

        Note:
            current_sl, current_r, remaining_volumeは
            永続化しない。MT5の最新値で再設定される。

        Args:
            position_id: ポジションID

        Returns:
            dict | None: 状態dict（未登録の場合None）
        """
        pos = self._positions.get(position_id)
        if pos is None:
            return None
        return {
            "position_id": position_id,
            "highest_price": pos.highest_price,
            "lowest_price": pos.lowest_price,
            "highest_r": pos.highest_r,
            "bars_held": pos.bars_held,
            "trailing_activated": pos.trailing_activated,
            "entry_own_score": pos.entry_own_score,
            "mfe_last_update_time": (
                pos.mfe_last_update_time.isoformat()
                if pos.mfe_last_update_time is not None
                else None
            ),
            "partial_closed_1r": (
                position_id in self._partial_closed_1r
            ),
            "partial_closed_2r": (
                position_id in self._partial_closed_2r
            ),
            "partial_closed_15r": (
                position_id in self._partial_closed_15r
            ),
            "tp_disabled": (
                position_id in self._tp_disabled
            ),
            "early_be_applied": (
                position_id in self._early_be_applied
            ),
            "insurance_sl_applied": (
                position_id in self._insurance_sl_applied
            ),
            "insurance_partial_applied": (
                position_id
                in self._insurance_partial_applied
            ),
            "half_r_partial_applied": (
                position_id
                in self._half_r_partial_applied
            ),
        }

    def import_state(
        self, position_id: str, state: dict,
    ) -> None:
        """管理状態をインポートして復元

        DB非依存。dictの追跡値をManagedPositionに上書きし、
        フラグsetに追加する。

        Args:
            position_id: ポジションID
            state: export_state()で出力されたdict
        """
        pos = self._positions.get(position_id)
        if pos is None:
            return

        # 追跡値の復元
        pos.highest_price = state.get(
            "highest_price", pos.highest_price
        )
        pos.lowest_price = state.get(
            "lowest_price", pos.lowest_price
        )
        pos.highest_r = state.get(
            "highest_r", pos.highest_r
        )
        pos.bars_held = state.get(
            "bars_held", pos.bars_held
        )
        pos.trailing_activated = state.get(
            "trailing_activated", pos.trailing_activated
        )
        pos.entry_own_score = state.get(
            "entry_own_score", pos.entry_own_score
        )
        _mfe_t = state.get("mfe_last_update_time")
        if _mfe_t is not None:
            pos.mfe_last_update_time = datetime.fromisoformat(
                _mfe_t,
            )

        # フラグの復元（対称的: True→add, False→discard）
        _flag_map = {
            "partial_closed_1r": self._partial_closed_1r,
            "partial_closed_2r": self._partial_closed_2r,
            "partial_closed_15r": self._partial_closed_15r,
            "tp_disabled": self._tp_disabled,
            "early_be_applied": self._early_be_applied,
            "insurance_sl_applied": (
                self._insurance_sl_applied
            ),
            "insurance_partial_applied": (
                self._insurance_partial_applied
            ),
            "half_r_partial_applied": (
                self._half_r_partial_applied
            ),
        }
        for key, flag_set in _flag_map.items():
            if state.get(key):
                flag_set.add(position_id)
            else:
                flag_set.discard(position_id)

    def evaluate(
        self,
        position_id: str,
        current_price: float,
        current_time: datetime,
        atr: float,
        current_signal: SignalType | None = None,
        buy_score: float = 0.0,
        sell_score: float = 0.0,
        fundamental_assessment: object | None = None,
    ) -> ManagementAction:
        """ポジションを評価

        Args:
            position_id: ポジションID
            current_price: 現在価格
            current_time: 現在時刻
            atr: ATR値
            current_signal: 現在のシグナル（反転チェック用）
            buy_score: BUY方向コンセンサススコア
            sell_score: SELL方向コンセンサススコア
            fundamental_assessment: ファンダメンタル評価結果

        Returns:
            ManagementAction: 管理アクション
        """
        position = self._positions.get(position_id)
        if position is None:
            return ManagementAction.hold("ポジション未登録")

        # 価格更新
        position.update_price(current_price, current_time)
        position.bars_held += 1

        # エッジ劣化率を計算（以降のチェックで共有）
        _own_score = (
            buy_score
            if position.direction == SignalType.BUY
            else sell_score
        )
        _decay_ratio = 0.0
        if position.entry_own_score > 0 and buy_score + sell_score > 0:
            _decay_ratio = max(
                0.0,
                (position.entry_own_score - _own_score)
                / position.entry_own_score,
            )

        # 1. SL到達チェック
        action = self._check_sl(position, current_price)
        if action is not None:
            self._try_state_transition(position, action)
            return action

        # 2. 部分利確チェック（1R, 2R）※TP前に実行
        action = self._check_partial_close(
            position, current_price, current_time,
        )
        if action is not None:
            self._try_state_transition(position, action)
            return action

        # 3. TP到達チェック（1R後は無効化可能）
        action = self._check_tp(position, current_price)
        if action is not None:
            self._try_state_transition(position, action)
            return action

        # 3.5 利益反転ガード
        if self.config.profit_reversal_enabled:
            action = self._check_profit_reversal(
                position, current_price,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 3.6 早期利益ガード
        if self.config.early_profit_guard_enabled:
            action = self._check_early_profit_guard(
                position, current_price, current_time,
                buy_score, sell_score,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 3.7 週末前強制クローズ
        if self.config.weekend_close_enabled:
            action = self._check_weekend_close(
                position, current_price, current_time,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 3.8 指標前ポジション管理
        if self.config.pre_event_exit_enabled:
            action = self._check_pre_event_exit(
                position, current_price,
                fundamental_assessment,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 3.9 利益停滞exit（Profit Plateau）
        if self.config.profit_plateau_enabled:
            action = self._check_profit_plateau(
                position, current_time, current_price,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 4. 進捗なしExitチェック
        action = self._check_stagnation_exit(
            position, current_time, current_price,
            decay_ratio=_decay_ratio,
        )
        if action is not None:
            self._try_state_transition(position, action)
            return action

        # 5. 時間決済チェック
        if self.config.time_exit_enabled:
            action = self._check_time_exit(
                position, current_time, current_price,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 6. シグナル反転チェック
        if current_signal is not None:
            action = self._check_signal_reversal(
                position, current_signal, current_price,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 6.5 コンセンサス逆転exit
        if self.config.consensus_exit_enabled:
            action = self._check_consensus_exit(
                position, current_price,
                buy_score, sell_score,
            )
            if action is not None:
                self._try_state_transition(
                    position, action,
                )
                return action

        # 6.6 エッジ劣化exit
        action = self._check_edge_decay_exit(
            position, current_price,
            _own_score, _decay_ratio,
        )
        if action is not None:
            self._try_state_transition(position, action)
            return action

        # 7. トレーリング更新
        action = self._check_trailing(
            position, current_price, atr,
            fundamental_assessment=fundamental_assessment,
        )
        if action is not None:
            self._try_state_transition(position, action)
            return action

        return ManagementAction.hold("条件未達")

    # --- 状態遷移マッピング ---
    _ACTION_TO_STATE: dict[
        ManagementActionType, PositionState
    ] = {
        ManagementActionType.UPDATE_SL: (
            PositionState.TRAILING
        ),
        ManagementActionType.PARTIAL_CLOSE: (
            PositionState.PARTIAL_CLOSED
        ),
        ManagementActionType.FULL_CLOSE: (
            PositionState.CLOSED
        ),
    }

    def _try_state_transition(
        self,
        position: ManagedPosition,
        action: ManagementAction,
    ) -> None:
        """アクションに応じた状態遷移を試行（警告のみ）

        段階的導入: 遷移不可でもブロックせず警告ログのみ出力。

        Args:
            position: 管理対象ポジション
            action: 実行予定のアクション
        """
        target = self._ACTION_TO_STATE.get(
            action.action_type,
        )
        if target is None:
            return

        sm = position.state_machine
        if sm.can_transition(target):
            sm.transition(target)
        else:
            logger.warning(
                "状態遷移スキップ: %s (%s → %s), "
                "アクション=%s, 理由=%s",
                position.position_id,
                sm.state.value,
                target.value,
                action.action_type.value,
                action.reason,
            )

    def _get_be_price(
        self,
        position: ManagedPosition,
    ) -> float:
        """スプレッド+スリッページ+クッションを考慮したBE価格

        クッションによりノイズでのBE_HITを防ぎ、
        利益方向に少し余裕を持たせる。

        Args:
            position: 管理対象ポジション

        Returns:
            float: BE価格（クッション込み）
        """
        # entry=fill(Ask/Bid+slip), exit=trigger±slip
        # 両方にslippage適用 → BE損益ゼロには2倍必要
        _pu = self.config.pip_unit
        slip_offset = self.config.slippage_pips * 2 * _pu
        # クッション: BE_HIT頻度低減のため利益方向に余裕
        cushion = self.config.be_cushion_pips * _pu
        offset = slip_offset + cushion
        if position.direction == SignalType.BUY:
            return position.entry_price + offset
        else:
            return position.entry_price - offset

    def _check_sl(
        self,
        position: ManagedPosition,
        current_price: float,
    ) -> ManagementAction | None:
        """SL到達チェック（建値=BREAKEVENを識別）"""
        is_hit = False
        if position.direction == SignalType.BUY:
            is_hit = current_price <= position.current_sl
        else:
            is_hit = current_price >= position.current_sl

        if not is_hit:
            return None

        # 建値移動後のSL = be_priceならBREAKEVEN
        be_price = self._get_be_price(position)
        is_breakeven = (
            abs(position.current_sl - be_price)
            < position.r_value * 0.05
        )
        if is_breakeven:
            exit_reason = ExitReason.BREAKEVEN
        elif position.trailing_activated:
            exit_reason = ExitReason.TRAILING_STOP
        else:
            exit_reason = ExitReason.STOP_LOSS
        return ManagementAction.full_close(
            f"SL到達: {current_price:.5f} vs "
            f"{position.current_sl:.5f}",
            exit_reason,
            trigger_price=position.current_sl,
        )

    def _check_tp(
        self,
        position: ManagedPosition,
        current_price: float,
    ) -> ManagementAction | None:
        """TP到達チェック"""
        # 1R部分利確後のTP無効化
        if position.position_id in self._tp_disabled:
            return None

        if position.direction == SignalType.BUY:
            if current_price >= position.original_tp:
                return ManagementAction.full_close(
                    f"TP到達: {current_price:.5f} "
                    f">= {position.original_tp:.5f}",
                    ExitReason.TAKE_PROFIT,
                    trigger_price=position.original_tp,
                )
        else:
            if current_price <= position.original_tp:
                return ManagementAction.full_close(
                    f"TP到達: {current_price:.5f} "
                    f"<= {position.original_tp:.5f}",
                    ExitReason.TAKE_PROFIT,
                    trigger_price=position.original_tp,
                )
        return None

    def _check_profit_plateau(
        self,
        position: ManagedPosition,
        current_time: datetime,
        current_price: float,
    ) -> ManagementAction | None:
        """利益停滞検出による動的エグジット

        MFE（最高含み益）の更新が一定時間停止した事実を検出し、
        利益が残っているうちに決済する。

        判定データ（全て事実ベース）:
        1. highest_r >= min_mfe_r → 一度は有意な利益に到達した
        2. mfe_last_update_time からの経過 → 利益更新が止まっている
        3. current_r >= min_current_r → まだ含み益がある
        4. (highest_r - current_r) / highest_r → ピークからの後退率

        MFE分析に基づく根拠:
        - MFE到達の中央値は23分、86%が1時間以内
        - 30分以降の利益カーブはフラット
        - 利益が伸びなくなった後は返す傾向が強い
        """
        cfg = self.config

        # MFEが最低閾値に達していなければスキップ
        if position.highest_r < cfg.profit_plateau_min_mfe_r:
            return None

        # 現在含み益が最低ラインを下回っていればスキップ
        if position.current_r < cfg.profit_plateau_min_current_r:
            return None

        # MFE最終更新時刻が未設定（初回バー等）
        mfe_time = position.mfe_last_update_time
        if mfe_time is None:
            return None

        # MFE更新停止からの経過時間
        stall_minutes = (
            (current_time - mfe_time).total_seconds() / 60
        )

        # ピークからの後退率を計算
        effective_stall = cfg.profit_plateau_stall_minutes
        if position.highest_r > 0:
            retreat_ratio = (
                (position.highest_r - position.current_r)
                / position.highest_r
            )
            # 後退率が閾値以上なら待機時間を短縮
            if retreat_ratio >= cfg.profit_plateau_retreat_threshold:
                effective_stall *= (
                    cfg.profit_plateau_retreat_multiplier
                )
        else:
            retreat_ratio = 0.0

        if stall_minutes >= effective_stall:
            return ManagementAction.full_close(
                reason=(
                    f"利益停滞: MFE={position.highest_r:.2f}R"
                    f"→現在{position.current_r:.2f}R"
                    f" (停滞{stall_minutes:.0f}分,"
                    f" 後退{retreat_ratio:.0%})"
                ),
                exit_reason=ExitReason.PROFIT_PLATEAU,
                trigger_price=current_price,
            )

        return None

    def _check_stagnation_exit(
        self,
        position: ManagedPosition,
        current_time: datetime,
        current_price: float,
        decay_ratio: float = 0.0,
    ) -> ManagementAction | None:
        """進捗なしExit

        一定時間経過後、MFE(highest_r)が閾値未満なら撤退。
        レジームベースで動的に閾値を設定:
        - TREND: 60分（早期検知）
        - RANGE: 90分（レンジ内での停滞を許容）
        - CHOPPY: 120分（従来値）

        エッジ劣化が閾値以上の場合、stagnation時間を短縮する。
        """
        regime = getattr(position.plan, "regime", None)
        is_range = regime == "RANGE"
        elapsed = (
            (current_time - position.entry_time).total_seconds()
            / 60
        )

        # MFE<0.2R + 30分経過: 超早期exit（全レジーム共通）
        cfg = self.config
        if (
            cfg.very_early_exit_enabled
            and elapsed >= cfg.very_early_exit_minutes
            and position.highest_r < cfg.very_early_exit_mfe_r
        ):
            return ManagementAction.full_close(
                f"超早期exit: {elapsed:.0f}分経過,"
                f" MFE={position.highest_r:.2f}R"
                f"<{cfg.very_early_exit_mfe_r}R",
                ExitReason.STAGNATION,
                trigger_price=current_price,
            )

        if (
            is_range
            and self.config.range_day_stagnation_enabled
        ):
            # Stage1: 45分 + MFE<0.05R → 早期撤退
            s1_min = (
                self.config
                .range_day_stagnation_stage1_minutes
            )
            s1_mfe = (
                self.config
                .range_day_stagnation_stage1_min_mfe_r
            )
            if (
                elapsed >= s1_min
                and position.highest_r < s1_mfe
            ):
                return ManagementAction.full_close(
                    f"進捗なし(S1): {elapsed:.0f}分経過,"
                    f" MFE={position.highest_r:.2f}R",
                    ExitReason.STAGNATION,
                    trigger_price=current_price,
                )
            # Stage2: 60分 + MFE<0.10R → 通常撤退
            s2_min = (
                self.config
                .range_day_stagnation_stage2_minutes
            )
            s2_mfe = (
                self.config
                .range_day_stagnation_stage2_min_mfe_r
            )
            if (
                elapsed >= s2_min
                and position.highest_r < s2_mfe
            ):
                return ManagementAction.full_close(
                    f"進捗なし(S2): {elapsed:.0f}分経過,"
                    f" MFE={position.highest_r:.2f}R",
                    ExitReason.STAGNATION,
                    trigger_price=current_price,
                )
            return None

        # 段階的STAGNATION（有効時）
        if self.config.progressive_stagnation_enabled:
            cfg = self.config
            # Stage1: 60分 + MFE<0.05R + 含み損(-0.15R以下)
            if (
                elapsed >= cfg.stagnation_stage1_minutes
                and position.highest_r
                < cfg.stagnation_stage1_mfe_r
                and position.current_r
                <= cfg.stagnation_stage1_max_r
            ):
                return ManagementAction.full_close(
                    f"進捗なし(S1): {elapsed:.0f}分,"
                    f" MFE={position.highest_r:.2f}R,"
                    f" R={position.current_r:.2f}",
                    ExitReason.STAGNATION,
                    trigger_price=current_price,
                )
            # Stage2: 90分 + MFE<0.10R + 含み損(-0.10R以下)
            if (
                elapsed >= cfg.stagnation_stage2_minutes
                and position.highest_r
                < cfg.stagnation_stage2_mfe_r
                and position.current_r
                <= cfg.stagnation_stage2_max_r
            ):
                return ManagementAction.full_close(
                    f"進捗なし(S2): {elapsed:.0f}分,"
                    f" MFE={position.highest_r:.2f}R,"
                    f" R={position.current_r:.2f}",
                    ExitReason.STAGNATION,
                    trigger_price=current_price,
                )

        # STAGNATION予防的SL引き締め（TREND/BREAKOUT）
        if (
            self.config.stag_pretighten_enabled
            and regime in ("TREND", "BREAKOUT")
        ):
            _stag_min = (
                self.config.stag_trend_minutes
                if self.config.stag_trend_minutes is not None
                else 90.0
            )
            _at = _stag_min * self.config.stag_pretighten_pct
            if (
                elapsed >= _at
                and position.highest_r
                < self.config.stag_pretighten_mfe_r
            ):
                # エントリー ± pretighten_sl_r のR値でSL計算
                _r_val = position.r_value
                if _r_val > 0:
                    if position.direction == SignalType.BUY:
                        _new_sl = (
                            position.entry_price
                            + self.config.stag_pretighten_sl_r
                            * _r_val
                        )
                        # 現在SLより有利な場合のみ更新
                        if _new_sl > position.current_sl:
                            position.current_sl = _new_sl
                            return ManagementAction.update_sl(
                                new_sl=_new_sl,
                                reason=(
                                    f"STAG予防SL引締: "
                                    f"{elapsed:.0f}分経過,"
                                    f" MFE={position.highest_r:.2f}R,"
                                    f" SL→{_new_sl:.5f}"
                                ),
                            )
                    else:
                        _new_sl = (
                            position.entry_price
                            - self.config.stag_pretighten_sl_r
                            * _r_val
                        )
                        # 現在SLより有利な場合のみ更新
                        if _new_sl < position.current_sl:
                            position.current_sl = _new_sl
                            return ManagementAction.update_sl(
                                new_sl=_new_sl,
                                reason=(
                                    f"STAG予防SL引締: "
                                    f"{elapsed:.0f}分経過,"
                                    f" MFE={position.highest_r:.2f}R,"
                                    f" SL→{_new_sl:.5f}"
                                ),
                            )

        # レジームベース動的STAGNATION時間
        # BREAKOUT: TREND値+30分（猶予延長）、TREND: 90分、
        # RANGE: 120分、CHOPPY/その他: 120分
        _trend_min = (
            self.config.stag_trend_minutes
            if self.config.stag_trend_minutes is not None
            else 90.0
        )
        _range_min = (
            self.config.stag_range_minutes
            if self.config.stag_range_minutes is not None
            else 120.0
        )
        _breakout_min = (
            self.config.stag_breakout_minutes
            if self.config.stag_breakout_minutes
            is not None
            else _trend_min + 30.0
        )
        regime_stag_minutes = {
            "BREAKOUT": _breakout_min,
            "TREND": _trend_min,
            "RANGE": _range_min,
            "CHOPPY": self.config.stagnation_exit_minutes,
        }.get(regime, self.config.stagnation_exit_minutes)

        # エッジ劣化が閾値以上の場合はstagnation時間を短縮
        _stag_decay_note = ""
        if (
            self.config.edge_decay_stagnation_enabled
            and decay_ratio
            >= self.config.edge_decay_stagnation_threshold
        ):
            regime_stag_minutes = (
                regime_stag_minutes
                * self.config.edge_decay_stagnation_multiplier
            )
            _stag_decay_note = (
                f" [decay={decay_ratio:.0%}→短縮]"
            )

        stag_mfe = self.config.stagnation_min_mfe_r

        if (
            elapsed >= regime_stag_minutes
            and position.highest_r < stag_mfe
        ):
            return ManagementAction.full_close(
                f"進捗なし({regime}): {elapsed:.0f}分経過,"
                f" MFE={position.highest_r:.2f}R"
                f"{_stag_decay_note}",
                ExitReason.STAGNATION,
                trigger_price=current_price,
            )
        return None

    def _check_time_exit(
        self,
        position: ManagedPosition,
        current_time: datetime,
        current_price: float,
    ) -> ManagementAction | None:
        """時間決済チェック"""
        # dynamic_entry_tf に基づいて保有時間を動的計算
        from autotrader.config.tf_params_registry import (
            get_holding_minutes,
        )
        entry_tf = (
            getattr(position.plan, "dynamic_entry_tf", None)
            or position.plan.entry_tf
        )
        # レジーム別最大保有時間の解決
        regime = getattr(position.plan, "regime", None)
        if (
            regime in ("TREND", "BREAKOUT")
            and self.config.max_holding_minutes_trend is not None
        ):
            max_minutes = self.config.max_holding_minutes_trend
        elif self.config.max_holding_minutes_override is not None:
            max_minutes = self.config.max_holding_minutes_override
        else:
            max_minutes = get_holding_minutes(entry_tf)
        elapsed = (
            current_time - position.entry_time
        ).total_seconds() / 60

        if elapsed >= max_minutes:
            return ManagementAction.full_close(
                f"時間決済: {elapsed:.0f}分 >= 最大{max_minutes}分",
                ExitReason.TIME_EXIT,
                trigger_price=current_price,
            )
        return None

    def _check_signal_reversal(
        self,
        position: ManagedPosition,
        current_signal: SignalType,
        current_price: float,
    ) -> ManagementAction | None:
        """シグナル反転チェック

        含み損(R<=0)の場合は無視しSLに委ねる。
        含み益(R>0)の場合は部分決済+BE移動で利益確保。
        """
        if current_signal == SignalType.HOLD:
            return None

        is_reversal = (
            (position.direction == SignalType.BUY and
             current_signal == SignalType.SELL) or
            (position.direction == SignalType.SELL and
             current_signal == SignalType.BUY)
        )

        if is_reversal:
            # 含み損 → 無視（SLに委ねる）
            if position.current_r <= 0:
                return None
            # 含み益 → 部分決済 + BE移動
            be_price = self._get_be_price(position)
            return ManagementAction.partial_close(
                ratio=self.config.signal_rev_close_ratio,
                new_sl=be_price,
                reason=(
                    f"反転縮小: {position.current_r:.2f}R,"
                    f" SL→BE"
                ),
                exit_reason=ExitReason.SIGNAL_REVERSAL,
                trigger_price=current_price,
            )
        return None

    def _check_consensus_exit(
        self,
        position: ManagedPosition,
        current_price: float,
        buy_score: float,
        sell_score: float,
    ) -> ManagementAction | None:
        """コンセンサス逆転exit

        ポジション方向のスコアが低下し、逆方向スコアが
        閾値を超えた場合に早期撤退する。
        SL到達前に損失を最小化し、利益がある場合は残す。

        Args:
            position: 管理中ポジション
            current_price: 現在価格
            buy_score: 現在のBUY方向スコア
            sell_score: 現在のSELL方向スコア

        Returns:
            ManagementAction | None: 決済アクション
        """
        # スコアが両方0（HOLDシグナル、データなし等）は無視
        if buy_score == 0.0 and sell_score == 0.0:
            return None

        # ポジション方向に応じてスコアを取得
        if position.direction == SignalType.BUY:
            own_score = buy_score
            opp_score = sell_score
        else:
            own_score = sell_score
            opp_score = buy_score

        # 逆方向スコアが閾値を超え、かつ自方向が弱い
        if (
            opp_score >= self.config.consensus_exit_threshold
            and own_score <= self.config.consensus_exit_own_max
        ):
            # 含み益制限モード: 含み損時のみ発動
            if (
                self.config.consensus_exit_loss_only
                and position.current_r > 0
            ):
                return None

            return ManagementAction.full_close(
                reason=(
                    f"コンセンサス逆転: "
                    f"自方向={own_score:.1f}, "
                    f"逆方向={opp_score:.1f}"
                ),
                exit_reason=ExitReason.SIGNAL_REVERSAL,
                trigger_price=current_price,
            )

        return None

    def _check_edge_decay_exit(
        self,
        position: ManagedPosition,
        current_price: float,
        own_score: float,
        decay_ratio: float,
    ) -> ManagementAction | None:
        """エッジ劣化exit

        エントリー時のコンセンサススコアと現在スコアを比較し、
        劣化率が閾値を超えた場合に早期撤退する。

        モードB（優先）: 利益が出ている状態でMFEピークから大きく後退し、
        かつエッジも劣化している場合、プラスのうちに撤退。

        モードA: 損失が小さい範囲でエッジが大幅に劣化した場合に撤退。

        Args:
            position: 管理中ポジション
            current_price: 現在価格
            own_score: 現在の自方向コンセンサススコア
            decay_ratio: エントリー時からのスコア劣化率（0.0〜1.0）

        Returns:
            ManagementAction | None: 決済アクション
        """
        cfg = self.config

        # エントリースコアが記録されていない場合はスキップ
        if position.entry_own_score <= 0:
            return None

        # 最低保有バー数未満はスキップ（エントリー直後のノイズ排除）
        if position.bars_held < cfg.edge_decay_exit_min_bars:
            return None

        # モードB: 利益侵食 + エッジ劣化（利益を守る・優先）
        if cfg.edge_decay_profit_exit_enabled:
            if (
                position.current_r > 0  # 利益あり
                and position.highest_r > 0  # MFEが記録されている
                and decay_ratio >= cfg.edge_decay_profit_decay_min
            ):
                profit_erosion = (
                    (position.highest_r - position.current_r)
                    / position.highest_r
                )
                if (
                    profit_erosion
                    >= cfg.edge_decay_profit_erosion_threshold
                ):
                    return ManagementAction.full_close(
                        reason=(
                            f"エッジ劣化+利益侵食: "
                            f"decay={decay_ratio:.0%},"
                            f" MFE={position.highest_r:.2f}R"
                            f"→{position.current_r:.2f}R"
                            f" (侵食{profit_erosion:.0%})"
                        ),
                        exit_reason=ExitReason.EDGE_DECAY,
                        trigger_price=current_price,
                    )

        # モードA: 純粋エッジ劣化exit
        if cfg.edge_decay_exit_enabled:
            if (
                decay_ratio >= cfg.edge_decay_exit_threshold
                and position.current_r
                >= cfg.edge_decay_exit_max_loss_r
            ):
                return ManagementAction.full_close(
                    reason=(
                        f"エッジ劣化: "
                        f"entry={position.entry_own_score:.1f}"
                        f"→{own_score:.1f}"
                        f" (劣化{decay_ratio:.0%})"
                    ),
                    exit_reason=ExitReason.EDGE_DECAY,
                    trigger_price=current_price,
                )

        return None

    def _check_profit_reversal(
        self,
        position: ManagedPosition,
        current_price: float,
    ) -> ManagementAction | None:
        """利益反転ガード

        MFEが一定以上に到達した後、利益が急速に低下した場合に
        SL到達前に早期退出し損失を最小限に抑える。

        SL_HITトレードの60%は一度5pips以上の利益が乗っていた。
        この機能により利益→損失の転換を検出し保護する。
        """
        cfg = self.config
        # MFEが閾値に達していない場合はスキップ
        if position.highest_r < cfg.profit_reversal_mfe_r:
            return None

        # highest_rからの下落幅を計算
        r_drop = position.highest_r - position.current_r
        if (
            r_drop >= cfg.profit_reversal_drop_r
            and position.current_r <= cfg.profit_reversal_max_r
        ):
            return ManagementAction.full_close(
                reason=(
                    f"利益反転ガード: MFE={position.highest_r:.2f}R"
                    f"→現在{position.current_r:.2f}R"
                    f" (下落{r_drop:.2f}R)"
                ),
                exit_reason=ExitReason.STAGNATION,
                trigger_price=current_price,
            )
        return None

    def _check_early_profit_guard(
        self,
        position: ManagedPosition,
        current_price: float,
        current_time: datetime,
        buy_score: float,
        sell_score: float,
    ) -> ManagementAction | None:
        """早期利益ガード

        小利益帯でセンチメント悪化（逆方向スコア優勢）を
        検出し、利益があるうちに早期撤退する。

        profit_reversalはMFE>=0.3R後の急落を捕捉するが、
        この機能はMFE 0.05-0.30Rの小利益帯を保護する。
        """
        # スコアが両方0（ライブ初期化等）は無視
        if buy_score == 0.0 and sell_score == 0.0:
            return None

        cfg = self.config
        # 1. 一度は有利方向に動いた証拠
        if position.highest_r < cfg.early_profit_guard_min_mfe_r:
            return None

        # 2. 現在まだ含み益
        if position.current_r <= cfg.early_profit_guard_min_r:
            return None

        # 3. 大利益はprofit_reversalに委任
        if position.current_r > cfg.early_profit_guard_max_r:
            return None

        # 4. エントリーノイズ除外
        elapsed = (
            (current_time - position.entry_time).total_seconds()
            / 60
        )
        if elapsed < cfg.early_profit_guard_min_hold_minutes:
            return None

        # 5. スコア方向の判定
        if position.direction == SignalType.BUY:
            own_score = buy_score
            opp_score = sell_score
        else:
            own_score = sell_score
            opp_score = buy_score

        # 6. 逆方向に実質的な勢い
        if opp_score < cfg.early_profit_guard_min_opp_score:
            return None

        # 7. スコア差で逆転検知
        score_diff = opp_score - own_score
        if score_diff < cfg.early_profit_guard_score_diff:
            return None

        return ManagementAction.full_close(
            reason=(
                f"早期利益ガード: R={position.current_r:.2f},"
                f" MFE={position.highest_r:.2f}R,"
                f" opp={opp_score:.1f} own={own_score:.1f}"
                f" (diff={score_diff:.1f})"
            ),
            exit_reason=ExitReason.TAKE_PROFIT_EARLY,
            trigger_price=current_price,
        )

    def _check_pre_event_exit(
        self,
        position: ManagedPosition,
        current_price: float,
        fundamental_assessment: object | None,
    ) -> ManagementAction | None:
        """指標前ポジション管理

        HIGHインパクト指標の前に:
        - 含み益 → 全決済（利益確保）
        - 含み損 → SL引き締め（損失限定）
        """
        if fundamental_assessment is None:
            return None
        get_mins = getattr(
            fundamental_assessment,
            "minutes_until_next_high_event",
            None,
        )
        if get_mins is None:
            return None
        mins = get_mins()
        if mins is None:
            return None
        if mins > self.config.pre_event_minutes:
            return None

        current_r = position.current_r
        # 含み益 → 全決済
        if (
            current_r > 0
            and self.config.pre_event_profit_close
        ):
            return ManagementAction.full_close(
                f"指標前利確 ({mins:.0f}分前, "
                f"R={current_r:.2f})",
                exit_reason=(
                    ExitReason.PRE_EVENT_CLOSE
                ),
            )
        # 含み損 → SL引き締め
        if (
            current_r <= 0
            and self.config.pre_event_loss_tighten
        ):
            tighten_r = self.config.pre_event_tighten_r
            r_value = position.r_value
            if position.direction == SignalType.BUY:
                new_sl = (
                    position.entry_price
                    + tighten_r * r_value
                )
                if new_sl > position.current_sl:
                    position.current_sl = new_sl
                    return ManagementAction.update_sl(
                        new_sl,
                        f"指標前SL引き締め"
                        f" ({mins:.0f}分前)",
                    )
            else:
                new_sl = (
                    position.entry_price
                    - tighten_r * r_value
                )
                if new_sl < position.current_sl:
                    position.current_sl = new_sl
                    return ManagementAction.update_sl(
                        new_sl,
                        f"指標前SL引き締め"
                        f" ({mins:.0f}分前)",
                    )
        return None

    def _check_weekend_close(
        self,
        position: ManagedPosition,
        current_price: float,
        current_time: datetime,
    ) -> ManagementAction | None:
        """週末前強制クローズチェック

        金曜日の指定時刻（UTC）以降は全ポジションを強制決済する。
        週末のギャップリスクによる予期しない損失を防ぐため。

        Args:
            position: 管理対象ポジション
            current_price: 現在価格
            current_time: 現在時刻（UTC）

        Returns:
            ManagementAction | None: 強制クローズアクション
        """
        # 金曜日（weekday=4）の指定時刻以降のみ発動
        if current_time.weekday() != 4:
            return None
        cutoff = (
            current_time.hour > self.config.weekend_close_hour
            or (
                current_time.hour == self.config.weekend_close_hour
                and current_time.minute >= self.config.weekend_close_minute
            )
        )
        if not cutoff:
            return None

        return ManagementAction.full_close(
            f"週末前強制クローズ "
            f"({current_time.hour:02d}:{current_time.minute:02d} UTC)",
            exit_reason=ExitReason.WEEKEND_CLOSE,
            trigger_price=current_price,
        )

    def _check_partial_close(
        self,
        position: ManagedPosition,
        current_price: float,
        current_time: datetime,
    ) -> ManagementAction | None:
        """部分利確チェック（2R→1R→早期BE順）"""
        pos_id = position.position_id
        mode = position.plan.mode
        be_allowed = mode in self.config.be_enabled_modes

        # regime判定（UNIVERSALモードはregimeのみで判定）
        is_range = (
            getattr(position.plan, "regime", None) == "RANGE"
        )

        # === 2R（最高優先）===
        if (position.current_r >= 2.0 and
                pos_id not in self._partial_closed_2r):
            self._partial_closed_2r.add(pos_id)

            if position.direction == SignalType.BUY:
                new_sl = (
                    position.entry_price + position.r_value
                )
                _2r_price = (
                    position.entry_price
                    + position.r_value * 2
                )
            else:
                new_sl = (
                    position.entry_price - position.r_value
                )
                _2r_price = (
                    position.entry_price
                    - position.r_value * 2
                )

            position.current_sl = new_sl

            return ManagementAction.partial_close(
                ratio=self.config.partial_close_2r_ratio,
                new_sl=new_sl,
                reason=(
                    f"2R到達: {position.current_r:.2f}R、"
                    f"SLを1Rに移動"
                ),
                exit_reason=ExitReason.TAKE_PROFIT_2R,
                trigger_price=_2r_price,
            )

        # === 1.5R（2Rと1Rの間）===
        if (
            self.config.partial_close_15r_enabled
            and position.current_r >= 1.5
            and pos_id not in self._partial_closed_15r
            and pos_id not in self._partial_closed_2r
        ):
            self._partial_closed_15r.add(pos_id)
            if position.direction == SignalType.BUY:
                _15r_price = (
                    position.entry_price
                    + position.r_value * 1.5
                )
            else:
                _15r_price = (
                    position.entry_price
                    - position.r_value * 1.5
                )
            return ManagementAction.partial_close(
                ratio=self.config.partial_close_15r_ratio,
                new_sl=None,
                reason=(
                    f"1.5R到達: {position.current_r:.2f}R、"
                    f"{self.config.partial_close_15r_ratio:.0%}利確"
                ),
                exit_reason=ExitReason.TAKE_PROFIT_EARLY,
                trigger_price=_15r_price,
            )

        # === 1R（中優先）===
        if (position.current_r >= 1.0 and
                pos_id not in self._partial_closed_1r):
            self._partial_closed_1r.add(pos_id)
            # 早期BE重複防止
            self._early_be_applied.add(pos_id)

            # TP無効化（残りはRunner運用）
            if self.config.disable_tp_after_partial:
                self._tp_disabled.add(pos_id)

            new_sl = None
            if self.config.breakeven_at_1r and be_allowed:
                be_price = self._get_be_price(position)
                new_sl = be_price
                position.current_sl = be_price

            # 1Rレベルの理論価格
            if position.direction == SignalType.BUY:
                _1r_price = (
                    position.entry_price + position.r_value
                )
            else:
                _1r_price = (
                    position.entry_price - position.r_value
                )

            return ManagementAction.partial_close(
                ratio=self.config.partial_close_1r_ratio,
                new_sl=new_sl,
                reason=(
                    f"1R到達: {position.current_r:.2f}R、"
                    f"建値移動"
                ),
                exit_reason=ExitReason.TAKE_PROFIT_1R,
                trigger_price=_1r_price,
            )

        # === RANGE×DAY 0.5R部分利確 ===
        if (
            is_range
            and self.config.range_day_half_r_partial_enabled
            and position.current_r
            >= self.config.range_day_half_r_trigger
            and pos_id not in self._half_r_partial_applied
            and pos_id not in self._partial_closed_1r
        ):
            self._half_r_partial_applied.add(pos_id)
            self._early_be_applied.add(pos_id)
            be_price = self._get_be_price(position)
            position.current_sl = be_price
            trigger_r = (
                self.config.range_day_half_r_trigger
            )
            if position.direction == SignalType.BUY:
                trig_price = (
                    position.entry_price
                    + trigger_r * position.r_value
                )
            else:
                trig_price = (
                    position.entry_price
                    - trigger_r * position.r_value
                )
            return ManagementAction.partial_close(
                ratio=(
                    self.config.range_day_half_r_partial_ratio
                ),
                new_sl=be_price,
                reason=(
                    f"RANGE×DAY {trigger_r}R部分利確:"
                    f" {position.current_r:.2f}R, BE移動"
                ),
                exit_reason=ExitReason.TAKE_PROFIT_EARLY,
                trigger_price=trig_price,
            )

        # === ユニバーサル0.5R部分利確（全レジーム対応）===
        if (
            self.config.universal_half_r_enabled
            and not is_range  # RANGE用は上で処理済み
            and position.current_r
            >= self.config.universal_half_r_trigger
            and pos_id not in self._half_r_partial_applied
            and pos_id not in self._partial_closed_1r
        ):
            self._half_r_partial_applied.add(pos_id)
            self._early_be_applied.add(pos_id)
            be_price = self._get_be_price(position)
            position.current_sl = be_price
            _u_trig = self.config.universal_half_r_trigger
            if position.direction == SignalType.BUY:
                _u_price = (
                    position.entry_price
                    + _u_trig * position.r_value
                )
            else:
                _u_price = (
                    position.entry_price
                    - _u_trig * position.r_value
                )
            return ManagementAction.partial_close(
                ratio=self.config.universal_half_r_ratio,
                new_sl=be_price,
                reason=(
                    f"ユニバーサル{_u_trig}R部分利確:"
                    f" {position.current_r:.2f}R, BE移動"
                ),
                exit_reason=ExitReason.TAKE_PROFIT_EARLY,
                trigger_price=_u_price,
            )

        # === RANGE×DAY 軽い保険（1Rの後、早期BEの前）===
        if (
            is_range
            and self.config.range_day_insurance_enabled
        ):
            elapsed_min = (
                (current_time - position.entry_time)
                .total_seconds() / 60
            )

            # trigger_r到達 + 保険SL適用済み → 部分利確 + BE
            ins_trigger = self.config.insurance_trigger_r
            ins_mfe_block = (
                self.config.insurance_block_high_mfe_r
            )
            ins_min_hold = (
                self.config.insurance_min_holding_minutes
            )
            if (
                position.current_r >= ins_trigger
                and pos_id in self._insurance_sl_applied
                and pos_id
                not in self._insurance_partial_applied
                and pos_id
                not in self._half_r_partial_applied
                and position.highest_r < ins_mfe_block
                and elapsed_min >= ins_min_hold
            ):
                self._insurance_partial_applied.add(pos_id)
                self._early_be_applied.add(pos_id)
                be_price = self._get_be_price(position)
                position.current_sl = be_price
                # trigger_rレベルの理論価格
                if position.direction == SignalType.BUY:
                    _tr_price = (
                        position.entry_price
                        + ins_trigger * position.r_value
                    )
                else:
                    _tr_price = (
                        position.entry_price
                        - ins_trigger * position.r_value
                    )
                return ManagementAction.partial_close(
                    ratio=(
                        self.config
                        .range_day_insurance_partial_ratio
                    ),
                    new_sl=be_price,
                    reason=(
                        f"RANGE×DAY保険{ins_trigger}R:"
                        f" 部分利確+BE, MFE="
                        f"{position.highest_r:.2f}R"
                    ),
                    exit_reason=(
                        ExitReason.TAKE_PROFIT_EARLY
                    ),
                    trigger_price=_tr_price,
                )

            # 0.3R到達 within 30分 → SL引き上げ
            if (
                position.current_r >= 0.3
                and elapsed_min
                <= self.config.range_day_insurance_max_minutes
                and pos_id
                not in self._insurance_sl_applied
            ):
                self._insurance_sl_applied.add(pos_id)
                offset = (
                    abs(
                        self.config
                        .range_day_insurance_sl_offset_r
                    )
                    * position.r_value
                )
                if position.direction == SignalType.BUY:
                    new_sl = (
                        position.entry_price - offset
                    )
                else:
                    new_sl = (
                        position.entry_price + offset
                    )
                position.current_sl = new_sl

                # 同一tick trigger_rも超えている場合
                if (
                    position.current_r >= ins_trigger
                    and position.highest_r < ins_mfe_block
                    and elapsed_min >= ins_min_hold
                    and pos_id
                    not in self._half_r_partial_applied
                ):
                    self._insurance_partial_applied.add(
                        pos_id
                    )
                    self._early_be_applied.add(pos_id)
                    be_price = self._get_be_price(
                        position
                    )
                    position.current_sl = be_price
                    # trigger_rレベルの理論価格
                    if position.direction == SignalType.BUY:
                        _tr_price = (
                            position.entry_price
                            + ins_trigger
                            * position.r_value
                        )
                    else:
                        _tr_price = (
                            position.entry_price
                            - ins_trigger
                            * position.r_value
                        )
                    return ManagementAction.partial_close(
                        ratio=(
                            self.config
                            .range_day_insurance_partial_ratio
                        ),
                        new_sl=be_price,
                        reason=(
                            f"RANGE×DAY保険{ins_trigger}"
                            f"R: 部分利確+BE(即時)"
                        ),
                        exit_reason=(
                            ExitReason.TAKE_PROFIT_EARLY
                        ),
                        trigger_price=_tr_price,
                    )

                return ManagementAction.update_sl(
                    new_sl=new_sl,
                    reason=(
                        f"RANGE×DAY保険0.3R: SL→"
                        f"{self.config.range_day_insurance_sl_offset_r}R"
                    ),
                )

        # === 早期BE（最低優先）===
        # 保険モード適用済み → 早期BEスキップ
        if pos_id in self._insurance_sl_applied:
            return None

        effective_be_r = self.config.early_breakeven_r
        if is_range and self.config.range_day_be_disabled:
            if self.config.range_day_fast_be_enabled:
                elapsed_min = (
                    (current_time - position.entry_time)
                    .total_seconds() / 60
                )
                if (
                    elapsed_min
                    <= self.config.range_day_fast_be_minutes
                ):
                    # 速い到達→通常の0.5R BEを適用
                    effective_be_r = (
                        self.config.early_breakeven_r
                    )
                else:
                    # ゆっくり到達→1Rまで待つ
                    effective_be_r = (
                        self.config.range_day_early_be_r
                    )
            else:
                effective_be_r = (
                    self.config.range_day_early_be_r
                )

        if (
            be_allowed
            and self.config.early_breakeven_enabled
            and position.current_r >= effective_be_r
            and pos_id not in self._early_be_applied
            and pos_id not in self._partial_closed_1r
        ):
            self._early_be_applied.add(pos_id)
            be_price = self._get_be_price(position)
            position.current_sl = be_price

            # P0-3: 小利確オプション（フラグON時のみ）
            if self.config.early_partial_close_enabled:
                # effective_be_rレベルの理論価格
                if position.direction == SignalType.BUY:
                    _early_price = (
                        position.entry_price
                        + effective_be_r
                        * position.r_value
                    )
                else:
                    _early_price = (
                        position.entry_price
                        - effective_be_r
                        * position.r_value
                    )
                return ManagementAction.partial_close(
                    ratio=self.config.early_partial_close_ratio,
                    new_sl=be_price,
                    reason=(
                        f"早期小利確: "
                        f"{position.current_r:.2f}R"
                        f">={effective_be_r}R"
                    ),
                    exit_reason=ExitReason.TAKE_PROFIT_EARLY,
                    trigger_price=_early_price,
                )

            return ManagementAction.update_sl(
                new_sl=be_price,
                reason=(
                    f"早期BE移動: "
                    f"{position.current_r:.2f}R"
                    f">={effective_be_r}R"
                ),
            )

        return None

    def _check_trailing(
        self,
        position: ManagedPosition,
        current_price: float,
        atr: float,
        fundamental_assessment: object | None = None,
    ) -> ManagementAction | None:
        """トレーリングチェック"""
        if position.current_r < self.config.trailing_start_r:
            return None

        # ATRベースのトレーリング距離（atr=0時は無効化）
        if atr <= 0:
            return None
        # レジーム別ATR倍率の解決
        regime = getattr(position.plan, "regime", None)
        _base_mult = self.config.trailing_atr_multiplier
        _s2_mult = self.config.trailing_stage2_atr_multiplier
        if regime in ("TREND", "BREAKOUT"):
            if self.config.trailing_trend_atr_multiplier is not None:
                _base_mult = (
                    self.config.trailing_trend_atr_multiplier
                )
            if (
                self.config.trailing_trend_stage2_atr_multiplier
                is not None
            ):
                _s2_mult = (
                    self.config.trailing_trend_stage2_atr_multiplier
                )

        # 3段階トレーリング: highest_rが高いほど引き締め
        if (
            self.config.trailing_stage3_enabled
            and position.highest_r
            >= self.config.trailing_stage3_r
        ):
            # Stage3: 最も引き締め（MFEピーク付近で刈り取り）
            # レジーム別倍率がStage2に適用されている場合、同じ比率でStage3も調整
            _s3_mult = self.config.trailing_stage3_atr_multiplier
            if _s2_mult != self.config.trailing_stage2_atr_multiplier:
                _s3_mult *= (
                    _s2_mult
                    / self.config.trailing_stage2_atr_multiplier
                )
            trail_distance = atr * _s3_mult
        elif (
            self.config.trailing_stage2_enabled
            and position.highest_r
            >= self.config.trailing_stage2_r
        ):
            trail_distance = atr * _s2_mult
        else:
            trail_distance = atr * _base_mult

        # Phase 2b: ファンダメンタル評価でSL距離調整
        # 低収束時はSLを引き締め（乗数<1.0）
        if (
            fundamental_assessment is not None
            and hasattr(
                fundamental_assessment, "trailing_sl_multiplier"
            )
        ):
            trail_distance *= (
                fundamental_assessment.trailing_sl_multiplier
            )

        if position.direction == SignalType.BUY:
            new_sl = position.highest_price - trail_distance
            # SLが現在価格以上（MT5拒否の原因）は無効
            if new_sl >= current_price:
                return None
            if new_sl > position.current_sl:
                position.current_sl = new_sl
                position.trailing_activated = True
                return ManagementAction.update_sl(
                    new_sl,
                    f"トレーリング: {new_sl:.5f}"
                    f"（最高{position.highest_price:.5f}）",
                )
        else:
            new_sl = position.lowest_price + trail_distance
            # SLが現在価格以下（MT5拒否の原因）は無効
            if new_sl <= current_price:
                return None
            if new_sl < position.current_sl:
                position.current_sl = new_sl
                position.trailing_activated = True
                return ManagementAction.update_sl(
                    new_sl,
                    f"トレーリング: {new_sl:.5f}"
                    f"（最安{position.lowest_price:.5f}）",
                )

        return None

    def get_position(self, position_id: str) -> ManagedPosition | None:
        """ポジションを取得

        Args:
            position_id: ポジションID

        Returns:
            ManagedPosition | None: ポジション（未登録の場合None）
        """
        return self._positions.get(position_id)

    def get_current_sl(self, position_id: str) -> float | None:
        """現在のSL価格を取得

        Args:
            position_id: ポジションID

        Returns:
            float | None: SL価格
        """
        position = self._positions.get(position_id)
        return position.current_sl if position else None

    def reset(self) -> None:
        """状態をリセット"""
        self._positions.clear()
        self._partial_closed_1r.clear()
        self._partial_closed_2r.clear()
        self._early_be_applied.clear()
        self._tp_disabled.clear()
        self._insurance_sl_applied.clear()
        self._insurance_partial_applied.clear()
        self._half_r_partial_applied.clear()
