"""ドメインエンティティ定義

Pydanticベースのイミュータブルなエンティティ。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from autotrader.core.enums import (
    ConfidenceLevel,
    ExitReason,
    SignalType,
    Timeframe,
)
from autotrader.core.exceptions import InvalidTransitionError


class PositionState(str, Enum):
    """ポジション状態"""

    PENDING = "pending"
    OPEN = "open"
    TRAILING = "trailing"
    PARTIAL_CLOSED = "partial_closed"
    CLOSED = "closed"


# 有効な状態遷移マップ
VALID_TRANSITIONS: dict[PositionState, set[PositionState]] = {
    PositionState.PENDING: {
        PositionState.OPEN,
        PositionState.CLOSED,
    },
    PositionState.OPEN: {
        PositionState.TRAILING,
        PositionState.PARTIAL_CLOSED,
        PositionState.CLOSED,
    },
    PositionState.TRAILING: {
        PositionState.PARTIAL_CLOSED,
        PositionState.CLOSED,
    },
    PositionState.PARTIAL_CLOSED: {
        PositionState.CLOSED,
    },
    PositionState.CLOSED: set(),
}


class Candle(BaseModel):
    """ローソク足データ

    Attributes:
        symbol: シンボル
        timeframe: 時間足
        time: タイムスタンプ（timestampのエイリアス）
        open: 始値
        high: 高値
        low: 安値
        close: 終値
        volume: 出来高
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = "USDJPY"
    timeframe: Timeframe = Timeframe.M15
    time: datetime = Field(default_factory=datetime.now)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def timestamp(self) -> datetime:
        """timestampのエイリアス（後方互換性）"""
        return self.time

    @property
    def body_size(self) -> float:
        """実体サイズ"""
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        """上ヒゲ"""
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        """下ヒゲ"""
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        """陽線かどうか"""
        return self.close > self.open


class Signal(BaseModel):
    """トレードシグナル

    Attributes:
        signal_id: シグナルID
        symbol: 通貨ペア
        timeframe: 時間足
        signal_type: シグナル種別
        confidence: 確度（0-1）
        stop_loss: 損切価格
        take_profit: 利確価格
        reasoning: 判断理由
        created_at: シグナル生成時刻
        indicators_snapshot: 指標スナップショット
    """

    model_config = ConfigDict(frozen=True)

    signal_id: str = ""
    symbol: str = "USDJPY"
    timeframe: Timeframe = Timeframe.M15
    signal_type: SignalType
    confidence: float = Field(ge=0.0, le=1.0)
    stop_loss: float | None = None
    take_profit: float | None = None
    reasoning: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    indicators_snapshot: dict[str, Any] = Field(default_factory=dict)
    # ログ強化: レジーム/モード/コンセンサススコア
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    # ポジションサイジング結果
    lot: float | None = None

    @property
    def timestamp(self) -> datetime:
        """timestampのエイリアス（後方互換性）"""
        return self.created_at

    @property
    def target_price(self) -> float | None:
        """target_priceのエイリアス（後方互換性）"""
        return self.take_profit

    @property
    def stop_loss_price(self) -> float | None:
        """stop_loss_priceのエイリアス（後方互換性）"""
        return self.stop_loss

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """確度レベルを取得"""
        return ConfidenceLevel.from_confidence(self.confidence)


class Position(BaseModel):
    """ポジション情報

    Attributes:
        position_id: ポジションID（バックテスト用）
        ticket: チケットID（MT5用）
        symbol: 通貨ペア
        signal_type: 方向（BUY/SELL）
        volume: ロット数
        entry_price: エントリー価格
        stop_loss: 損切価格
        take_profit: 利確価格
        opened_at: オープン時刻
        signal_id: シグナルID
        unrealized_pnl: 未実現損益
    """

    model_config = ConfigDict(frozen=True)

    position_id: str = ""
    ticket: int = 0
    symbol: str = "USDJPY"
    signal_type: SignalType
    volume: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: datetime = Field(default_factory=datetime.now)
    signal_id: str | None = None
    # ログ強化: レジーム/モード/コンセンサススコア
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    unrealized_pnl: float = 0.0
    state: PositionState = PositionState.OPEN


class PositionStateMachine:
    """ポジション状態遷移の管理

    Attributes:
        _state: 現在の状態
    """

    def __init__(
        self,
        initial_state: PositionState = PositionState.PENDING,
    ) -> None:
        self._state = initial_state

    @property
    def state(self) -> PositionState:
        """現在の状態を取得"""
        return self._state

    def transition(self, new_state: PositionState) -> None:
        """状態遷移を実行

        Args:
            new_state: 遷移先の状態

        Raises:
            InvalidTransitionError: 不正な遷移の場合
        """
        if new_state not in VALID_TRANSITIONS[self._state]:
            allowed = [
                s.value for s in VALID_TRANSITIONS[self._state]
            ]
            raise InvalidTransitionError(
                f"不正な状態遷移: {self._state.value}"
                f" → {new_state.value}. "
                f"許可: {allowed}"
            )
        self._state = new_state

    def can_transition(
        self, new_state: PositionState,
    ) -> bool:
        """遷移可能か判定

        Args:
            new_state: 遷移先の状態

        Returns:
            bool: 遷移可能ならTrue
        """
        return new_state in VALID_TRANSITIONS[self._state]

    @property
    def is_terminal(self) -> bool:
        """終端状態か"""
        return self._state == PositionState.CLOSED


class Trade(BaseModel):
    """トレード履歴

    Attributes:
        trade_id: トレードID（バックテスト用）
        ticket: チケットID（MT5用）
        symbol: 通貨ペア
        signal_type: 方向
        volume: ロット数
        entry_price: エントリー価格
        exit_price: 決済価格
        stop_loss: 損切価格
        take_profit: 利確価格
        profit_loss: 損益
        profit_loss_pips: 損益（pips）
        exit_reason: 決済理由
        opened_at: オープン時刻
        closed_at: クローズ時刻
        signal_id: シグナルID
    """

    model_config = ConfigDict(frozen=True)

    trade_id: str = ""
    ticket: int = 0
    symbol: str = "USDJPY"
    signal_type: SignalType
    volume: float
    entry_price: float
    exit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    profit_loss: float | None = None
    profit_loss_pips: float | None = None
    exit_reason: ExitReason | None = None
    opened_at: datetime = Field(default_factory=datetime.now)
    closed_at: datetime | None = None
    signal_id: str | None = None
    # ログ強化: レジーム/モード/コンセンサススコア
    regime: str | None = None
    mode: str | None = None
    consensus_score: float | None = None
    # 部分決済の親ポジションID
    parent_trade_id: str | None = None
    # ポジションID（部分決済の合算集計用）
    position_id: str | None = None
    # MFE/MAE（最大含み益/最大含み損、pips単位）
    mfe_pips: float | None = None
    mae_pips: float | None = None
    # エントリー時メトリクス
    entry_spread: float | None = None
    entry_atr: float | None = None
    entry_adx: float | None = None
    entry_bb_width: float | None = None


class AccountInfo(BaseModel):
    """口座情報

    Attributes:
        balance: 残高
        equity: 有効証拠金
        margin: 使用証拠金
        free_margin: 余剰証拠金
        margin_level: 証拠金維持率
        profit: 含み損益
        login: ログインID
        server: サーバー名
        name: 口座名義
        currency: 口座通貨
        leverage: レバレッジ
    """

    model_config = ConfigDict(frozen=True)

    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    profit: float = 0.0
    login: int = 0
    server: str = ""
    name: str = ""
    currency: str = "JPY"
    leverage: int = 0


class SymbolInfo(BaseModel):
    """通貨ペア情報

    Attributes:
        symbol: シンボル名
        point: 1ポイント
        digits: 小数桁数
        spread: 現在スプレッド
        min_lot: 最小ロット
        max_lot: 最大ロット
        lot_step: ロットステップ
        contract_size: 契約サイズ
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    point: float
    digits: int
    spread: int = 0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    contract_size: float = 100000.0
