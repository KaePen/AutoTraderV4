"""M1実行ゲートフィルタ.

コンセンサス通過後、M1がエントリー方向に味方しているか確認。
Negative screening（極値除外）ではなくPositive screening（好条件確認）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autotrader.core.enums import SignalType


@dataclass(frozen=True)
class M1ExecutionGateConfig:
    """M1実行ゲート設定."""

    enabled: bool = False
    # EMAアラインメント重み
    ema_weight: float = 1.0
    # バーモメンタム重み
    bar_weight: float = 0.5
    # BB健全ゾーン重み
    bb_weight: float = 0.5
    # BB健全ゾーン下限
    bb_low: float = 0.3
    # BB健全ゾーン上限
    bb_high: float = 0.7
    # 通過に必要な最小スコア
    threshold: float = 1.0


@dataclass(frozen=True)
class M1ExecutionGateResult:
    """M1実行ゲート結果."""

    passed: bool
    score: float = 0.0
    reason: str = ""
    ema_aligned: bool = False
    bar_momentum: bool = False
    bb_healthy: bool = False


class M1ExecutionGate:
    """M1実行ゲート.

    M1が方向に味方しているか3条件の加重スコアで判定。
    """

    def __init__(self, config: M1ExecutionGateConfig) -> None:
        self._config = config

    @property
    def config(self) -> M1ExecutionGateConfig:
        """設定を返す."""
        return self._config

    def check(
        self,
        direction: SignalType,
        m1_row: pd.Series | None,
    ) -> M1ExecutionGateResult:
        """M1実行ゲート判定.

        Args:
            direction: エントリー方向
            m1_row: M1現在行データ

        Returns:
            M1ExecutionGateResult: 判定結果
        """
        if not self._config.enabled:
            return M1ExecutionGateResult(
                passed=True, reason="ゲート無効",
            )
        if m1_row is None:
            return M1ExecutionGateResult(
                passed=True, reason="M1データなし",
            )
        if direction == SignalType.HOLD:
            return M1ExecutionGateResult(
                passed=True, reason="HOLD",
            )

        score = 0.0
        ema_ok = self._check_ema(direction, m1_row)
        bar_ok = self._check_bar(direction, m1_row)
        bb_ok = self._check_bb(m1_row)

        if ema_ok:
            score += self._config.ema_weight
        if bar_ok:
            score += self._config.bar_weight
        if bb_ok:
            score += self._config.bb_weight

        passed = score >= self._config.threshold

        if not passed:
            _dir = (
                "BUY"
                if direction == SignalType.BUY
                else "SELL"
            )
            reason = (
                f"M1実行ゲート不通過({_dir}): "
                f"score={score:.1f}"
                f"<{self._config.threshold:.1f} "
                f"[EMA={'○' if ema_ok else '×'}, "
                f"Bar={'○' if bar_ok else '×'}, "
                f"BB={'○' if bb_ok else '×'}]"
            )
        else:
            reason = ""

        return M1ExecutionGateResult(
            passed=passed,
            score=score,
            reason=reason,
            ema_aligned=ema_ok,
            bar_momentum=bar_ok,
            bb_healthy=bb_ok,
        )

    def _check_ema(
        self,
        direction: SignalType,
        row: pd.Series,
    ) -> bool:
        """EMAアラインメントチェック.

        BUY: close > ema_26 AND ema_12 > ema_26
        SELL: close < ema_26 AND ema_12 < ema_26
        """
        close = row.get("close")
        ema_12 = row.get("ema_12")
        ema_26 = row.get("ema_26")
        if (
            close is None
            or ema_12 is None
            or ema_26 is None
        ):
            return False
        if (
            pd.isna(close)
            or pd.isna(ema_12)
            or pd.isna(ema_26)
        ):
            return False

        if direction == SignalType.BUY:
            return (
                float(close) > float(ema_26)
                and float(ema_12) > float(ema_26)
            )
        return (
            float(close) < float(ema_26)
            and float(ema_12) < float(ema_26)
        )

    def _check_bar(
        self,
        direction: SignalType,
        row: pd.Series,
    ) -> bool:
        """バーモメンタムチェック.

        BUY: 陽線（close > open）
        SELL: 陰線（close < open）
        """
        close = row.get("close")
        open_ = row.get("open")
        if close is None or open_ is None:
            return False
        if pd.isna(close) or pd.isna(open_):
            return False

        if direction == SignalType.BUY:
            return float(close) > float(open_)
        return float(close) < float(open_)

    def _check_bb(self, row: pd.Series) -> bool:
        """BB健全ゾーンチェック.

        sma_20とbb_widthからpercent_bを算出し、
        0.3-0.7の範囲内なら健全。
        """
        close = row.get("close")
        sma_20 = row.get("sma_20")
        bb_width = row.get("bb_width")
        if (
            close is None
            or sma_20 is None
            or bb_width is None
        ):
            return False
        if (
            pd.isna(close)
            or pd.isna(sma_20)
            or pd.isna(bb_width)
        ):
            return False
        _close = float(close)
        _sma = float(sma_20)
        _width = float(bb_width)
        if _width <= 0:
            return False
        # bb_percent_b = (close - lower) / width
        # lower = sma_20 - width / 2
        pct_b = (_close - _sma + _width / 2) / _width
        return (
            self._config.bb_low
            <= pct_b
            <= self._config.bb_high
        )
