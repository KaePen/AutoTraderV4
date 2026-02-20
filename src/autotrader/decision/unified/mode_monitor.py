"""モード別監視クラス

SCALPING/DAY_TRADE/SWINGの各モードを独立して監視し、
それぞれの時間足セットでシグナルを評価する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from autotrader.core.enums import SignalType, TradingStrategyMode
from autotrader.decision.unified.timeframe_evaluator import (
    TimeframeEvaluator,
    TimeframeSignal,
)

if TYPE_CHECKING:
    from autotrader.core.entities import Candle


@dataclass(frozen=True)
class ModeConfig:
    """モード別設定

    Attributes:
        mode: トレーディングモード
        primary_tf: プライマリ時間足
        entry_tf: エントリー判断時間足
        confirm_tfs: 確認用時間足リスト
        max_hold_minutes: 最大保有時間（分）
        sl_range: SL範囲（min, max）pips
        tp_range: TP範囲（min, max）pips
        min_confidence: 最小確度
    """

    mode: TradingStrategyMode
    primary_tf: str
    entry_tf: str
    confirm_tfs: list[str]
    max_hold_minutes: int
    sl_range: tuple[float, float]
    tp_range: tuple[float, float]
    min_confidence: float = 0.5


# 各モードのデフォルト設定
SCALPING_CONFIG = ModeConfig(
    mode=TradingStrategyMode.SCALPING,
    primary_tf="M5",
    entry_tf="M1",
    confirm_tfs=["M15"],
    max_hold_minutes=90,
    sl_range=(10.0, 20.0),
    tp_range=(10.0, 30.0),
    min_confidence=0.6,
)

DAY_TRADE_CONFIG = ModeConfig(
    mode=TradingStrategyMode.DAY_TRADE,
    primary_tf="M15",
    entry_tf="M5",
    confirm_tfs=["H1", "H4"],
    max_hold_minutes=480,  # 8時間
    sl_range=(20.0, 40.0),
    tp_range=(40.0, 100.0),
    min_confidence=0.55,
)

SWING_CONFIG = ModeConfig(
    mode=TradingStrategyMode.SWING,
    primary_tf="H4",
    entry_tf="H1",
    confirm_tfs=["D1"],
    max_hold_minutes=2880,  # 2日
    sl_range=(50.0, 100.0),
    tp_range=(100.0, 400.0),
    min_confidence=0.5,
)


@dataclass
class ModeSignal:
    """モード別シグナル

    Attributes:
        mode: トレーディングモード
        direction: シグナル方向
        confidence: 確度
        sl_pips: 損切りpips
        tp_pips: 利確pips
        max_hold_bars: 最大保有バー数
        rationale: 判断理由
        primary_signal: プライマリTFシグナル
        confirm_signals: 確認TFシグナルリスト
    """

    mode: TradingStrategyMode
    direction: SignalType
    confidence: float
    sl_pips: float
    tp_pips: float
    max_hold_bars: int
    rationale: str
    primary_signal: TimeframeSignal | None = None
    confirm_signals: list[TimeframeSignal] = field(default_factory=list)


class ModeMonitor:
    """モード別監視クラス

    特定のトレードモードに対応する時間足セットを監視し、
    エントリーシグナルを生成する。
    """

    def __init__(
        self,
        config: ModeConfig,
        market_data: dict[str, pd.DataFrame] | None = None,
    ):
        """初期化

        Args:
            config: モード設定
            market_data: 時間足別マーケットデータ
        """
        self._config = config
        self._market_data = market_data or {}

        # 各時間足の評価器を作成
        self._evaluators: dict[str, TimeframeEvaluator] = {}
        self._init_evaluators()

    def _init_evaluators(self) -> None:
        """評価器を初期化"""
        # 必要な全時間足
        all_tfs = {self._config.primary_tf, self._config.entry_tf}
        all_tfs.update(self._config.confirm_tfs)

        for tf in all_tfs:
            self._evaluators[tf] = TimeframeEvaluator(timeframe=tf)

    def set_market_data(self, market_data: dict[str, pd.DataFrame]) -> None:
        """マーケットデータを設定

        Args:
            market_data: 時間足別マーケットデータ
        """
        self._market_data = market_data

        # 上位TFデータを各評価器に設定
        for tf, evaluator in self._evaluators.items():
            htf_data = {
                k: v for k, v in market_data.items()
                if self._is_higher_tf(k, tf)
            }
            evaluator.set_higher_tf_data(htf_data)

    def _is_higher_tf(self, candidate: str, base: str) -> bool:
        """上位時間足かどうかを判定

        Args:
            candidate: 候補時間足
            base: 基準時間足

        Returns:
            bool: 上位時間足の場合True
        """
        tf_order = ["M1", "M5", "M15", "M30", "H1", "H4", "H8", "D1"]
        try:
            return tf_order.index(candidate) > tf_order.index(base)
        except ValueError:
            return False

    def evaluate(
        self,
        current_time: pd.Timestamp,
        candle: "Candle | None" = None,
    ) -> ModeSignal | None:
        """モード評価

        Args:
            current_time: 現在時刻
            candle: 現在のローソク足

        Returns:
            ModeSignal | None: シグナル（条件未達の場合None）
        """
        # プライマリTFの評価
        primary_signal = self._evaluate_timeframe(
            self._config.primary_tf,
            current_time,
        )
        if primary_signal is None:
            return None

        # プライマリがHOLDならスキップ
        if primary_signal.direction == SignalType.HOLD:
            return None

        # 確認TFの評価
        confirm_signals = []
        aligned_count = 0
        for tf in self._config.confirm_tfs:
            sig = self._evaluate_timeframe(tf, current_time)
            if sig is not None:
                confirm_signals.append(sig)
                if sig.direction == primary_signal.direction:
                    aligned_count += 1

        # 確認TFの過半数が同方向でなければスキップ
        required_confirms = max(1, len(self._config.confirm_tfs) // 2)
        if aligned_count < required_confirms:
            return None

        # 確度チェック
        confidence = self._calculate_confidence(
            primary_signal, confirm_signals
        )
        if confidence < self._config.min_confidence:
            return None

        # SL/TP計算
        sl_pips = self._calculate_sl(primary_signal)
        tp_pips = self._calculate_tp(primary_signal, sl_pips)

        # 最大保有バー数計算
        max_hold_bars = self._calculate_max_hold_bars()

        # 理由生成
        rationale = self._generate_rationale(
            primary_signal, confirm_signals, aligned_count
        )

        return ModeSignal(
            mode=self._config.mode,
            direction=primary_signal.direction,
            confidence=confidence,
            sl_pips=sl_pips,
            tp_pips=tp_pips,
            max_hold_bars=max_hold_bars,
            rationale=rationale,
            primary_signal=primary_signal,
            confirm_signals=confirm_signals,
        )

    def _evaluate_timeframe(
        self,
        timeframe: str,
        current_time: pd.Timestamp,
    ) -> TimeframeSignal | None:
        """特定時間足を評価

        Args:
            timeframe: 時間足
            current_time: 現在時刻

        Returns:
            TimeframeSignal | None: シグナル
        """
        if timeframe not in self._evaluators:
            return None

        if timeframe not in self._market_data:
            return None

        df = self._market_data[timeframe]
        if df.empty:
            return None

        # 現在時刻に対応する行を取得
        row = self._get_current_row(df, current_time)
        if row is None:
            return None

        evaluator = self._evaluators[timeframe]
        return evaluator.evaluate(row)

    def _get_current_row(
        self,
        df: pd.DataFrame,
        current_time: pd.Timestamp,
    ) -> pd.Series | None:
        """現在時刻に対応するデータ行を取得

        Args:
            df: データフレーム
            current_time: 現在時刻

        Returns:
            pd.Series | None: データ行
        """
        if "time" not in df.columns:
            return None

        # 現在時刻以前の最新行を取得
        mask = df["time"] <= current_time
        filtered = df[mask]

        if filtered.empty:
            return None

        return filtered.iloc[-1]

    def _calculate_confidence(
        self,
        primary_signal: TimeframeSignal,
        confirm_signals: list[TimeframeSignal],
    ) -> float:
        """確度を計算

        Args:
            primary_signal: プライマリシグナル
            confirm_signals: 確認シグナルリスト

        Returns:
            float: 確度
        """
        # プライマリの確度をベースに
        base_confidence = primary_signal.confidence

        # 確認TFで同方向のものをボーナス
        for sig in confirm_signals:
            if sig.direction == primary_signal.direction:
                base_confidence += sig.confidence * 0.1

        return min(base_confidence, 1.0)

    def _calculate_sl(self, primary_signal: TimeframeSignal) -> float:
        """SLを計算

        Args:
            primary_signal: プライマリシグナル

        Returns:
            float: SL pips
        """
        sl_min, sl_max = self._config.sl_range

        # シグナルのSLを範囲内に収める
        sl = primary_signal.sl_pips
        return max(sl_min, min(sl, sl_max))

    def _calculate_tp(
        self,
        primary_signal: TimeframeSignal,
        sl_pips: float,
    ) -> float:
        """TPを計算

        Args:
            primary_signal: プライマリシグナル
            sl_pips: SL pips

        Returns:
            float: TP pips
        """
        tp_min, tp_max = self._config.tp_range

        # シグナルのTPを範囲内に収める
        tp = primary_signal.tp_pips
        tp = max(tp_min, min(tp, tp_max))

        # 最低でもSLと同等
        return max(tp, sl_pips)

    def _calculate_max_hold_bars(self) -> int:
        """最大保有バー数を計算

        Returns:
            int: 最大保有バー数
        """
        # プライマリTFのバー数に変換
        tf_minutes = {
            "M1": 1,
            "M5": 5,
            "M15": 15,
            "H1": 60,
            "H4": 240,
            "D1": 1440,
        }

        primary_minutes = tf_minutes.get(self._config.primary_tf, 60)
        return self._config.max_hold_minutes // primary_minutes

    def _generate_rationale(
        self,
        primary_signal: TimeframeSignal,
        confirm_signals: list[TimeframeSignal],
        aligned_count: int,
    ) -> str:
        """判断理由を生成

        Args:
            primary_signal: プライマリシグナル
            confirm_signals: 確認シグナルリスト
            aligned_count: 一致TF数

        Returns:
            str: 判断理由
        """
        mode_name = self._config.mode.value
        direction = primary_signal.direction.value
        primary_reason = primary_signal.reason

        parts = [
            f"[{mode_name}]",
            f"{direction}",
            f"Primary({self._config.primary_tf}):{primary_reason}",
            f"Confirm:{aligned_count}/{len(self._config.confirm_tfs)}TF一致",
        ]

        return " | ".join(parts)

    @property
    def mode(self) -> TradingStrategyMode:
        """モードを取得"""
        return self._config.mode

    @property
    def config(self) -> ModeConfig:
        """設定を取得"""
        return self._config
