"""部分利確・トレーリングストップ

段階的な部分決済とトレーリングストップを管理。
リスク管理を改善し、利益を最大化する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autotrader.core.entities import Position
    from autotrader.core.enums import SignalType


class PartialCloseStage(Enum):
    """部分決済ステージ"""

    INITIAL = "initial"  # 初期状態
    STAGE_1R = "stage_1r"  # 1R到達（50%決済）
    STAGE_2R = "stage_2r"  # 2R到達（25%決済）
    TRAILING = "trailing"  # 3R以降（トレーリング）


@dataclass(frozen=True)
class PartialCloseConfig:
    """部分決済設定

    Attributes:
        stage_1r_close_ratio: 1R到達時の決済比率（デフォルト0.5）
        stage_2r_close_ratio: 2R到達時の決済比率（デフォルト0.5=残りの50%）
        trailing_step_r: トレーリング幅（R単位、デフォルト0.5R）
        move_sl_to_entry_at_1r: 1R到達時にSLを建値へ移動
        move_sl_to_1r_at_2r: 2R到達時にSLを1Rへ移動
    """

    stage_1r_close_ratio: float = 0.5
    stage_2r_close_ratio: float = 0.5
    trailing_step_r: float = 0.5
    move_sl_to_entry_at_1r: bool = True
    move_sl_to_1r_at_2r: bool = True

    @classmethod
    def default(cls) -> PartialCloseConfig:
        """デフォルト設定を取得"""
        return cls()

    @classmethod
    def aggressive(cls) -> PartialCloseConfig:
        """積極的設定（早めに利確）"""
        return cls(
            stage_1r_close_ratio=0.6,
            stage_2r_close_ratio=0.6,
            trailing_step_r=0.3,
        )

    @classmethod
    def conservative(cls) -> PartialCloseConfig:
        """保守的設定（利を伸ばす）"""
        return cls(
            stage_1r_close_ratio=0.4,
            stage_2r_close_ratio=0.4,
            trailing_step_r=0.7,
        )


@dataclass
class PositionState:
    """ポジション状態（部分決済追跡用）

    Attributes:
        position_id: ポジションID
        initial_volume: 初期ロット数
        remaining_volume: 残ロット数
        current_stage: 現在のステージ
        entry_price: エントリー価格
        original_sl: 元のSL価格
        original_tp: 元のTP価格
        current_sl: 現在のSL価格
        r_value: 1R値（SL距離）
        highest_r: 到達した最高R
        trailing_sl: トレーリングSL価格
    """

    position_id: str
    initial_volume: float
    remaining_volume: float
    current_stage: PartialCloseStage
    entry_price: float
    original_sl: float
    original_tp: float
    current_sl: float
    r_value: float
    highest_r: float = 0.0
    trailing_sl: float | None = None


@dataclass(frozen=True)
class PartialCloseAction:
    """部分決済アクション

    Attributes:
        position_id: ポジションID
        close_volume: 決済ロット数
        new_sl: 新しいSL価格（None=変更なし）
        reason: 決済理由
        r_at_close: 決済時のR値
    """

    position_id: str
    close_volume: float
    new_sl: float | None
    reason: str
    r_at_close: float


class PartialCloseManager:
    """部分決済マネージャー

    ポジションの段階的な部分決済とトレーリングストップを管理。

    戦略:
    1. 1R到達時: 50%利確、SLを建値へ移動
    2. 2R到達時: 残り25%利確（元の12.5%）、SLを1Rへ移動
    3. 3R以降: 0.5R幅でトレーリング

    Args:
        config: 部分決済設定
    """

    def __init__(
        self, config: PartialCloseConfig | None = None
    ) -> None:
        self.config = config or PartialCloseConfig.default()
        self._position_states: dict[str, PositionState] = {}

    def register_position(
        self,
        position: "Position",
    ) -> None:
        """ポジションを登録

        Args:
            position: ポジション情報
        """
        from autotrader.core.enums import SignalType

        if position.stop_loss is None:
            return

        # R値を計算（SL距離）
        if position.signal_type == SignalType.BUY:
            r_value = position.entry_price - position.stop_loss
        else:
            r_value = position.stop_loss - position.entry_price

        if r_value <= 0:
            return

        state = PositionState(
            position_id=position.position_id,
            initial_volume=position.volume,
            remaining_volume=position.volume,
            current_stage=PartialCloseStage.INITIAL,
            entry_price=position.entry_price,
            original_sl=position.stop_loss,
            original_tp=position.take_profit or 0.0,
            current_sl=position.stop_loss,
            r_value=r_value,
        )

        self._position_states[position.position_id] = state

    def unregister_position(self, position_id: str) -> None:
        """ポジションを登録解除

        Args:
            position_id: ポジションID
        """
        self._position_states.pop(position_id, None)

    def evaluate(
        self,
        position: "Position",
        current_price: float,
    ) -> PartialCloseAction | None:
        """現在価格でポジションを評価

        Args:
            position: ポジション情報
            current_price: 現在価格

        Returns:
            PartialCloseAction | None: 部分決済アクション（不要ならNone）
        """
        from autotrader.core.enums import SignalType

        state = self._position_states.get(position.position_id)
        if state is None:
            return None

        # 現在のR値を計算
        if position.signal_type == SignalType.BUY:
            current_r = (current_price - state.entry_price) / state.r_value
        else:
            current_r = (state.entry_price - current_price) / state.r_value

        # 最高Rを更新
        if current_r > state.highest_r:
            state.highest_r = current_r

        # ステージ別処理
        if state.current_stage == PartialCloseStage.INITIAL:
            return self._check_stage_1r(state, current_r, position.signal_type)
        elif state.current_stage == PartialCloseStage.STAGE_1R:
            return self._check_stage_2r(state, current_r, position.signal_type)
        elif state.current_stage == PartialCloseStage.STAGE_2R:
            return self._check_trailing(state, current_r, position.signal_type)
        elif state.current_stage == PartialCloseStage.TRAILING:
            return self._update_trailing(state, current_r, position.signal_type)

        return None

    def _check_stage_1r(
        self,
        state: PositionState,
        current_r: float,
        signal_type: "SignalType",
    ) -> PartialCloseAction | None:
        """1R到達チェック

        Args:
            state: ポジション状態
            current_r: 現在のR値
            signal_type: シグナル種別

        Returns:
            PartialCloseAction | None: 部分決済アクション
        """
        from autotrader.core.enums import SignalType

        if current_r < 1.0:
            return None

        # 1R到達: 部分決済
        close_volume = state.remaining_volume * self.config.stage_1r_close_ratio
        state.remaining_volume -= close_volume
        state.current_stage = PartialCloseStage.STAGE_1R

        # SLを建値へ移動
        new_sl = None
        if self.config.move_sl_to_entry_at_1r:
            new_sl = state.entry_price
            state.current_sl = new_sl

        return PartialCloseAction(
            position_id=state.position_id,
            close_volume=close_volume,
            new_sl=new_sl,
            reason="1R到達: 50%決済、SL建値移動",
            r_at_close=current_r,
        )

    def _check_stage_2r(
        self,
        state: PositionState,
        current_r: float,
        signal_type: "SignalType",
    ) -> PartialCloseAction | None:
        """2R到達チェック

        Args:
            state: ポジション状態
            current_r: 現在のR値
            signal_type: シグナル種別

        Returns:
            PartialCloseAction | None: 部分決済アクション
        """
        from autotrader.core.enums import SignalType

        if current_r < 2.0:
            return None

        # 2R到達: 追加部分決済
        close_volume = state.remaining_volume * self.config.stage_2r_close_ratio
        state.remaining_volume -= close_volume
        state.current_stage = PartialCloseStage.STAGE_2R

        # SLを1Rへ移動
        new_sl = None
        if self.config.move_sl_to_1r_at_2r:
            if signal_type == SignalType.BUY:
                new_sl = state.entry_price + state.r_value
            else:
                new_sl = state.entry_price - state.r_value
            state.current_sl = new_sl

        return PartialCloseAction(
            position_id=state.position_id,
            close_volume=close_volume,
            new_sl=new_sl,
            reason="2R到達: 25%決済、SLを1Rへ移動",
            r_at_close=current_r,
        )

    def _check_trailing(
        self,
        state: PositionState,
        current_r: float,
        signal_type: "SignalType",
    ) -> PartialCloseAction | None:
        """トレーリング開始チェック

        Args:
            state: ポジション状態
            current_r: 現在のR値
            signal_type: シグナル種別

        Returns:
            PartialCloseAction | None: トレーリング開始アクション
        """
        from autotrader.core.enums import SignalType

        if current_r < 3.0:
            return None

        # 3R到達: トレーリング開始
        state.current_stage = PartialCloseStage.TRAILING

        # トレーリングSLを設定（2.5R地点）
        trailing_r = current_r - self.config.trailing_step_r
        if signal_type == SignalType.BUY:
            state.trailing_sl = state.entry_price + state.r_value * trailing_r
        else:
            state.trailing_sl = state.entry_price - state.r_value * trailing_r

        state.current_sl = state.trailing_sl

        return PartialCloseAction(
            position_id=state.position_id,
            close_volume=0.0,  # 決済なし、SL更新のみ
            new_sl=state.trailing_sl,
            reason=f"3R到達: トレーリング開始（SL={trailing_r:.1f}R）",
            r_at_close=current_r,
        )

    def _update_trailing(
        self,
        state: PositionState,
        current_r: float,
        signal_type: "SignalType",
    ) -> PartialCloseAction | None:
        """トレーリングSL更新

        Args:
            state: ポジション状態
            current_r: 現在のR値
            signal_type: シグナル種別

        Returns:
            PartialCloseAction | None: SL更新アクション
        """
        from autotrader.core.enums import SignalType

        # 新しいトレーリングSL位置を計算
        new_trailing_r = current_r - self.config.trailing_step_r

        # 現在のSL位置をR値で計算
        if signal_type == SignalType.BUY:
            current_sl_r = (
                (state.current_sl - state.entry_price) / state.r_value
            )
        else:
            current_sl_r = (
                (state.entry_price - state.current_sl) / state.r_value
            )

        # SLが上がる（改善する）場合のみ更新
        if new_trailing_r <= current_sl_r:
            return None

        # 新しいSL価格を計算
        if signal_type == SignalType.BUY:
            new_sl = state.entry_price + state.r_value * new_trailing_r
        else:
            new_sl = state.entry_price - state.r_value * new_trailing_r

        state.trailing_sl = new_sl
        state.current_sl = new_sl

        return PartialCloseAction(
            position_id=state.position_id,
            close_volume=0.0,
            new_sl=new_sl,
            reason=f"トレーリング更新（SL={new_trailing_r:.1f}R）",
            r_at_close=current_r,
        )

    def get_current_sl(self, position_id: str) -> float | None:
        """現在のSL価格を取得

        Args:
            position_id: ポジションID

        Returns:
            float | None: 現在のSL価格
        """
        state = self._position_states.get(position_id)
        if state is None:
            return None
        return state.current_sl

    def get_remaining_volume(self, position_id: str) -> float | None:
        """残ロット数を取得

        Args:
            position_id: ポジションID

        Returns:
            float | None: 残ロット数
        """
        state = self._position_states.get(position_id)
        if state is None:
            return None
        return state.remaining_volume

    def get_position_state(self, position_id: str) -> PositionState | None:
        """ポジション状態を取得

        Args:
            position_id: ポジションID

        Returns:
            PositionState | None: ポジション状態
        """
        return self._position_states.get(position_id)

    def reset(self) -> None:
        """状態をリセット"""
        self._position_states.clear()
