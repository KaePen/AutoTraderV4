"""アダプティブ調整用の軽量トレード記録"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from autotrader.config.trading_params import get_pip_unit
from autotrader.core.entities import ExitReason, Trade


@dataclass(frozen=True)
class TradeRecord:
    """パラメータ調整に必要な最小限のトレード情報

    Attributes:
        pnl: 損益（金額）
        pnl_pips: 損益（pips）
        exit_reason: 決済理由
        regime: エントリー時レジーム
        consensus_score: エントリー時コンセンサススコア
        mfe_pips: 最大含み益（pips）
        mae_pips: 最大含み損（pips）
        sl_pips: 設定SL（pips）
        holding_minutes: 保有時間（分）
        closed_at: 決済時刻
    """

    pnl: float
    pnl_pips: float
    exit_reason: str
    regime: str
    consensus_score: float
    mfe_pips: float
    mae_pips: float
    sl_pips: float
    holding_minutes: float
    closed_at: datetime

    @classmethod
    def from_trade(cls, trade: Trade) -> TradeRecord:
        """core.entities.Trade から変換

        Args:
            trade: 完了したトレードエンティティ

        Returns:
            TradeRecord: 軽量トレード記録
        """
        # 保有時間（分）
        holding_min = 0.0
        if trade.opened_at and trade.closed_at:
            td = trade.closed_at - trade.opened_at
            holding_min = td.total_seconds() / 60.0

        # SL pips（エントリーとSLの差分）
        sl_pips = 0.0
        _pip_unit = get_pip_unit(trade.symbol or "JPY")
        if trade.stop_loss and trade.entry_price:
            sl_pips = abs(
                trade.entry_price - trade.stop_loss
            ) / _pip_unit

        # exit_reason を文字列化
        exit_str = ""
        if trade.exit_reason is not None:
            exit_str = (
                trade.exit_reason.value
                if isinstance(trade.exit_reason, ExitReason)
                else str(trade.exit_reason)
            )

        return cls(
            pnl=trade.profit_loss or 0.0,
            pnl_pips=trade.profit_loss_pips or 0.0,
            exit_reason=exit_str,
            regime=trade.regime or "",
            consensus_score=trade.consensus_score or 0.0,
            mfe_pips=trade.mfe_pips or 0.0,
            mae_pips=trade.mae_pips or 0.0,
            sl_pips=sl_pips,
            holding_minutes=holding_min,
            closed_at=trade.closed_at or datetime.now(UTC),
        )

    @property
    def is_win(self) -> bool:
        """勝ちトレードか"""
        return self.pnl > 0

    @property
    def is_sl_hit(self) -> bool:
        """SL_HITか"""
        return self.exit_reason == "SL_HIT"

    @property
    def is_stagnation(self) -> bool:
        """STAGNATION exitか"""
        return self.exit_reason == "STAGNATION"

    def to_dict(self) -> dict:
        """JSON serializable dict に変換 (state dump用)

        フィールド追加時に自動追従するため asdict を使用。
        datetime 型のみ post-step で ISO 文字列化。
        """
        data = asdict(self)
        data["closed_at"] = self.closed_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> TradeRecord:
        """dict から TradeRecord を復元 (state inject用)"""
        kwargs = {**data}
        kwargs["closed_at"] = datetime.fromisoformat(data["closed_at"])
        return cls(**kwargs)
