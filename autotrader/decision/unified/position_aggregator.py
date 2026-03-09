"""ポジション統合管理

マルチモードトレード時の複数ポジションを統合管理。
グローバルリスク制限とモード間コンフリクト解決を担当。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from autotrader.core.enums import SignalType
from autotrader.decision.unified.mode_monitor import ModeSignal
from autotrader.decision.unified.mode_selector import UNIVERSAL_MODE

if TYPE_CHECKING:
    pass


@dataclass
class ModePosition:
    """モード別ポジション情報

    Attributes:
        mode: トレードモード
        position_id: ポジションID
        direction: 方向
        entry_price: エントリー価格
        entry_time: エントリー時刻
        volume: ボリューム
        sl_pips: 損切りpips
        tp_pips: 利確pips
        max_hold_bars: 最大保有バー数
        current_bars: 現在の保有バー数
        unrealized_pnl: 未実現損益
    """

    mode: str
    position_id: str
    direction: SignalType
    entry_price: float
    entry_time: datetime
    volume: float
    sl_pips: float
    tp_pips: float
    max_hold_bars: int
    current_bars: int = 0
    unrealized_pnl: float = 0.0


@dataclass
class AggregatorConfig:
    """アグリゲーター設定

    Attributes:
        max_total_positions: 合計最大ポジション数
        max_per_mode: モード別最大ポジション数
        max_total_risk_pct: 合計最大リスク（残高比率%）
        max_per_mode_risk_pct: モード別最大リスク（残高比率%）
        allow_opposite_directions: 逆方向ポジションを許可
        force_close_on_max_bars: 最大バー数で強制決済
    """

    max_total_positions: int = 3
    max_per_mode: int = 1
    max_total_risk_pct: float = 6.0  # 6% = 3ポジション × 2%
    max_per_mode_risk_pct: float = 2.0
    allow_opposite_directions: bool = False
    force_close_on_max_bars: bool = True


@dataclass
class AggregatorState:
    """アグリゲーター状態

    Attributes:
        positions: アクティブポジションリスト
        total_risk_pct: 合計リスク（残高比率%）
        mode_risk_pct: モード別リスク
        mode_position_count: モード別ポジション数
        daily_trades: 当日取引数
        daily_pnl: 当日損益
    """

    positions: list[ModePosition] = field(default_factory=list)
    total_risk_pct: float = 0.0
    mode_risk_pct: dict[str, float] = field(
        default_factory=dict
    )
    mode_position_count: dict[str, int] = field(
        default_factory=dict
    )
    daily_trades: int = 0
    daily_pnl: float = 0.0


class PositionAggregator:
    """ポジション統合管理クラス

    マルチモード環境での複数ポジションを統合管理し、
    グローバルリスク制限を適用する。
    """

    def __init__(
        self,
        config: AggregatorConfig | None = None,
        initial_balance: float = 1_000_000.0,
    ):
        """初期化

        Args:
            config: アグリゲーター設定
            initial_balance: 初期残高
        """
        self._config = config or AggregatorConfig()
        self._initial_balance = initial_balance
        self._current_balance = initial_balance

        # 状態
        self._state = AggregatorState()
        self._position_counter = 0

    def can_open_position(
        self,
        signal: ModeSignal,
        volume: float = 1.0,
    ) -> tuple[bool, str]:
        """ポジション開設可能かチェック

        Args:
            signal: モードシグナル
            volume: ボリューム

        Returns:
            tuple[bool, str]: (可能か, 理由)
        """
        mode = signal.mode

        # 合計ポジション数チェック
        total_positions = len(self._state.positions)
        if total_positions >= self._config.max_total_positions:
            return False, f"合計ポジション上限({self._config.max_total_positions})"

        # モード別ポジション数チェック
        mode_count = self._state.mode_position_count.get(mode, 0)
        if mode_count >= self._config.max_per_mode:
            return False, f"{mode}ポジション上限({self._config.max_per_mode})"

        # 逆方向チェック
        if not self._config.allow_opposite_directions:
            for pos in self._state.positions:
                if pos.direction != signal.direction:
                    return False, "逆方向ポジション禁止"

        # リスクチェック
        position_risk_pct = self._calculate_position_risk(
            signal.sl_pips, volume
        )

        # 合計リスクチェック
        if self._state.total_risk_pct + position_risk_pct > self._config.max_total_risk_pct:
            return False, f"合計リスク上限({self._config.max_total_risk_pct}%)"

        # モード別リスクチェック
        mode_risk = self._state.mode_risk_pct.get(mode, 0.0)
        if mode_risk + position_risk_pct > self._config.max_per_mode_risk_pct:
            return False, f"{mode}リスク上限({self._config.max_per_mode_risk_pct}%)"

        return True, "OK"

    def open_position(
        self,
        signal: ModeSignal,
        entry_price: float,
        entry_time: datetime,
        volume: float = 1.0,
    ) -> ModePosition | None:
        """ポジションを開設

        Args:
            signal: モードシグナル
            entry_price: エントリー価格
            entry_time: エントリー時刻
            volume: ボリューム

        Returns:
            ModePosition | None: 開設されたポジション
        """
        can_open, reason = self.can_open_position(signal, volume)
        if not can_open:
            return None

        self._position_counter += 1
        position_id = f"{signal.mode}_{self._position_counter}"

        position = ModePosition(
            mode=signal.mode,
            position_id=position_id,
            direction=signal.direction,
            entry_price=entry_price,
            entry_time=entry_time,
            volume=volume,
            sl_pips=signal.sl_pips,
            tp_pips=signal.tp_pips,
            max_hold_bars=signal.max_hold_bars,
        )

        # 状態更新
        self._state.positions.append(position)
        self._state.mode_position_count[signal.mode] = (
            self._state.mode_position_count.get(signal.mode, 0) + 1
        )

        # リスク更新
        position_risk = self._calculate_position_risk(signal.sl_pips, volume)
        self._state.total_risk_pct += position_risk
        self._state.mode_risk_pct[signal.mode] = (
            self._state.mode_risk_pct.get(signal.mode, 0.0) + position_risk
        )

        return position

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        pnl: float,
    ) -> ModePosition | None:
        """ポジションを決済

        Args:
            position_id: ポジションID
            exit_price: 決済価格
            pnl: 損益

        Returns:
            ModePosition | None: 決済されたポジション
        """
        # ポジション検索
        position = None
        for pos in self._state.positions:
            if pos.position_id == position_id:
                position = pos
                break

        if position is None:
            return None

        # 状態更新
        self._state.positions.remove(position)
        self._state.mode_position_count[position.mode] = max(
            0, self._state.mode_position_count.get(position.mode, 0) - 1
        )

        # リスク更新
        position_risk = self._calculate_position_risk(
            position.sl_pips, position.volume
        )
        self._state.total_risk_pct = max(
            0, self._state.total_risk_pct - position_risk
        )
        self._state.mode_risk_pct[position.mode] = max(
            0, self._state.mode_risk_pct.get(position.mode, 0.0) - position_risk
        )

        # 日次統計更新
        self._state.daily_trades += 1
        self._state.daily_pnl += pnl
        self._current_balance += pnl

        return position

    def update_positions(
        self,
        current_price: float,
        pip_value: float = 100.0,
    ) -> list[str]:
        """ポジションを更新（未実現損益計算、最大保有バー超過チェック）

        Args:
            current_price: 現在価格
            pip_value: pip価値

        Returns:
            list[str]: 強制決済が必要なポジションIDリスト
        """
        force_close_ids = []

        for position in self._state.positions:
            # バー数更新
            position.current_bars += 1

            # 未実現損益計算
            if position.direction == SignalType.BUY:
                pips_diff = (current_price - position.entry_price) * 100
            else:
                pips_diff = (position.entry_price - current_price) * 100

            position.unrealized_pnl = pips_diff * pip_value * position.volume

            # 最大保有バー超過チェック
            if (
                self._config.force_close_on_max_bars
                and position.current_bars >= position.max_hold_bars
            ):
                force_close_ids.append(position.position_id)

        return force_close_ids

    def _calculate_position_risk(
        self,
        sl_pips: float,
        volume: float,
    ) -> float:
        """ポジションリスクを計算（残高比率%）

        Args:
            sl_pips: SL pips
            volume: ボリューム

        Returns:
            float: リスク（%）
        """
        # SL金額 = SL pips × pip価値 × ボリューム
        # pip価値は仮に100円（USDJPY）
        pip_value = 100.0
        sl_amount = sl_pips * pip_value * volume
        return sl_amount / self._current_balance * 100

    def get_positions_by_mode(
        self,
        mode: str,
    ) -> list[ModePosition]:
        """モード別ポジションを取得

        Args:
            mode: トレードモード

        Returns:
            list[ModePosition]: ポジションリスト
        """
        return [p for p in self._state.positions if p.mode == mode]

    def get_all_positions(self) -> list[ModePosition]:
        """全ポジションを取得

        Returns:
            list[ModePosition]: ポジションリスト
        """
        return list(self._state.positions)

    def get_state(self) -> AggregatorState:
        """状態を取得

        Returns:
            AggregatorState: 現在の状態
        """
        return self._state

    def get_summary(self) -> dict:
        """サマリーを取得

        Returns:
            dict: サマリー辞書
        """
        mode_summaries = {}
        mode = UNIVERSAL_MODE
        positions = self.get_positions_by_mode(mode)
        mode_summaries[mode] = {
            "position_count": len(positions),
            "total_volume": sum(p.volume for p in positions),
            "total_unrealized_pnl": sum(
                p.unrealized_pnl for p in positions
            ),
            "risk_pct": self._state.mode_risk_pct.get(mode, 0.0),
        }

        return {
            "total_positions": len(self._state.positions),
            "total_risk_pct": self._state.total_risk_pct,
            "daily_trades": self._state.daily_trades,
            "daily_pnl": self._state.daily_pnl,
            "current_balance": self._current_balance,
            "modes": mode_summaries,
        }

    def reset_daily(self) -> None:
        """日次リセット"""
        self._state.daily_trades = 0
        self._state.daily_pnl = 0.0

    @property
    def config(self) -> AggregatorConfig:
        """設定を取得"""
        return self._config

    @property
    def current_balance(self) -> float:
        """現在残高を取得"""
        return self._current_balance
