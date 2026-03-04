"""M1マイクロ反転フィルタ

コンセンサス通過後にM1の「行き過ぎ」を検知してエントリーを抑制する。
3指標の合議制で判定: BB %B, Stochastic K, ROC/ATR比。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autotrader.core.entities import SignalType


@dataclass(frozen=True)
class MicroReversalConfig:
    """M1マイクロ反転フィルタ設定

    Attributes:
        enabled: 有効フラグ
        bb_extreme: BB %B極値閾値（BUYで>この値、SELLで<1-この値）
        stoch_extreme: Stochastic K極値閾値（BUYで>この値、SELLで<100-この値）
        roc_atr_extreme: ROC/ATR比の極値閾値
        roc_lookback: ROC計算に使うM1足本数
        min_signals: 発動に必要な最小シグナル数（2=2/3合議）
    """

    enabled: bool = False
    bb_extreme: float = 0.90
    stoch_extreme: float = 80.0
    roc_atr_extreme: float = 1.5
    roc_lookback: int = 5
    min_signals: int = 2


@dataclass(frozen=True)
class MicroReversalResult:
    """M1マイクロ反転フィルタ結果

    Attributes:
        should_filter: エントリー抑制すべきか
        reason: 抑制理由
        bb_triggered: BB %Bが極値にあるか
        stoch_triggered: Stochastic Kが極値にあるか
        roc_triggered: ROC/ATR比が極値にあるか
        signal_count: 発動シグナル数
    """

    should_filter: bool
    reason: str = ""
    bb_triggered: bool = False
    stoch_triggered: bool = False
    roc_triggered: bool = False
    signal_count: int = 0


class MicroReversalFilter:
    """M1マイクロ反転フィルタ

    コンセンサス通過後・direction確定後に、M1足の
    「行き過ぎ」を3指標で合議判定する。

    3指標:
    1. BB %B: BUY→ >bb_extreme / SELL→ <(1-bb_extreme)
    2. Stochastic K: BUY→ >stoch_extreme / SELL→ <(100-stoch_extreme)
    3. ROC/ATR: 直近N本M1足の価格変化がATRのroc_atr_extreme倍超
    """

    def __init__(self, config: MicroReversalConfig) -> None:
        """初期化

        Args:
            config: フィルタ設定
        """
        self._config = config

    @property
    def config(self) -> MicroReversalConfig:
        """設定を取得"""
        return self._config

    def check(
        self,
        direction: SignalType,
        m1_row: pd.Series | None,
        m1_df: pd.DataFrame | None = None,
        m1_index: int | None = None,
    ) -> MicroReversalResult:
        """M1マイクロ反転チェック

        Args:
            direction: エントリー方向（BUY/SELL）
            m1_row: 現在のM1データ行（bb_percent_b, stoch_k, atr_14を含む）
            m1_df: M1のDataFrame全体（ROC計算用）
            m1_index: m1_df中の現在行インデックス位置

        Returns:
            MicroReversalResult: フィルタ結果
        """
        if not self._config.enabled:
            return MicroReversalResult(should_filter=False)

        if direction == SignalType.HOLD:
            return MicroReversalResult(should_filter=False)

        if m1_row is None:
            return MicroReversalResult(should_filter=False)

        is_buy = direction == SignalType.BUY

        # 1. BB %B チェック
        bb_triggered = self._check_bb(m1_row, is_buy)

        # 2. Stochastic K チェック
        stoch_triggered = self._check_stoch(m1_row, is_buy)

        # 3. ROC/ATR チェック
        roc_triggered = self._check_roc_atr(
            m1_row,
            m1_df,
            m1_index,
            is_buy,
        )

        signal_count = sum(
            [
                bb_triggered,
                stoch_triggered,
                roc_triggered,
            ]
        )
        should_filter = signal_count >= self._config.min_signals

        reason = ""
        if should_filter:
            triggers: list[str] = []
            if bb_triggered:
                bb_val = self._get_float(m1_row, "bb_percent_b")
                triggers.append(f"BB%B={bb_val:.2f}")
            if stoch_triggered:
                stoch_val = self._get_float(
                    m1_row,
                    "stoch_k",
                )
                triggers.append(f"StochK={stoch_val:.1f}")
            if roc_triggered:
                triggers.append("ROC/ATR超過")
            dir_str = "BUY" if is_buy else "SELL"
            reason = (
                f"M1マイクロ反転({dir_str}): "
                f"{signal_count}/3 [{', '.join(triggers)}]"
            )

        return MicroReversalResult(
            should_filter=should_filter,
            reason=reason,
            bb_triggered=bb_triggered,
            stoch_triggered=stoch_triggered,
            roc_triggered=roc_triggered,
            signal_count=signal_count,
        )

    def _check_bb(
        self,
        row: pd.Series,
        is_buy: bool,
    ) -> bool:
        """BB %B極値チェック

        Args:
            row: M1データ行
            is_buy: BUY方向か

        Returns:
            bool: 極値に達しているか
        """
        bb_val = self._get_float(row, "bb_percent_b")
        if bb_val is None:
            return False

        if is_buy:
            # BUY時: 上限付近（>0.90）→ 高掴みリスク
            return bb_val > self._config.bb_extreme
        else:
            # SELL時: 下限付近（<0.10）→ 底掴みリスク
            return bb_val < (1.0 - self._config.bb_extreme)

    def _check_stoch(
        self,
        row: pd.Series,
        is_buy: bool,
    ) -> bool:
        """Stochastic K極値チェック

        Args:
            row: M1データ行
            is_buy: BUY方向か

        Returns:
            bool: 極値に達しているか
        """
        stoch_val = self._get_float(row, "stoch_k")
        if stoch_val is None:
            return False

        if is_buy:
            # BUY時: 買われすぎ（>80）
            return stoch_val > self._config.stoch_extreme
        else:
            # SELL時: 売られすぎ（<20）
            return stoch_val < (100.0 - self._config.stoch_extreme)

    def _check_roc_atr(
        self,
        row: pd.Series,
        m1_df: pd.DataFrame | None,
        m1_index: int | None,
        is_buy: bool,
    ) -> bool:
        """ROC/ATR比チェック（インライン計算）

        直近N本M1足の価格変化がATRの閾値倍を超えるか判定。

        Args:
            row: 現在のM1データ行
            m1_df: M1のDataFrame全体
            m1_index: 現在行のインデックス位置
            is_buy: BUY方向か

        Returns:
            bool: ROC/ATR比が極値を超えているか
        """
        atr_val = self._get_float(row, "atr_14")
        if atr_val is None or atr_val <= 0:
            return False

        if m1_df is None or m1_index is None:
            return False

        lookback = self._config.roc_lookback
        start_idx = m1_index - lookback
        if start_idx < 0:
            return False

        # 直近N本の価格変化（close基準）
        try:
            current_close = float(
                m1_df.iloc[m1_index]["close"],
            )
            past_close = float(
                m1_df.iloc[start_idx]["close"],
            )
        except (KeyError, IndexError, TypeError):
            return False

        if past_close == 0:
            return False

        price_change = current_close - past_close
        roc_atr_ratio = abs(price_change) / atr_val

        if roc_atr_ratio <= self._config.roc_atr_extreme:
            return False

        # 方向一致チェック: BUY時は上昇が過剰、SELL時は下落が過剰
        if is_buy and price_change > 0:
            return True
        return bool(not is_buy and price_change < 0)

    @staticmethod
    def _get_float(
        row: pd.Series,
        col: str,
    ) -> float | None:
        """データ行からfloat値を安全に取得

        Args:
            row: データ行
            col: カラム名

        Returns:
            float | None: 値
        """
        val = row.get(col)
        if val is None or pd.isna(val):
            return None
        return float(val)
