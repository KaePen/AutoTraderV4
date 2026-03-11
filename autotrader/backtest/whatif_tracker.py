"""What-Ifトレード追跡モジュール

ブロックされたシグナルを仮想ポジションとして追跡し、
SL/TP判定でトレード結果をシミュレーションする。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# What-If CSV カラム定義
WHATIF_CSV_COLUMNS = [
    "signal_time",
    "symbol",
    "direction",
    "entry_price",
    "exit_time",
    "exit_price",
    "sl_pips",
    "tp_pips",
    "exit_reason",
    "pips",
    "mfe_pips",
    "mae_pips",
    "holding_minutes",
    "consensus_score",
    "block_reason",
    "regime",
    "mode",
]


@dataclass
class WhatIfPosition:
    """仮想ポジション

    Attributes:
        signal_time: シグナル発生時刻
        symbol: 通貨ペア
        direction: 方向（BUY/SELL）
        entry_price: エントリー価格（シグナル時close）
        sl_price: SL価格
        tp_price: TP価格
        sl_pips: SL幅（pips）
        tp_pips: TP幅（pips）
        consensus_score: コンセンサススコア
        block_reason: ブロック理由
        regime: レジーム
        mode: モード
        mfe_pips: 最大順行（pips）
        mae_pips: 最大逆行（pips）
    """

    signal_time: datetime
    symbol: str
    direction: str
    entry_price: float
    sl_price: float
    tp_price: float
    sl_pips: float
    tp_pips: float
    consensus_score: float
    block_reason: str
    regime: str
    mode: str
    mfe_pips: float = 0.0
    mae_pips: float = 0.0


class WhatIfTracker:
    """ブロックシグナルの仮想トレード追跡

    SL/TP単純判定のみで近似。トレーリングストップ等の
    複雑なexit再現は行わない。

    Args:
        pip_unit: 1pipの価格単位（例: USDJPY=0.01）
    """

    def __init__(self, pip_unit: float = 0.01) -> None:
        """初期化"""
        self._open: list[WhatIfPosition] = []
        self._closed: list[dict] = []
        self._pip_unit = pip_unit

    def add_signal(
        self,
        signal_time: datetime,
        symbol: str,
        direction: str,
        entry_price: float,
        sl_pips: float,
        tp_pips: float,
        consensus_score: float,
        block_reason: str,
        regime: str,
        mode: str,
    ) -> None:
        """ブロックシグナルを仮想ポジションとして追加

        Args:
            signal_time: シグナル時刻
            symbol: 通貨ペア
            direction: BUY or SELL
            entry_price: エントリー価格
            sl_pips: SL幅（pips）
            tp_pips: TP幅（pips）
            consensus_score: コンセンサススコア
            block_reason: ブロック理由
            regime: レジーム
            mode: モード
        """
        if sl_pips <= 0 or tp_pips <= 0:
            return

        if direction == "BUY":
            sl_price = entry_price - sl_pips * self._pip_unit
            tp_price = entry_price + tp_pips * self._pip_unit
        else:
            sl_price = entry_price + sl_pips * self._pip_unit
            tp_price = entry_price - tp_pips * self._pip_unit

        pos = WhatIfPosition(
            signal_time=signal_time,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            consensus_score=consensus_score,
            block_reason=block_reason,
            regime=regime,
            mode=mode,
        )
        self._open.append(pos)

    def update(
        self,
        high: float,
        low: float,
        close: float,
        candle_time: datetime,
    ) -> None:
        """毎足でSL/TP判定 + MFE/MAE更新

        TP優先（同一足でSL/TP両方ヒット時）。

        Args:
            high: 足の高値
            low: 足の安値
            close: 足の終値
            candle_time: 足の時刻
        """
        still_open: list[WhatIfPosition] = []
        for pos in self._open:
            # MFE/MAE更新
            if pos.direction == "BUY":
                fav = (high - pos.entry_price) / self._pip_unit
                adv = (pos.entry_price - low) / self._pip_unit
            else:
                fav = (pos.entry_price - low) / self._pip_unit
                adv = (high - pos.entry_price) / self._pip_unit
            pos.mfe_pips = max(pos.mfe_pips, fav)
            pos.mae_pips = max(pos.mae_pips, adv)

            # SL/TP判定
            hit = self._check_exit(pos, high, low)
            if hit is not None:
                self._close_position(
                    pos, hit, candle_time,
                )
            else:
                still_open.append(pos)
        self._open = still_open

    def _check_exit(
        self,
        pos: WhatIfPosition,
        high: float,
        low: float,
    ) -> str | None:
        """SL/TPヒット判定（TP優先）

        Args:
            pos: 仮想ポジション
            high: 足の高値
            low: 足の安値

        Returns:
            exit理由（"TP"/"SL"）またはNone
        """
        if pos.direction == "BUY":
            tp_hit = high >= pos.tp_price
            sl_hit = low <= pos.sl_price
        else:
            tp_hit = low <= pos.tp_price
            sl_hit = high >= pos.sl_price

        # TP優先
        if tp_hit:
            return "TP"
        if sl_hit:
            return "SL"
        return None

    def _close_position(
        self,
        pos: WhatIfPosition,
        exit_reason: str,
        candle_time: datetime,
    ) -> None:
        """仮想ポジションを決済

        Args:
            pos: 仮想ポジション
            exit_reason: exit理由
            candle_time: 決済時刻
        """
        if exit_reason == "TP":
            exit_price = pos.tp_price
        elif exit_reason == "SL":
            exit_price = pos.sl_price
        else:
            exit_price = pos.entry_price  # FORCE_CLOSE等

        if pos.direction == "BUY":
            pips = (
                (exit_price - pos.entry_price) / self._pip_unit
            )
        else:
            pips = (
                (pos.entry_price - exit_price) / self._pip_unit
            )

        holding_min = 0.0
        if candle_time and pos.signal_time:
            td = candle_time - pos.signal_time
            holding_min = td.total_seconds() / 60.0

        self._closed.append({
            "signal_time": pos.signal_time.strftime(
                "%Y-%m-%d %H:%M:%S",
            ),
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_price": f"{pos.entry_price:.5f}",
            "exit_time": candle_time.strftime(
                "%Y-%m-%d %H:%M:%S",
            ),
            "exit_price": f"{exit_price:.5f}",
            "sl_pips": f"{pos.sl_pips:.1f}",
            "tp_pips": f"{pos.tp_pips:.1f}",
            "exit_reason": exit_reason,
            "pips": f"{pips:.1f}",
            "mfe_pips": f"{pos.mfe_pips:.1f}",
            "mae_pips": f"{pos.mae_pips:.1f}",
            "holding_minutes": f"{holding_min:.0f}",
            "consensus_score": f"{pos.consensus_score:.2f}",
            "block_reason": pos.block_reason,
            "regime": pos.regime,
            "mode": pos.mode,
        })

    def force_close_all(
        self,
        close: float,
        candle_time: datetime,
    ) -> None:
        """全仮想ポジション強制決済

        Args:
            close: 終値
            candle_time: 決済時刻
        """
        for pos in self._open:
            # MFE/MAE最終更新
            if pos.direction == "BUY":
                fav = (
                    (close - pos.entry_price) / self._pip_unit
                )
                adv = (
                    (pos.entry_price - close) / self._pip_unit
                )
            else:
                fav = (
                    (pos.entry_price - close) / self._pip_unit
                )
                adv = (
                    (close - pos.entry_price) / self._pip_unit
                )
            pos.mfe_pips = max(pos.mfe_pips, max(fav, 0))
            pos.mae_pips = max(pos.mae_pips, max(adv, 0))

            if pos.direction == "BUY":
                pips = (
                    (close - pos.entry_price) / self._pip_unit
                )
            else:
                pips = (
                    (pos.entry_price - close) / self._pip_unit
                )

            holding_min = 0.0
            if candle_time and pos.signal_time:
                td = candle_time - pos.signal_time
                holding_min = td.total_seconds() / 60.0

            self._closed.append({
                "signal_time": pos.signal_time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                ),
                "symbol": pos.symbol,
                "direction": pos.direction,
                "entry_price": f"{pos.entry_price:.5f}",
                "exit_time": candle_time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                ),
                "exit_price": f"{close:.5f}",
                "sl_pips": f"{pos.sl_pips:.1f}",
                "tp_pips": f"{pos.tp_pips:.1f}",
                "exit_reason": "FORCE_CLOSE",
                "pips": f"{pips:.1f}",
                "mfe_pips": f"{pos.mfe_pips:.1f}",
                "mae_pips": f"{pos.mae_pips:.1f}",
                "holding_minutes": f"{holding_min:.0f}",
                "consensus_score": (
                    f"{pos.consensus_score:.2f}"
                ),
                "block_reason": pos.block_reason,
                "regime": pos.regime,
                "mode": pos.mode,
            })
        self._open.clear()

    def get_closed_rows(self) -> list[dict]:
        """CSV出力用の辞書リストを返す

        Returns:
            決済済み仮想トレードの辞書リスト
        """
        return list(self._closed)

    @property
    def open_count(self) -> int:
        """未決済仮想ポジション数"""
        return len(self._open)
