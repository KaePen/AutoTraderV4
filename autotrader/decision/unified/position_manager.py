"""ポジション管理モジュール

ExitManager + PartialCloseManagerの統合。保有中の戦術を一元管理する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from autotrader.core.enums import ExitReason, SignalType, TradingStrategyMode
from autotrader.decision.unified.mode_selector import TradingPlan


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

    @property
    def r_value(self) -> float:
        """1R値（SL距離）を取得"""
        return abs(self.entry_price - self.original_sl)

    def update_price(self, current_price: float) -> None:
        """価格更新時に最高/最安価格を更新

        Args:
            current_price: 現在価格
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
            self.lowest_price = min(self.lowest_price, current_price)
            self.current_r = (
                (self.entry_price - current_price) / self.r_value
                if self.r_value > 0 else 0.0
            )

        self.highest_r = max(self.highest_r, self.current_r)


@dataclass(frozen=True)
class PositionManagerConfig:
    """ポジション管理設定

    Attributes:
        partial_close_1r_ratio: 1R到達時の部分決済比率
        partial_close_2r_ratio: 2R到達時の部分決済比率
        breakeven_at_1r: 1Rで建値移動するか
        trailing_start_r: トレーリング開始R値
        trailing_atr_multiplier: ATRトレーリング倍率
        time_exit_enabled: 時間決済を有効にするか
        spread_pips: スプレッド（pips）
        slippage_pips: スリッページ（pips）
        be_enabled_modes: BE移動を有効にするモード
        early_breakeven_r: 早期BE移動のR閾値
        early_breakeven_enabled: 早期BE移動を有効にするか
        disable_tp_after_partial: 1R部分利確後にTPを無効化するか
        be_cushion_pips: BE移動時の利益方向クッション(pips)
    """

    partial_close_1r_ratio: float = 0.05
    partial_close_2r_ratio: float = 0.05
    breakeven_at_1r: bool = True
    trailing_start_r: float = 0.5
    trailing_atr_multiplier: float = 2.5
    time_exit_enabled: bool = True
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    be_enabled_modes: tuple[TradingStrategyMode, ...] = (
        TradingStrategyMode.UNIVERSAL,
    )
    early_breakeven_r: float = 0.3
    early_breakeven_enabled: bool = True
    disable_tp_after_partial: bool = True
    signal_rev_close_ratio: float = 0.0
    stagnation_exit_minutes: float = 120.0
    stagnation_min_mfe_r: float = 0.10
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
    range_day_insurance_enabled: bool = True
    range_day_insurance_max_minutes: float = 30.0
    range_day_insurance_sl_offset_r: float = -0.1
    range_day_insurance_partial_ratio: float = 0.20
    # TP_EARLY厳格化
    insurance_trigger_r: float = 1.0
    insurance_block_high_mfe_r: float = 0.8
    insurance_min_holding_minutes: float = 15.0
    # RANGE×DAY 0.5R部分確定
    range_day_half_r_partial_enabled: bool = True
    range_day_half_r_partial_ratio: float = 0.20
    range_day_half_r_trigger: float = 0.5
    # BE移動クッション: BE価格に余裕を持たせノイズによるBE_HIT削減
    be_cushion_pips: float = 3.0


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
        )

    def unregister_position(self, position_id: str) -> None:
        """ポジションを登録解除

        Args:
            position_id: ポジションID
        """
        self._positions.pop(position_id, None)
        self._partial_closed_1r.discard(position_id)
        self._partial_closed_2r.discard(position_id)
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
            "partial_closed_1r": (
                position_id in self._partial_closed_1r
            ),
            "partial_closed_2r": (
                position_id in self._partial_closed_2r
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

        # フラグの復元（対称的: True→add, False→discard）
        _flag_map = {
            "partial_closed_1r": self._partial_closed_1r,
            "partial_closed_2r": self._partial_closed_2r,
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
    ) -> ManagementAction:
        """ポジションを評価

        Args:
            position_id: ポジションID
            current_price: 現在価格
            current_time: 現在時刻
            atr: ATR値
            current_signal: 現在のシグナル（反転チェック用）

        Returns:
            ManagementAction: 管理アクション
        """
        position = self._positions.get(position_id)
        if position is None:
            return ManagementAction.hold("ポジション未登録")

        # 価格更新
        position.update_price(current_price)
        position.bars_held += 1

        # 1. SL到達チェック
        action = self._check_sl(position, current_price)
        if action is not None:
            return action

        # 2. 部分利確チェック（1R, 2R）※TP前に実行
        action = self._check_partial_close(
            position, current_price, current_time,
        )
        if action is not None:
            return action

        # 3. TP到達チェック（1R後は無効化可能）
        action = self._check_tp(position, current_price)
        if action is not None:
            return action

        # 4. 進捗なしExitチェック
        action = self._check_stagnation_exit(
            position, current_time, current_price,
        )
        if action is not None:
            return action

        # 5. 時間決済チェック
        if self.config.time_exit_enabled:
            action = self._check_time_exit(
                position, current_time, current_price,
            )
            if action is not None:
                return action

        # 6. シグナル反転チェック
        if current_signal is not None:
            action = self._check_signal_reversal(
                position, current_signal, current_price,
            )
            if action is not None:
                return action

        # 7. トレーリング更新
        action = self._check_trailing(position, current_price, atr)
        if action is not None:
            return action

        return ManagementAction.hold("条件未達")

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
        slip_offset = self.config.slippage_pips * 2 * 0.01
        # クッション: BE_HIT頻度低減のため利益方向に余裕
        cushion = self.config.be_cushion_pips * 0.01
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

    def _check_stagnation_exit(
        self,
        position: ManagedPosition,
        current_time: datetime,
        current_price: float,
    ) -> ManagementAction | None:
        """進捗なしExit

        一定時間経過後、MFE(highest_r)が閾値未満なら撤退。
        """
        is_range = (
            getattr(position.plan, "regime", None) == "RANGE"
        )
        elapsed = (
            (current_time - position.entry_time).total_seconds()
            / 60
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

        # UNIVERSALモード: stagnation設定を使用
        stag_minutes = self.config.stagnation_exit_minutes
        stag_mfe = self.config.stagnation_min_mfe_r

        if (
            elapsed >= stag_minutes
            and position.highest_r < stag_mfe
        ):
            return ManagementAction.full_close(
                f"進捗なし: {elapsed:.0f}分経過,"
                f" MFE={position.highest_r:.2f}R",
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
        max_minutes = get_holding_minutes(entry_tf)
        elapsed = (current_time - position.entry_time).total_seconds() / 60

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

        effective_be_r = self.config.early_breakeven_r  # 0.5
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
    ) -> ManagementAction | None:
        """トレーリングチェック"""
        if position.current_r < self.config.trailing_start_r:
            return None

        # ATRベースのトレーリング距離（atr=0時は無効化）
        if atr <= 0:
            return None
        trail_distance = atr * self.config.trailing_atr_multiplier

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
